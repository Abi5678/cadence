"""Mock external systems: pharmacy, lab, payer, vendor, patient SMS, staffing, doctor Slack.

Every executor runs only after an approval is approved (see clinic.decide) and returns a receipt id.
Nothing leaves this machine except doctor escalations, which go to Slack when SLACK_BOT_TOKEN and
the provider's Slack user are configured; otherwise they are recorded locally.
"""
from __future__ import annotations

import os
from datetime import timedelta

from .db import emit, iso, new_id, now, one

LAB_RESULT_MINUTES = float(os.environ.get("CADENCE_LAB_RESULT_MINUTES", "2"))


def _receipt(prefix: str) -> str:
    return new_id(prefix)


def _msg(conn, channel: str, party: str, body: str, ref: str | None = None) -> str:
    mid = new_id("M")
    conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (mid, channel, "out", party, body, ref, iso(now())))
    return mid


def patient_template_message(conn, p: dict) -> str:
    _msg(conn, "sms", p["patient_id"], p["body"], p.get("ref"))
    return _receipt("SMS")


def patient_message(conn, p: dict) -> str:
    _msg(conn, "sms", p["patient_id"], p["body"], p.get("ref"))
    return _receipt("SMS")


def transmit_rx(conn, p: dict) -> str:
    o = one(conn.execute("SELECT * FROM orders WHERE id=?", (p["order_id"],)))
    if not o or o["kind"] != "rx" or not o["signed_by"]:
        raise RuntimeError("only signed prescriptions can be transmitted")
    rid = _receipt("ERX")
    conn.execute("UPDATE orders SET status='transmitted' WHERE id=?", (o["id"],))
    pharmacy = p.get("pharmacy") or "Synthetic Pharmacy (local fixture)"
    cov = p.get("coverage") or {}
    cov_line = ""
    if cov:
        cov_line = (f" Coverage: covered, tier {cov.get('tier')}, estimated copay ${cov.get('estimated_copay')}. "
                    "Eligibility is not a payment guarantee.")
    _doc(conn, o["patient_id"], "rx_copy", f"Prescription sent to pharmacy: {o['detail'][:50]}",
         f"Prescription sent to {pharmacy}. Confirmation {rid}.{cov_line} Medication text unchanged.", o["id"])
    slack_dm(conn, o["provider_id"], f"Prescription {o['id']} transmitted to the patient's pharmacy (receipt {rid}).")
    return rid


def send_lab_order(conn, p: dict) -> str:
    o = one(conn.execute("SELECT * FROM orders WHERE id=?", (p["order_id"],)))
    if not o or o["kind"] != "lab" or not o["signed_by"]:
        raise RuntimeError("only signed lab orders can be sent")
    due = iso(now() + timedelta(minutes=LAB_RESULT_MINUTES))
    conn.execute("UPDATE orders SET status='sent_to_lab', result_due_at=? WHERE id=?", (due, o["id"]))
    rid = _receipt("LAB")
    slack_dm(conn, o["provider_id"], f"Lab order {o['id']} for {o['patient_id']} sent to the lab (receipt {rid}).")
    return rid


def submit_claim(conn, p: dict) -> str:
    conn.execute("UPDATE charges SET status='submitted' WHERE id=?", (p["charge_id"],))
    ch = one(conn.execute("SELECT * FROM charges WHERE id=?", (p["charge_id"],)))
    rid = _receipt("CLM")
    owed = one(conn.execute("SELECT COALESCE(SUM(patient_responsibility),0) AS t FROM charges WHERE appointment_id=?", (ch["appointment_id"],)))["t"]
    _doc(conn, ch["patient_id"], "statement", "Billing statement",
         f"Claim {rid} submitted to your insurer for visit {ch['appointment_id']}. Your responsibility so far: ${owed:.2f}.", ch["appointment_id"])
    return rid


def send_document(conn, p: dict) -> str:
    d = one(conn.execute("SELECT * FROM documents WHERE id=?", (p["document_id"],)))
    if not d or d["status"] == "needs_release":
        raise RuntimeError("document is not released")
    conn.execute("UPDATE documents SET status='sent' WHERE id=?", (d["id"],))
    _msg(conn, "sms", d["patient_id"], f"New document in your patient portal: {d['title']}. (secure link, synthetic)", d["id"])
    return _receipt("DOC")


