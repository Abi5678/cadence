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
import os
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import adapters
from .db import emit, iso, new_id, now, one, parse, rows

CLINIC_TZ = ZoneInfo(os.environ.get("CADENCE_TZ", "America/New_York"))
OFFER_MINUTES = 30
CONFIRM_WINDOW_HOURS = 48
REMINDER_HOURS = 3

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


def local(ts: str) -> str:
    """Clinic-local wording for patient messages, e.g. 'Sun Oct 4 at 10:00 AM'."""
    d = parse(ts).astimezone(CLINIC_TZ)
    return d.strftime(f"%a %b {d.day} at {d.strftime('%I').lstrip('0')}:%M %p")


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


PATIENT_CONSENT = {"patient_template_message": "sms", "patient_message": "sms", "send_document": "documents"}


def has_consent(conn, patient_id: str, kind: str) -> bool:
    r = one(conn.execute("SELECT granted FROM consents WHERE patient_id=? AND kind=?", (patient_id, kind)))
    return bool(r and r["granted"])


def set_consent(conn, patient_id: str, kind: str, granted: bool, by: str) -> dict:
    if kind not in ("sms", "documents"):
        raise ClinicError("consent kind must be sms or documents")
    conn.execute("INSERT INTO consents VALUES (?,?,?,?,?) ON CONFLICT(patient_id,kind) DO UPDATE SET granted=excluded.granted, "
                 "recorded_at=excluded.recorded_at, recorded_by=excluded.recorded_by", (patient_id, kind, int(granted), iso(now()), by))
    emit(conn, None, "consent.updated", by, f"{patient_id} {kind} consent {'granted' if granted else 'withdrawn'}")
    return {"patient_id": patient_id, "kind": kind, "granted": granted}


def propose(conn, task_id: str | None, action: str, payload: dict, summary: str, dedupe_key: str | None = None) -> dict:
    if action not in adapters.EXECUTORS:
        raise ClinicError(f"unknown action {action}")
    need = PATIENT_CONSENT.get(action)
    if need and not has_consent(conn, payload["patient_id"], need):
        emit(conn, task_id, "consent.missing", "service", f"{payload['patient_id']} has no {need} consent: '{summary}' not sent")
        raise ClinicError(f"{payload['patient_id']} has not consented to {need}; ask the front desk to contact them another way")
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
    when = local(a["starts_at"])
    body = f"Hi {p['name'].split()[0]}, this is the clinic confirming your visit on {when}. Reply YES to confirm, NO to cancel, or RESCHEDULE."
    try:
        propose(conn, task_id, "patient_template_message", {"patient_id": a["patient_id"], "body": body, "ref": appt_id},
                f"Confirmation request for {appt_id}", dedupe_key=f"confirm-req:{appt_id}")
        state = "requested"
    except ClinicError:
        state = "call_needed"  # no SMS consent: front desk phones the patient
    conn.execute("UPDATE appointments SET confirmation=?, version=version+1 WHERE id=?", (state, appt_id))
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
    if a["status"] in ("cancelled", "no_show"):
        raise ClinicError(f"{appt_id} is already {a['status']}; its slot was reopened then. Nothing to do.")
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


