"""Clinic business logic for the nine operational areas.

Pure functions over a sqlite3 connection, so they are testable without an LLM. The agent reaches
them through MCP tools (mcp_tools.py); the always-on scheduler calls the sweep_* functions.

Safety rules enforced here, not in the prompt:
- Prescriptions and lab orders are only transmitted when signed by a provider. The agent cannot
  create or edit orders, only route signed ones.
- Anything that leaves the clinic (pharmacy, lab, payer, vendor, free-text patient messages,
  staffing changes) becomes an approval item that a coordinator must approve.
- Routine templated patient messages (confirmation request, slot offer, aftercare check-in) are
  sent automatically; their wording is fixed by the service, not the model.
"""
from __future__ import annotations

import json
from datetime import timedelta

from . import adapters
from .db import emit, iso, new_id, now, one, parse, rows

OFFER_MINUTES = 30
CONFIRM_WINDOW_HOURS = 48

# Aftercare answers containing any of these are escalated to the doctor. This is a routing rule,
# not a clinical judgment: the doctor decides what it means.
ESCALATE_WORDS = ("yes", "fever", "pain", "bleeding", "redness", "swelling", "worse", "pus", "dizzy", "short of breath")

# Vitals thresholds that trigger a doctor alert (synthetic demo values).
VITAL_LIMITS = {
    "heart_rate": (50, 110), "systolic_bp": (90, 160), "spo2": (92, 101), "temp_c": (35.5, 38.0),
    "glucose": (70, 250),
}


class ClinicError(ValueError):
    pass


# ---------------------------------------------------------------- tasks & events

def create_task(conn, kind: str, title: str, brief: str = "", dedupe_key: str | None = None) -> dict:
    if dedupe_key:
        existing = one(conn.execute("SELECT * FROM tasks WHERE dedupe_key=?", (dedupe_key,)))
        if existing:
            return existing
    tid = new_id("T")
    ts = iso(now())
    conn.execute("INSERT INTO tasks (id,kind,title,brief,status,created_at,updated_at,dedupe_key) VALUES (?,?,?,?,?,?,?,?)",
                 (tid, kind, title, brief, "running", ts, ts, dedupe_key))
    emit(conn, tid, "task.created", "service", title, {"kind": kind})
    return get_task(conn, tid)


def get_task(conn, task_id: str) -> dict:
    t = one(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)))
    if not t:
        raise ClinicError(f"unknown task {task_id}")
    t["events"] = rows(conn.execute("SELECT seq,kind,actor,summary,created_at FROM events WHERE task_id=? ORDER BY seq", (task_id,)))
    t["approvals"] = rows(conn.execute("SELECT id,action,summary,state,receipt FROM approvals WHERE task_id=?", (task_id,)))
    return t


def set_task_status(conn, task_id: str, status: str, note: str = "") -> dict:
    if status not in ("running", "waiting", "review", "completed", "failed", "cancelled"):
        raise ClinicError("bad status")
    conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, iso(now()), task_id))
    emit(conn, task_id, f"task.{status}", "agent", note or status)
    return get_task(conn, task_id)


# ---------------------------------------------------------------- approvals

# Actions that may run without a human click.
AUTO_ACTIONS = {"patient_template_message", "escalate_to_doctor"}


def propose(conn, task_id: str | None, action: str, payload: dict, summary: str, dedupe_key: str | None = None) -> dict:
    if action not in adapters.EXECUTORS:
        raise ClinicError(f"unknown action {action}")
    if dedupe_key:
        existing = one(conn.execute("SELECT * FROM approvals WHERE dedupe_key=?", (dedupe_key,)))
        if existing:
            return existing
    aid = new_id("AP")
    conn.execute("INSERT INTO approvals (id,task_id,action,payload,summary,state,created_at,dedupe_key) VALUES (?,?,?,?,?,?,?,?)",
                 (aid, task_id, action, json.dumps(payload), summary, "prepared", iso(now()), dedupe_key))
    emit(conn, task_id, "approval.prepared", "agent", summary, {"approval_id": aid, "action": action})
    if action in AUTO_ACTIONS:
        return decide(conn, aid, approve=True, by="policy:auto")
    if task_id:
        conn.execute("UPDATE tasks SET status='review', updated_at=? WHERE id=?", (iso(now()), task_id))
    return get_approval(conn, aid)