def _doc(conn, patient_id: str, kind: str, title: str, body: str, ref: str) -> None:
    did = new_id("D")
    conn.execute("INSERT INTO documents (id,patient_id,kind,title,body,status,ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (did, patient_id, kind, title, body, "released", ref, iso(now())))
    emit(conn, None, "document.created", "service", f"{title} for {patient_id}", {"document_id": did})


def slack_dm(conn, provider_id: str, text: str) -> str | None:
    """DM a provider from the Cadence bot. Falls back to a local preview when Slack isn't configured."""
    prov = one(conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,))) or {}
    slack_user = prov.get("slack_user")
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token and slack_user:
        try:
            from slack_sdk import WebClient  # imported lazily so tests run without Slack
            r = WebClient(token=token, timeout=10).chat_postMessage(channel=slack_user, text=text)
            _msg(conn, "slack", provider_id, text, r.get("ts"))
            # Remember the DM channel so slack_sync can read the doctor's replies (no im:write scope needed).
            conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (f"slack_dm_channel:{provider_id}", r.get("channel")))
            return r.get("ts")
        except Exception as e:  # noqa: BLE001 - Slack outage must not block clinic work
            emit(conn, None, "slack.error", "service", f"Slack DM failed: {e}"[:300])
    _msg(conn, "slack-preview", provider_id, text)
    return None


def vendor_order(conn, p: dict) -> str:
    # Demo: delivery is instant so the dashboard shows the restock.
    conn.execute("UPDATE inventory SET on_hand=on_hand+? WHERE sku=?", (int(p["qty"]), p["sku"]))
    return _receipt("PO")


def fill_shift(conn, p: dict) -> str:
    s = one(conn.execute("SELECT * FROM shifts WHERE id=?", (p["shift_id"],)))
    if not s or s["staff_id"]:
        raise RuntimeError("shift already filled")
    conn.execute("UPDATE shifts SET staff_id=?, status='scheduled' WHERE id=?", (p["staff_id"], p["shift_id"]))
    return _receipt("SHIFT")


def escalate_to_doctor(conn, p: dict) -> str:
    text = f":rotating_light: Cadence escalation for patient {p['patient_id']}\n{p['summary']}\n(Routing only. No clinical assessment was made.)"
    ts = slack_dm(conn, p["provider_id"], text)
    return f"SLACK-{ts}" if ts else _receipt("ESC")


def payer_eligibility(conn, patient: dict) -> dict:
    """Mock 270/271 eligibility check."""
    plan = one(conn.execute("SELECT * FROM insurance_plans WHERE id=?", (patient["plan_id"],)))
    if not plan or (patient["member_id"] or "").startswith("EXPIRED"):
        return {"status": "inactive", "payer": plan["payer"] if plan else None, "member_id": patient["member_id"],
                "reason": "Coverage terminated (synthetic)", "copay": None}
    return {"status": "active", "payer": plan["payer"], "plan": plan["name"], "member_id": patient["member_id"],
            "copay": plan["copay"], "deductible": plan["deductible"], "coinsurance": plan["coinsurance"],
            "reference": _receipt("ELIG")}


def submit_medicare_claim(conn, p: dict) -> str:
    """Mock 837P submission to a Medicare Administrative Contractor. Remittance (835) arrives later."""
    import json as _json
    k = one(conn.execute("SELECT * FROM ccm_packets WHERE id=?", (p["packet_id"],)))
    if not k or k["status"] != "attested":
        raise RuntimeError("packet must be coordinator-reviewed and provider-attested")
    r = _json.loads(k["result"])
    cid, rid = new_id("CLM"), _receipt("MAC")
    conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (cid, k["id"], k["patient_id"], "Medicare Part B (mock MAC)", _json.dumps(r["codes"]), r["billed"], "submitted", rid, None, iso(now())))
    conn.execute("UPDATE ccm_packets SET status='submitted' WHERE id=?", (k["id"],))
    slack_dm(conn, k["attested_by"] or "DR-CHEN", f"Medicare claim {cid} submitted for {r['patient']} ({k['month']}), ${r['billed']:.2f}. Acknowledgement {rid}.")
    return rid


EXECUTORS = {
    "patient_template_message": patient_template_message,
    "patient_message": patient_message,
    "transmit_rx": transmit_rx,
    "send_lab_order": send_lab_order,
    "submit_claim": submit_claim,
    "vendor_order": vendor_order,
    "fill_shift": fill_shift,
    "escalate_to_doctor": escalate_to_doctor,
    "send_document": send_document,
    "submit_medicare_claim": submit_medicare_claim,
}