def offer_reschedule_options(conn, appt_id: str, slot_ids: list[str], task_id: str | None = None) -> dict:
    """Hold up to 3 open slots for this patient and text them a numbered menu (fixed wording). Nothing is booked yet."""
    a = _appt(conn, appt_id)
    if a["status"] not in ("booked", "confirmed"):
        raise ClinicError(f"{appt_id} is {a['status']}; only an active visit can be rescheduled")
    slots = []
    for sid in list(dict.fromkeys(slot_ids))[:3]:
        sl = _appt(conn, sid)
        if sl["status"] != "open" or (sl["offered_to"] and sl["offered_to"] != a["patient_id"] and not _offer_expired(sl)):
            raise ClinicError(f"slot {sid} is not available; pick others from find_open_slots")
        slots.append(sl)
    if not slots:
        raise ClinicError("give 1 to 3 open slot ids from find_open_slots")
    exp = iso(now() + timedelta(minutes=OFFER_MINUTES))
    for sl in slots:
        conn.execute("UPDATE appointments SET offered_to=?, offer_expires_at=?, version=version+1 WHERE id=?", (a["patient_id"], exp, sl["id"]))
    p = one(conn.execute("SELECT name FROM patients WHERE id=?", (a["patient_id"],)))
    names = {r["id"]: r["name"] for r in rows(conn.execute("SELECT id, name FROM providers"))}
    menu = " ".join(f"{i}) {local(sl['starts_at'])} with {names.get(sl['provider_id'], sl['provider_id'])}." for i, sl in enumerate(slots, 1))
    body = (f"Hi {p['name'].split()[0]}, here are other times for your visit: {menu} "
            f"Reply {', '.join(str(i) for i in range(1, len(slots) + 1))} to pick one (held for {OFFER_MINUTES} minutes), or NO to keep your current time.")
    propose(conn, task_id, "patient_template_message", {"patient_id": a["patient_id"], "body": body, "ref": appt_id},
            f"Reschedule options for {appt_id}", dedupe_key=f"resched-options:{appt_id}:{','.join(s_['id'] for s_ in slots)}")
    emit(conn, task_id, "reschedule.options", "agent", f"Held {len(slots)} slot(s) for {a['patient_id']} and texted the choices",
         {"appointment_id": appt_id, "slots": [s_["id"] for s_ in slots]})
    return {"appointment_id": appt_id, "options": [{"choice": i, "slot_id": s_["id"], "starts_at": s_["starts_at"]} for i, s_ in enumerate(slots, 1)],
            "expires_at": exp}


def reschedule_appointment(conn, appt_id: str, new_slot_id: str, task_id: str | None = None) -> dict:
    """Move a visit to a slot the PATIENT chose from offer_reschedule_options. Never to a slot they weren't offered."""
    a = _appt(conn, appt_id)
    if a["status"] not in ("booked", "confirmed"):
        raise ClinicError(f"{appt_id} is {a['status']}; it can't be rescheduled again")
    sl = _appt(conn, new_slot_id)
    if sl["offered_to"] != a["patient_id"] or _offer_expired(sl):
        raise ClinicError(f"{new_slot_id} was not offered to {a['patient_id']}: use offer_reschedule_options and book only the slot they pick")
    booked = book_appointment(conn, a["patient_id"], new_slot_id, a["reason"] or "rescheduled", task_id)
    cancel_appointment(conn, appt_id, "rescheduled", task_id)
    conn.execute("UPDATE appointments SET offered_to=NULL, offer_expires_at=NULL WHERE offered_to=? AND status='open'", (a["patient_id"],))
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
        "AND w.patient_id NOT IN (SELECT patient_id FROM slot_declines WHERE slot_id=?) "
        "AND w.patient_id NOT IN (SELECT patient_id FROM appointments WHERE patient_id IS NOT NULL "
        "  AND status IN ('booked','confirmed') AND starts_at <= ?) ORDER BY priority, added_at LIMIT 1",
        (s["provider_id"], s["starts_at"], slot_id, s["starts_at"])))
    if not cand:
        conn.execute("UPDATE appointments SET offered_to=NULL, offer_expires_at=NULL WHERE id=?", (slot_id,))
        if not one(conn.execute("SELECT 1 AS x FROM events WHERE kind='waitlist.empty' AND summary LIKE ?", (f"%{slot_id}",))):
            emit(conn, task_id, "waitlist.empty", "service", f"No waitlist candidate for {slot_id}")  # once per slot
        return {"slot": slot_id, "offered_to": None}
    exp = now() + timedelta(minutes=OFFER_MINUTES)
    conn.execute("UPDATE appointments SET offered_to=?, offer_expires_at=? WHERE id=?", (cand["patient_id"], iso(exp), slot_id))
    conn.execute("UPDATE waitlist SET status='offered' WHERE id=?", (cand["id"],))
    when = local(s["starts_at"])
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
    if body.strip().upper() in ("STOP", "UNSUBSCRIBE"):
        set_consent(conn, patient_id, "sms", False, f"patient:{patient_id}")
        return {"message_id": mid, "sms_consent": "withdrawn"}
    from . import demo_story
    bound = demo_story.bind_patient_reply(conn, patient_id, body)
    emit(conn, None, "patient.reply", patient_id, body[:200],
         {"message_id": mid, "patient_id": patient_id, "reschedule": bound})
    return {"message_id": mid, "reschedule": bound}