def get_approval(conn, aid: str) -> dict:
    a = one(conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)))
    if not a:
        raise ClinicError(f"unknown approval {aid}")
    a["payload"] = json.loads(a["payload"])
    return a


def decide(conn, aid: str, approve: bool, by: str) -> dict:
    a = get_approval(conn, aid)
    if a["state"] != "prepared":
        return a  # idempotent: already decided
    ts = iso(now())
    if not approve:
        conn.execute("UPDATE approvals SET state='rejected', decided_by=?, decided_at=? WHERE id=?", (by, ts, aid))
        emit(conn, a["task_id"], "approval.rejected", by, a["summary"], {"approval_id": aid})
        return get_approval(conn, aid)
    conn.execute("UPDATE approvals SET state='attempted', decided_by=?, decided_at=? WHERE id=?", (by, ts, aid))
    emit(conn, a["task_id"], "approval.attempted", by, a["summary"], {"approval_id": aid})
    try:
        receipt = adapters.EXECUTORS[a["action"]](conn, a["payload"])
    except Exception as e:  # noqa: BLE001 - surface any adapter failure as a failed delivery
        conn.execute("UPDATE approvals SET state='failed', receipt=? WHERE id=?", (str(e), aid))
        emit(conn, a["task_id"], "approval.failed", "service", f"{a['summary']}: {e}", {"approval_id": aid})
        return get_approval(conn, aid)
    conn.execute("UPDATE approvals SET state='confirmed', receipt=? WHERE id=?", (receipt, aid))
    emit(conn, a["task_id"], "approval.confirmed", "service", f"{a['summary']} (receipt {receipt})", {"approval_id": aid, "receipt": receipt})
    return get_approval(conn, aid)


# ---------------------------------------------------------------- appointments + waitlist

def list_appointments(conn, day_offset: int | None = None, status: str | None = None) -> list[dict]:
    q = "SELECT a.*, p.name AS patient_name, d.name AS provider_name FROM appointments a LEFT JOIN patients p ON p.id=a.patient_id LEFT JOIN providers d ON d.id=a.provider_id"
    out = rows(conn.execute(q + " ORDER BY starts_at"))
    if status:
        out = [a for a in out if a["status"] == status]
    if day_offset is not None:
        day = (now() + timedelta(days=day_offset)).date()
        out = [a for a in out if parse(a["starts_at"]).date() == day]
    return out


def _appt(conn, appt_id: str) -> dict:
    a = one(conn.execute("SELECT * FROM appointments WHERE id=?", (appt_id,)))
    if not a:
        raise ClinicError(f"unknown appointment {appt_id}")
    return a


def request_confirmation(conn, appt_id: str, task_id: str | None = None) -> dict:
    a = _appt(conn, appt_id)
    if a["status"] != "booked" or a["confirmation"] != "none":
        return a
    p = one(conn.execute("SELECT name FROM patients WHERE id=?", (a["patient_id"],)))
    when = parse(a["starts_at"]).strftime("%a %b %d %H:%M UTC")
    body = f"Hi {p['name'].split()[0]}, this is the clinic confirming your visit on {when}. Reply YES to confirm, NO to cancel, or RESCHEDULE."
    propose(conn, task_id, "patient_template_message", {"patient_id": a["patient_id"], "body": body, "ref": appt_id},
            f"Confirmation request for {appt_id}", dedupe_key=f"confirm-req:{appt_id}")
    conn.execute("UPDATE appointments SET confirmation='requested', version=version+1 WHERE id=?", (appt_id,))
    return _appt(conn, appt_id)


def confirm_appointment(conn, appt_id: str, task_id: str | None = None) -> dict:
    a = _appt(conn, appt_id)
    if a["status"] not in ("booked", "confirmed"):
        raise ClinicError(f"{appt_id} is {a['status']}")
    conn.execute("UPDATE appointments SET status='confirmed', confirmation='confirmed', version=version+1 WHERE id=?", (appt_id,))
    emit(conn, task_id, "appointment.confirmed", "agent", f"{appt_id} confirmed by patient", {"appointment_id": appt_id})
    return _appt(conn, appt_id)


def cancel_appointment(conn, appt_id: str, reason: str = "patient request", task_id: str | None = None) -> dict:
    """Cancel a patient's booking and reopen the slot (which the waitlist sweep then backfills)."""
    a = _appt(conn, appt_id)
    if a["status"] in ("completed", "checked_in"):
        raise ClinicError(f"cannot cancel {a['status']} appointment")
    if a["status"] == "open":
        return a
    slot_id = new_id("A")
    conn.execute("UPDATE appointments SET status='cancelled', confirmation='declined', version=version+1 WHERE id=?", (appt_id,))
    conn.execute("INSERT INTO appointments (id,patient_id,provider_id,starts_at,minutes,reason,status) VALUES (?,?,?,?,?,?,?)",
                 (slot_id, None, a["provider_id"], a["starts_at"], a["minutes"], None, "open"))
    emit(conn, task_id, "appointment.cancelled", "agent", f"{appt_id} cancelled ({reason}); slot {slot_id} reopened",
         {"appointment_id": appt_id, "open_slot": slot_id})
    return {"cancelled": appt_id, "open_slot": slot_id}


def find_open_slots(conn, provider_id: str | None = None, after_hours: int = 0) -> list[dict]:
    cutoff = iso(now() + timedelta(hours=after_hours))
    q = "SELECT * FROM appointments WHERE status='open' AND starts_at > ?"
    args: list = [cutoff]
    if provider_id:
        q += " AND provider_id=?"
        args.append(provider_id)
    return rows(conn.execute(q + " ORDER BY starts_at", args))


def book_appointment(conn, patient_id: str, slot_id: str, reason: str, task_id: str | None = None) -> dict:
    s = _appt(conn, slot_id)
    if s["status"] != "open" or (s["offered_to"] and s["offered_to"] != patient_id and not _offer_expired(s)):
        raise ClinicError(f"slot {slot_id} not available")
    conn.execute("UPDATE appointments SET patient_id=?, reason=?, status='confirmed', confirmation='confirmed', offered_to=NULL, offer_expires_at=NULL, version=version+1 WHERE id=?",
                 (patient_id, reason, slot_id))
    conn.execute("UPDATE waitlist SET status='placed' WHERE patient_id=? AND status IN ('waiting','offered')", (patient_id,))
    emit(conn, task_id, "appointment.booked", "agent", f"{patient_id} booked into {slot_id}", {"appointment_id": slot_id})
    return _appt(conn, slot_id)


def reschedule_appointment(conn, appt_id: str, new_slot_id: str, task_id: str | None = None) -> dict:
    a = _appt(conn, appt_id)
    booked = book_appointment(conn, a["patient_id"], new_slot_id, a["reason"] or "rescheduled", task_id)
    cancel_appointment(conn, appt_id, "rescheduled", task_id)
    return booked


def _offer_expired(slot: dict) -> bool:
    return bool(slot["offer_expires_at"]) and parse(slot["offer_expires_at"]) < now()