def patient_context(conn, patient_id: str) -> dict:
    p = one(conn.execute("SELECT p.id, p.name, p.dob, p.phone, p.plan_id, p.member_id, p.preferred_pharmacy, "
                         "i.payer AS insurance_payer, i.name AS insurance_plan FROM patients p "
                         "LEFT JOIN insurance_plans i ON i.id=p.plan_id WHERE p.id=?", (patient_id,)))
    if not p:
        raise ClinicError(f"unknown patient {patient_id}")
    p["chronic_conditions"] = [c["description"] for c in rows(conn.execute(
        "SELECT description FROM conditions WHERE patient_id=? AND chronic=1", (patient_id,)))]
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
        # One doctor alert per patient per 30 minutes; repeats are recorded but not re-sent.
        recent = one(conn.execute("SELECT id FROM approvals WHERE action='escalate_to_doctor' AND payload LIKE ? AND created_at >= ?",
                                  (f'%"patient_id": "{patient_id}"%', iso(now() - timedelta(minutes=30)))))
        if recent:
            emit(conn, None, "vitals.coalesced", "service", f"{patient_id} {kind}={value}{unit}: added to open alert {recent['id']}")
        else:
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
    from . import demo_story
    result = demo_story.enrich_eligibility(conn, result, appointment_id, patient_id)
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
    out = {"appointment_id": appt_id, "eligibility": elig,
           "consent_on_file": {"sms": has_consent(conn, a["patient_id"], "sms"),
                               "documents": has_consent(conn, a["patient_id"], "documents")}}
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
    prov = one(conn.execute("SELECT name FROM providers WHERE id=?", (a["provider_id"],)))
    doc = create_document(conn, a["patient_id"], "visit_summary", f"Visit summary: {a['reason'] or 'visit'}",
                          f"Administrative visit summary (synthetic). Visit on {local(a['starts_at'])} with {prov['name']}. "
                          f"Reason: {a['reason'] or 'n/a'}. Follow-up check-in scheduled. Billing statement to follow.", appt_id)
    return {"appointment_id": appt_id, "claim_approval": claim["id"], "aftercare_id": ac["id"], "document_id": doc["id"]}


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
    conn.execute("INSERT INTO orders (id,kind,patient_id,provider_id,detail,signed_by,signed_at,status,created_at,source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (oid, kind, patient_id, provider_id, detail, provider_id, ts, "received", ts, source))
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
    payload = {"order_id": order_id}
    if o["kind"] == "rx":
        from . import demo_story
        elig = verify_insurance(conn, o["patient_id"])
        if elig["status"] != "active":
            conn.execute("UPDATE orders SET status='coverage_review' WHERE id=?", (order_id,))
            adapters.slack_dm(conn, o["provider_id"], f"{order_id} not sent: {o['patient_id']}'s insurance is not active "
                                                      f"({elig.get('reason', 'inactive')}). The front desk will collect updated coverage.")
            return {"order_id": order_id, "eligibility": elig, "transmitted": False}
        payload["eligibility"] = {k: elig.get(k) for k in ("payer", "plan", "member_id", "reference")}
        cov = demo_story.apply_rx_coverage(conn, o, task_id)
        if not cov["coverage"]["covered"]:
            return cov
        payload["coverage"] = cov["coverage"]
        payload["pharmacy"] = cov["pharmacy"]
    action = "send_lab_order" if o["kind"] == "lab" else "transmit_rx"
    dest = "lab" if o["kind"] == "lab" else payload.get("pharmacy", "pharmacy")
    ap = propose(conn, task_id, action, payload, f"Send {o['kind']} order {order_id} to {dest}: {o['detail'][:60]}",
                 dedupe_key=f"route:{order_id}")
    conn.execute("UPDATE orders SET status='pending_approval' WHERE id=?", (order_id,))
    if (o["kind"] == "rx" and o.get("signature_ref") and ap["state"] == "prepared"
            and os.environ.get("CADENCE_SEND_PRESCRIBER_SIGNED_RX") == "1"):
        # Clinic policy (opt-in): the prescriber's own verified signature authorizes sending a covered Rx to the
        # patient's pharmacy; no second human approval. Uncovered or uninsured Rx stopped above.
        ap = decide(conn, ap["id"], True, "policy:prescriber-signed")
    return {"order_id": order_id, "approval_id": ap["id"], "state": ap["state"]}


# ---------------------------------------------------------------- documents

def create_document(conn, patient_id: str, kind: str, title: str, body: str, ref: str | None = None, status: str = "released") -> dict:
    did = new_id("D")
    conn.execute("INSERT INTO documents (id,patient_id,kind,title,body,status,ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (did, patient_id, kind, title, body, status, ref, iso(now())))
    emit(conn, None, "document.created", "service", f"{title} for {patient_id} ({status.replace('_', ' ')})", {"document_id": did})
    return one(conn.execute("SELECT * FROM documents WHERE id=?", (did,)))


def list_documents(conn, patient_id: str | None = None) -> list[dict]:
    if patient_id:
        return rows(conn.execute("SELECT * FROM documents WHERE patient_id=? ORDER BY created_at DESC", (patient_id,)))
    return rows(conn.execute("SELECT * FROM documents ORDER BY created_at DESC LIMIT 60"))


def send_document(conn, document_id: str, task_id: str | None = None) -> dict:
    d = one(conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)))
    if not d:
        raise ClinicError(f"unknown document {document_id}")
    if d["status"] == "needs_release":
        raise ClinicError(f"{document_id} must be released by the doctor before it can be shared")
    return propose(conn, task_id, "send_document", {"patient_id": d["patient_id"], "document_id": document_id},
                   f"Send '{d['title']}' to {d['patient_id']}", dedupe_key=f"senddoc:{document_id}")