def offer_slot_to_waitlist(conn, slot_id: str, task_id: str | None = None) -> dict:
    """Offer an open slot to the best waitlist candidate (priority, then wait time)."""
    s = _appt(conn, slot_id)
    if s["status"] != "open":
        raise ClinicError(f"slot {slot_id} is not open")
    if s["offered_to"] and not _offer_expired(s):
        return {"slot": slot_id, "already_offered_to": s["offered_to"]}
    if s["offered_to"]:  # expired: treat as a decline for this slot, keep their waitlist place
        conn.execute("INSERT OR IGNORE INTO slot_declines VALUES (?,?)", (slot_id, s["offered_to"]))
        conn.execute("UPDATE waitlist SET status='waiting' WHERE patient_id=? AND status='offered'", (s["offered_to"],))
    cand = one(conn.execute(
        "SELECT w.*, p.name FROM waitlist w JOIN patients p ON p.id=w.patient_id WHERE w.status='waiting' "
        "AND (w.provider_id IS NULL OR w.provider_id=?) AND w.patient_id NOT IN "
        "(SELECT patient_id FROM appointments WHERE starts_at=? AND patient_id IS NOT NULL) "
        "AND w.patient_id NOT IN (SELECT patient_id FROM slot_declines WHERE slot_id=?) ORDER BY priority, added_at LIMIT 1",
        (s["provider_id"], s["starts_at"], slot_id)))
    if not cand:
        conn.execute("UPDATE appointments SET offered_to=NULL, offer_expires_at=NULL WHERE id=?", (slot_id,))
        emit(conn, task_id, "waitlist.empty", "service", f"No waitlist candidate for {slot_id}")
        return {"slot": slot_id, "offered_to": None}
    exp = now() + timedelta(minutes=OFFER_MINUTES)
    conn.execute("UPDATE appointments SET offered_to=?, offer_expires_at=? WHERE id=?", (cand["patient_id"], iso(exp), slot_id))
    conn.execute("UPDATE waitlist SET status='offered' WHERE id=?", (cand["id"],))
    when = parse(s["starts_at"]).strftime("%a %b %d %H:%M UTC")
    body = (f"Hi {cand['name'].split()[0]}, an earlier visit opened on {when}. Reply YES within {OFFER_MINUTES} minutes "
            "to take it, or NO to keep your place on the waitlist.")
    propose(conn, task_id, "patient_template_message", {"patient_id": cand["patient_id"], "body": body, "ref": slot_id},
            f"Offer {slot_id} to {cand['patient_id']}", dedupe_key=f"offer:{slot_id}:{cand['patient_id']}:{iso(exp)}")
    return {"slot": slot_id, "offered_to": cand["patient_id"], "expires_at": iso(exp)}


def decline_offer(conn, slot_id: str, patient_id: str, task_id: str | None = None) -> dict:
    s = _appt(conn, slot_id)
    if s["offered_to"] != patient_id:
        raise ClinicError("no active offer for that patient")
    conn.execute("INSERT OR IGNORE INTO slot_declines VALUES (?,?)", (slot_id, patient_id))
    conn.execute("UPDATE appointments SET offered_to=NULL, offer_expires_at=NULL WHERE id=?", (slot_id,))
    conn.execute("UPDATE waitlist SET status='waiting' WHERE patient_id=? AND status='offered'", (patient_id,))
    emit(conn, task_id, "waitlist.declined", "agent", f"{patient_id} declined {slot_id}")
    return offer_slot_to_waitlist(conn, slot_id, task_id)


def add_to_waitlist(conn, patient_id: str, reason: str, provider_id: str | None = None, priority: int = 3) -> dict:
    wid = new_id("W")
    conn.execute("INSERT INTO waitlist (id,patient_id,provider_id,reason,priority,added_at) VALUES (?,?,?,?,?,?)",
                 (wid, patient_id, provider_id, reason, priority, iso(now())))
    emit(conn, None, "waitlist.added", "agent", f"{patient_id} added to waitlist")
    return one(conn.execute("SELECT * FROM waitlist WHERE id=?", (wid,)))


# ---------------------------------------------------------------- patient messaging

def record_patient_reply(conn, patient_id: str, body: str) -> dict:
    """Inbound patient text. Stored as data; the agent interprets it, never as instructions."""
    mid = new_id("M")
    conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (mid, "sms", "in", patient_id, body[:1000], None, iso(now())))
    emit(conn, None, "patient.reply", patient_id, body[:200], {"message_id": mid, "patient_id": patient_id})
    return {"message_id": mid}