# ---------------------------------------------------------------- doctor orders via Slack

def provider_by_slack(conn, slack_user: str) -> dict:
    p = one(conn.execute("SELECT * FROM providers WHERE slack_user=? OR id=? OR lower(name)=lower(?)", (slack_user, slack_user, slack_user or "")))
    if p:
        return p
    # The Slack bridge only admits allow-listed doctors, but the agent sees display names, not member ids.
    # With exactly one Slack-linked doctor, drafts default to them. Signing still needs that doctor's own CONFIRM.
    linked = rows(conn.execute("SELECT * FROM providers WHERE slack_user IS NOT NULL"))
    if len(linked) == 1:
        return linked[0]
    raise ClinicError(f"Slack user {slack_user} is not a registered provider")


def _open_doctor_order_task(conn, provider_id: str, patient_id: str) -> dict | None:
    """Active long-running task that tracks Slack drafts → CONFIRM → approval for one patient."""
    key = f"doctor_order:{provider_id}:{patient_id}"
    return one(conn.execute(
        "SELECT * FROM tasks WHERE dedupe_key=? AND status IN ('running','waiting','review') ORDER BY created_at DESC",
        (key,)))


def ensure_doctor_order_task(conn, provider_id: str, patient_id: str, detail: str) -> dict:
    """Create or reuse the open doctor_order task so Slack free-text becomes tracked clinic work."""
    existing = _open_doctor_order_task(conn, provider_id, patient_id)
    if existing:
        return existing
    key = f"doctor_order:{provider_id}:{patient_id}"
    # Prior completed tasks keep the same dedupe_key; clear it so a new open task can be created.
    conn.execute("UPDATE tasks SET dedupe_key=NULL WHERE dedupe_key=? AND status NOT IN ('running','waiting','review')", (key,))
    return create_task(
        conn, "doctor_order", f"Doctor orders for {patient_id}",
        f"Provider {provider_id} ordered care for {patient_id} in Slack (latest: {detail[:160]}). "
        f"Draft each rx/lab with draft_doctor_order (verbatim), ask them to CONFIRM in Slack, then after "
        f"signature the service queues lab/pharmacy approvals. finish_task once every item for this patient "
        f"is signed and queued (or cancelled) and you have posted a one-line status.",
        dedupe_key=key)


def draft_doctor_order(conn, doctor_slack_user: str, patient_id: str, kind: str, detail: str, task_id: str | None = None, notify: bool = True) -> dict:
    """Draft an order from a doctor's Slack message. It is NOT signed: the service asks the doctor to reply
    CONFIRM <id> in Slack and verifies that reply itself (see slack_sync.py)."""
    if kind not in ("lab", "rx"):
        raise ClinicError("kind must be lab or rx")
    prov = provider_by_slack(conn, doctor_slack_user)
    if not one(conn.execute("SELECT id FROM patients WHERE id=?", (patient_id,))):
        raise ClinicError(f"unknown patient {patient_id}; use find_patient")
    if not task_id:
        task_id = ensure_doctor_order_task(conn, prov["id"], patient_id, detail)["id"]
    oid = new_id("O")
    ts = iso(now())
    conn.execute("INSERT INTO orders (id,kind,patient_id,provider_id,detail,status,created_at,source) VALUES (?,?,?,?,?,?,?,?)",
                 (oid, kind, patient_id, prov["id"], detail[:500], "awaiting_signature", ts, "slack"))
    conn.execute("UPDATE tasks SET status='waiting', updated_at=? WHERE id=?", (ts, task_id))
    emit(conn, task_id, "order.drafted", "agent", f"Drafted {kind} order {oid} for {patient_id}: {detail[:80]}",
         {"order_id": oid, "kind": kind, "patient_id": patient_id})
    if notify:
        adapters.slack_dm(conn, prov["id"], f"Drafted {kind} order *{oid}* for {patient_id}: {detail[:300]}\n"
                                            f"Reply `CONFIRM {oid}` to sign it, or `CANCEL {oid}`.")
    row = one(conn.execute("SELECT * FROM orders WHERE id=?", (oid,)))
    row["task_id"] = task_id
    return row


def sign_order(conn, order_id: str, provider_id: str, signature_ref: str) -> dict:
    """Called only by the Slack sync after it verified the doctor's own CONFIRM message."""
    o = one(conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)))
    if not o or o["status"] != "awaiting_signature" or o["provider_id"] != provider_id:
        return o or {}
    conn.execute("UPDATE orders SET signed_by=?, signed_at=?, signature_ref=?, status='received' WHERE id=?",
                 (provider_id, iso(now()), signature_ref, order_id))
    task = _open_doctor_order_task(conn, provider_id, o["patient_id"])
    if not task:
        task = ensure_doctor_order_task(conn, provider_id, o["patient_id"], o["detail"])
    task_id = task["id"]
    emit(conn, task_id, "order.signed", provider_id,
         f"{order_id} signed in Slack (message {signature_ref})",
         {"order_id": order_id, "patient_id": o["patient_id"], "provider_id": provider_id})
    route_order(conn, order_id, task_id)
    return one(conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)))


def cancel_order(conn, order_id: str, provider_id: str) -> None:
    conn.execute("UPDATE orders SET status='cancelled' WHERE id=? AND provider_id=? AND status IN ('awaiting_signature','received')",
                 (order_id, provider_id))
    emit(conn, None, "order.cancelled", provider_id, f"{order_id} cancelled by provider")