def patient_context(conn, patient_id: str) -> dict:
    p = one(conn.execute("SELECT id,name,phone,plan_id FROM patients WHERE id=?", (patient_id,)))
    if not p:
        raise ClinicError(f"unknown patient {patient_id}")
    p["appointments"] = rows(conn.execute("SELECT id,starts_at,status,confirmation,reason FROM appointments WHERE patient_id=? ORDER BY starts_at", (patient_id,)))
    p["open_offers"] = rows(conn.execute("SELECT id,starts_at,offer_expires_at FROM appointments WHERE offered_to=? AND status='open'", (patient_id,)))
    p["aftercare"] = rows(conn.execute("SELECT id,question,status FROM aftercare WHERE patient_id=? AND status IN ('sent','answered')", (patient_id,)))
    p["recent_messages"] = rows(conn.execute("SELECT direction,body,created_at FROM messages WHERE party=? ORDER BY created_at DESC LIMIT 6", (patient_id,)))
    return p


def message_patient(conn, patient_id: str, body: str, task_id: str | None = None) -> dict:
    """Free-text patient message drafted by the agent: always needs coordinator approval."""
    return propose(conn, task_id, "patient_message", {"patient_id": patient_id, "body": body[:600]},
                   f"Message to {patient_id}: {body[:60]}")


# ---------------------------------------------------------------- aftercare + monitoring

def schedule_aftercare(conn, appt_id: str, hours_after: int = 24, question: str | None = None) -> dict:
    a = _appt(conn, appt_id)
    acid = new_id("AC")
    q = question or "How are you feeling after your visit? Any new or worsening symptoms (yes/no)?"
    conn.execute("INSERT INTO aftercare (id,patient_id,appointment_id,due_at,question) VALUES (?,?,?,?,?)",
                 (acid, a["patient_id"], appt_id, iso(parse(a["starts_at"]) + timedelta(hours=hours_after)), q))
    return one(conn.execute("SELECT * FROM aftercare WHERE id=?", (acid,)))


def record_aftercare_answer(conn, aftercare_id: str, answer: str, task_id: str | None = None) -> dict:
    ac = one(conn.execute("SELECT * FROM aftercare WHERE id=?", (aftercare_id,)))
    if not ac:
        raise ClinicError("unknown aftercare check")
    flagged = any(w in answer.lower() for w in ESCALATE_WORDS)
    conn.execute("UPDATE aftercare SET answer=?, status=?, flagged=? WHERE id=?",
                 (answer[:1000], "answered", int(flagged), aftercare_id))
    emit(conn, task_id, "aftercare.answered", ac["patient_id"], f"Aftercare {aftercare_id} answered" + (" (flagged)" if flagged else ""))
    if flagged:
        appt = _appt(conn, ac["appointment_id"])
        escalate_to_doctor(conn, appt["provider_id"], ac["patient_id"],
                           f"Aftercare reply for {ac['patient_id']} matched escalation words. Patient wrote: \"{answer[:300]}\"", task_id)
        conn.execute("UPDATE aftercare SET status='escalated' WHERE id=?", (aftercare_id,))
    else:
        conn.execute("UPDATE aftercare SET status='closed' WHERE id=?", (aftercare_id,))
    return one(conn.execute("SELECT * FROM aftercare WHERE id=?", (aftercare_id,)))


def record_vitals(conn, patient_id: str, kind: str, value: float, unit: str = "", source: str = "device") -> dict:
    vid = new_id("V")
    conn.execute("INSERT INTO vitals VALUES (?,?,?,?,?,?,?)", (vid, patient_id, kind, value, unit, iso(now()), source))
    lo_hi = VITAL_LIMITS.get(kind)
    out_of_range = bool(lo_hi) and not (lo_hi[0] <= value <= lo_hi[1])
    emit(conn, None, "vitals.recorded", source, f"{patient_id} {kind}={value}{unit}" + (" OUT OF RANGE" if out_of_range else ""),
         {"patient_id": patient_id, "out_of_range": out_of_range})
    if out_of_range:
        prov = one(conn.execute("SELECT provider_id FROM appointments WHERE patient_id=? ORDER BY starts_at DESC LIMIT 1", (patient_id,)))
        escalate_to_doctor(conn, (prov or {}).get("provider_id") or "DR-CHEN", patient_id,
                           f"Remote monitoring: {kind}={value}{unit} outside alert range {lo_hi}.", None)
    return {"id": vid, "out_of_range": out_of_range}