def release_document(conn, document_id: str, provider_id: str) -> None:
    d = one(conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)))
    if d and d["status"] == "needs_release":
        conn.execute("UPDATE documents SET status='released', released_by=? WHERE id=?", (provider_id, document_id))
        emit(conn, None, "document.released", provider_id, f"{d['title']} released for {d['patient_id']}")


def lab_results_due(conn) -> list[str]:
    """Mock lab: results arrive a couple of minutes after the order is sent."""
    done = []
    for o in rows(conn.execute("SELECT * FROM orders WHERE status='sent_to_lab' AND result_due_at <= ?", (iso(now()),))):
        conn.execute("UPDATE orders SET status='resulted' WHERE id=?", (o["id"],))
        doc = create_document(conn, o["patient_id"], "lab_result", f"Lab result: {o['detail'][:60]}",
                              f"SYNTHETIC lab result for {o['detail']}. Demo values only, not real data. Reviewed status: pending doctor release.",
                              o["id"], status="needs_release")
        adapters.slack_dm(conn, o["provider_id"], f"Lab result ready for {o['patient_id']} ({o['detail'][:120]}), document *{doc['id']}*.\n"
                                                   f"Reply `RELEASE {doc['id']}` to let the front desk share it with the patient.")
        done.append(o["id"])
    return done


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
    out["reminders"] = []
    for a in rows(conn.execute("SELECT a.*, p.name FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE a.status='confirmed'")):
        if t < parse(a["starts_at"]) <= t + timedelta(hours=REMINDER_HOURS):
            try:
                propose(conn, None, "patient_template_message",
                        {"patient_id": a["patient_id"], "ref": a["id"],
                         "body": f"Reminder: your visit is {local(a['starts_at'])}. Please bring your insurance card. Reply RESCHEDULE if you can't make it."},
                        f"Day-of reminder {a['id']}", dedupe_key=f"reminder:{a['id']}")
                out["reminders"].append(a["id"])
            except ClinicError:
                pass
    for s in rows(conn.execute("SELECT * FROM appointments WHERE status='open' AND starts_at > ?", (iso(t),))):
        if not s["offered_to"] or _offer_expired(s):
            r = offer_slot_to_waitlist(conn, s["id"])
            if r.get("offered_to"):
                out["offers"].append(r)
    for ac in rows(conn.execute("SELECT ac.*, p.name FROM aftercare ac JOIN patients p ON p.id=ac.patient_id WHERE ac.status='scheduled' AND ac.due_at <= ?", (iso(t),))):
        try:
            propose(conn, None, "patient_template_message",
                    {"patient_id": ac["patient_id"], "body": f"Hi {ac['name'].split()[0]}, checking in from the clinic. {ac['question']}", "ref": ac["id"]},
                    f"Aftercare check-in {ac['id']}", dedupe_key=f"aftercare:{ac['id']}")
            conn.execute("UPDATE aftercare SET status='sent' WHERE id=?", (ac["id"],))
        except ClinicError:
            conn.execute("UPDATE aftercare SET status='call_needed' WHERE id=?", (ac["id"],))
        out["aftercare_sent"].append(ac["id"])
    out["lab_results"] = lab_results_due(conn)
    out["low_stock"] = [i["sku"] for i in inventory_status(conn) if i["below_par"]]
    out["open_shifts"] = [s["id"] for s in staffing_overview(conn)["open_shifts"]]
    out["unrouted_orders"] = [o["id"] for o in list_orders(conn, "received")]
    tomorrow = (t + timedelta(days=1)).date()
    checked = {r["appointment_id"] for r in rows(conn.execute("SELECT appointment_id FROM eligibility_checks"))}
    out["insurance_tomorrow"] = [a["id"] for a in list_appointments(conn)
                                 if a["patient_id"] and a["status"] in ("booked", "confirmed")
                                 and parse(a["starts_at"]).date() == tomorrow and a["id"] not in checked]
    return out