def escalate_to_doctor(conn, provider_id: str, patient_id: str, summary: str, task_id: str | None = None) -> dict:
    return propose(conn, task_id, "escalate_to_doctor", {"provider_id": provider_id, "patient_id": patient_id, "summary": summary[:800]},
                   f"Escalate {patient_id} to {provider_id}")


# ---------------------------------------------------------------- check-in, insurance, billing

def verify_insurance(conn, patient_id: str, appointment_id: str | None = None) -> dict:
    p = one(conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)))
    if not p:
        raise ClinicError(f"unknown patient {patient_id}")
    result = adapters.payer_eligibility(conn, p)
    cid = new_id("EL")
    conn.execute("INSERT INTO eligibility_checks VALUES (?,?,?,?,?,?)",
                 (cid, patient_id, appointment_id, result["status"], json.dumps(result), iso(now())))
    emit(conn, None, "insurance.checked", "payer", f"{patient_id}: {result['status']}", result)
    return result


def checkin_patient(conn, appt_id: str, task_id: str | None = None) -> dict:
    a = _appt(conn, appt_id)
    if a["status"] not in ("booked", "confirmed"):
        raise ClinicError(f"{appt_id} is {a['status']}")
    elig = verify_insurance(conn, a["patient_id"], appt_id)
    conn.execute("UPDATE appointments SET status='checked_in', version=version+1 WHERE id=?", (appt_id,))
    emit(conn, task_id, "patient.checked_in", "front_desk", f"{a['patient_id']} checked in for {appt_id}", {"eligibility": elig["status"]})
    out = {"appointment_id": appt_id, "eligibility": elig}
    if elig["status"] == "active":
        out["copay_charge"] = create_charge(conn, appt_id, "COPAY", "Visit copay", elig["copay"], elig["copay"], task_id)
    else:
        out["action_needed"] = "Coverage not active: collect updated insurance or self-pay estimate at the desk."
    return out


def create_charge(conn, appt_id: str, code: str, description: str, amount: float, patient_resp: float | None = None, task_id: str | None = None) -> dict:
    a = _appt(conn, appt_id)
    chid = new_id("CH")
    conn.execute("INSERT INTO charges VALUES (?,?,?,?,?,?,?,?,?)",
                 (chid, appt_id, a["patient_id"], code, description, amount, patient_resp if patient_resp is not None else amount, "draft", iso(now())))
    emit(conn, task_id, "billing.charge", "agent", f"Charge {code} ${amount:.2f} for {appt_id}")
    return one(conn.execute("SELECT * FROM charges WHERE id=?", (chid,)))


def complete_visit(conn, appt_id: str, task_id: str | None = None) -> dict:
    """Mark a visit completed, draft the claim and schedule aftercare."""
    a = _appt(conn, appt_id)
    if a["status"] != "checked_in":
        raise ClinicError("visit must be checked in first")
    conn.execute("UPDATE appointments SET status='completed', version=version+1 WHERE id=?", (appt_id,))
    charge = create_charge(conn, appt_id, "VISIT-EST", "Established patient visit (demo code)", 140.0, 0.0, task_id)
    claim = propose(conn, task_id, "submit_claim", {"charge_id": charge["id"]}, f"Submit claim for {appt_id} ($140.00)",
                    dedupe_key=f"claim:{appt_id}")
    ac = schedule_aftercare(conn, appt_id)
    return {"appointment_id": appt_id, "claim_approval": claim["id"], "aftercare_id": ac["id"]}


def billing_summary(conn, patient_id: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM charges", []
    if patient_id:
        q, args = q + " WHERE patient_id=?", [patient_id]
    return rows(conn.execute(q + " ORDER BY created_at DESC LIMIT 50", args))


# ---------------------------------------------------------------- doctor orders: labs + Rx

def list_orders(conn, status: str | None = None) -> list[dict]:
    if status:
        return rows(conn.execute("SELECT * FROM orders WHERE status=? ORDER BY created_at", (status,)))
    return rows(conn.execute("SELECT * FROM orders ORDER BY created_at"))


def record_signed_order(conn, kind: str, patient_id: str, provider_id: str, detail: str, source: str) -> dict:
    """Called by the service when a provider submits an order (e.g. from a verified Slack user). Not an agent tool."""
    if kind not in ("lab", "rx"):
        raise ClinicError("kind must be lab or rx")
    oid = new_id("O")
    ts = iso(now())
    conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)", (oid, kind, patient_id, provider_id, detail, provider_id, ts, "received", ts, source))
    emit(conn, None, "order.received", provider_id, f"Signed {kind} order {oid} for {patient_id}")
    return one(conn.execute("SELECT * FROM orders WHERE id=?", (oid,)))


def route_order(conn, order_id: str, task_id: str | None = None) -> dict:
    o = one(conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)))
    if not o:
        raise ClinicError(f"unknown order {order_id}")
    if not o["signed_by"]:
        emit(conn, task_id, "order.blocked", "service", f"{order_id} is unsigned; asked provider to sign")
        raise ClinicError(f"{order_id} is not signed by a provider; request signature, do not transmit")
    if o["status"] != "received":
        return {"order_id": order_id, "status": o["status"]}
    action = "send_lab_order" if o["kind"] == "lab" else "transmit_rx"
    dest = "lab" if o["kind"] == "lab" else "pharmacy"
    ap = propose(conn, task_id, action, {"order_id": order_id}, f"Send {o['kind']} order {order_id} to {dest}: {o['detail'][:60]}",
                 dedupe_key=f"route:{order_id}")
    conn.execute("UPDATE orders SET status='pending_approval' WHERE id=?", (order_id,))
    return {"order_id": order_id, "approval_id": ap["id"], "state": ap["state"]}


# ---------------------------------------------------------------- inventory

def inventory_status(conn) -> list[dict]:
    out = rows(conn.execute("SELECT * FROM inventory ORDER BY sku"))
    for i in out:
        i["below_par"] = i["on_hand"] < i["par"]
    return out


def draft_reorder(conn, sku: str, qty: int | None = None, task_id: str | None = None) -> dict:
    i = one(conn.execute("SELECT * FROM inventory WHERE sku=?", (sku,)))
    if not i:
        raise ClinicError(f"unknown sku {sku}")
    q = int(qty or i["reorder_qty"])
    return propose(conn, task_id, "vendor_order", {"sku": sku, "qty": q},
                   f"Order {q} x {i['name']} from {i['vendor']} (${q * i['unit_cost']:.2f})", dedupe_key=f"reorder:{sku}:{q}:{i['on_hand']}")


def use_inventory(conn, sku: str, qty: int) -> dict:
    conn.execute("UPDATE inventory SET on_hand=MAX(on_hand-?,0) WHERE sku=?", (qty, sku))
    return one(conn.execute("SELECT * FROM inventory WHERE sku=?", (sku,)))


# ---------------------------------------------------------------- staffing

def staffing_overview(conn) -> dict:
    shifts = rows(conn.execute("SELECT s.*, st.name AS staff_name FROM shifts s LEFT JOIN staff st ON st.id=s.staff_id ORDER BY starts_at"))
    hours: dict[str, float] = {}
    for s in shifts:
        if s["staff_id"]:
            hours[s["staff_id"]] = hours.get(s["staff_id"], 0) + (parse(s["ends_at"]) - parse(s["starts_at"])).total_seconds() / 3600
    staff = rows(conn.execute("SELECT * FROM staff"))
    for st in staff:
        st["scheduled_hours"] = round(hours.get(st["id"], 0), 1)
    return {"open_shifts": [s for s in shifts if not s["staff_id"]], "staff": staff, "shifts": shifts}


def suggest_shift_fill(conn, shift_id: str) -> list[dict]:
    s = one(conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)))
    if not s:
        raise ClinicError(f"unknown shift {shift_id}")
    length = (parse(s["ends_at"]) - parse(s["starts_at"])).total_seconds() / 3600
    ov = staffing_overview(conn)
    busy = {x["staff_id"] for x in ov["shifts"] if x["staff_id"] and x["starts_at"] < s["ends_at"] and x["ends_at"] > s["starts_at"]}
    return sorted([st for st in ov["staff"] if st["role"] == s["role"] and st["id"] not in busy
                   and st["scheduled_hours"] + length <= st["max_weekly_hours"]], key=lambda st: st["scheduled_hours"])


def propose_shift_fill(conn, shift_id: str, staff_id: str, task_id: str | None = None) -> dict:
    if staff_id not in {c["id"] for c in suggest_shift_fill(conn, shift_id)}:
        raise ClinicError(f"{staff_id} is not eligible for {shift_id} (role, overlap or hour limit)")
    return propose(conn, task_id, "fill_shift", {"shift_id": shift_id, "staff_id": staff_id},
                   f"Assign {staff_id} to open shift {shift_id}", dedupe_key=f"shift:{shift_id}:{staff_id}")


# ---------------------------------------------------------------- always-on sweeps

def sweep(conn) -> dict:
    """Deterministic housekeeping run by the scheduler. Returns work the agent should look at."""
    t = now()
    out: dict = {"confirmations_requested": [], "offers": [], "aftercare_sent": [], "low_stock": [], "open_shifts": [],
                 "unrouted_orders": [], "insurance_tomorrow": []}
    for a in rows(conn.execute("SELECT * FROM appointments WHERE status='booked' AND confirmation='none'")):
        if t < parse(a["starts_at"]) <= t + timedelta(hours=CONFIRM_WINDOW_HOURS):
            request_confirmation(conn, a["id"])
            out["confirmations_requested"].append(a["id"])
    for s in rows(conn.execute("SELECT * FROM appointments WHERE status='open' AND starts_at > ?", (iso(t),))):
        if not s["offered_to"] or _offer_expired(s):
            r = offer_slot_to_waitlist(conn, s["id"])
            if r.get("offered_to"):
                out["offers"].append(r)
    for ac in rows(conn.execute("SELECT ac.*, p.name FROM aftercare ac JOIN patients p ON p.id=ac.patient_id WHERE ac.status='scheduled' AND ac.due_at <= ?", (iso(t),))):
        propose(conn, None, "patient_template_message",
                {"patient_id": ac["patient_id"], "body": f"Hi {ac['name'].split()[0]}, checking in from the clinic. {ac['question']}", "ref": ac["id"]},
                f"Aftercare check-in {ac['id']}", dedupe_key=f"aftercare:{ac['id']}")
        conn.execute("UPDATE aftercare SET status='sent' WHERE id=?", (ac["id"],))
        out["aftercare_sent"].append(ac["id"])
    out["low_stock"] = [i["sku"] for i in inventory_status(conn) if i["below_par"]]
    out["open_shifts"] = [s["id"] for s in staffing_overview(conn)["open_shifts"]]
    out["unrouted_orders"] = [o["id"] for o in list_orders(conn, "received")]
    tomorrow = (t + timedelta(days=1)).date()
    checked = {r["appointment_id"] for r in rows(conn.execute("SELECT appointment_id FROM eligibility_checks"))}
    out["insurance_tomorrow"] = [a["id"] for a in list_appointments(conn)
                                 if a["patient_id"] and a["status"] in ("booked", "confirmed")
                                 and parse(a["starts_at"]).date() == tomorrow and a["id"] not in checked]
    return out
