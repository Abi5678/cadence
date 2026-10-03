"""Mock external systems: pharmacy, lab, payer, vendor, patient SMS, staffing, doctor Slack.

Every executor runs only after an approval is approved (see clinic.decide) and returns a receipt id.
Nothing leaves this machine except doctor escalations, which go to Slack when SLACK_BOT_TOKEN and
the provider's Slack user are configured; otherwise they are recorded locally.
"""
from __future__ import annotations

import os

from .db import iso, new_id, now, one


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
    conn.execute("UPDATE orders SET status='transmitted' WHERE id=?", (o["id"],))
    return _receipt("ERX")


def send_lab_order(conn, p: dict) -> str:
    o = one(conn.execute("SELECT * FROM orders WHERE id=?", (p["order_id"],)))
    if not o or o["kind"] != "lab" or not o["signed_by"]:
        raise RuntimeError("only signed lab orders can be sent")
    conn.execute("UPDATE orders SET status='sent_to_lab' WHERE id=?", (o["id"],))
    return _receipt("LAB")


def submit_claim(conn, p: dict) -> str:
    conn.execute("UPDATE charges SET status='submitted' WHERE id=?", (p["charge_id"],))
    return _receipt("CLM")


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
    prov = one(conn.execute("SELECT * FROM providers WHERE id=?", (p["provider_id"],))) or {}
    text = f":rotating_light: Cadence escalation for patient {p['patient_id']}\n{p['summary']}\n(Routing only. No clinical assessment was made.)"
    slack_user = prov.get("slack_user") or os.environ.get("CADENCE_DOCTOR_SLACK_USER")
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token and slack_user:
        from slack_sdk import WebClient  # imported lazily so tests run without Slack
        r = WebClient(token=token).chat_postMessage(channel=slack_user, text=text)
        _msg(conn, "slack", p["provider_id"], text, r.get("ts"))
        return f"SLACK-{r.get('ts')}"
    _msg(conn, "slack-preview", p["provider_id"], text)
    return _receipt("ESC")


def payer_eligibility(conn, patient: dict) -> dict:
    """Mock 270/271 eligibility check."""
    plan = one(conn.execute("SELECT * FROM insurance_plans WHERE id=?", (patient["plan_id"],)))
    if not plan or (patient["member_id"] or "").startswith("EXPIRED"):
        return {"status": "inactive", "payer": plan["payer"] if plan else None, "member_id": patient["member_id"],
                "reason": "Coverage terminated (synthetic)", "copay": None}
    return {"status": "active", "payer": plan["payer"], "plan": plan["name"], "member_id": patient["member_id"],
            "copay": plan["copay"], "deductible": plan["deductible"], "coinsurance": plan["coinsurance"],
            "reference": _receipt("ELIG")}


EXECUTORS = {
    "patient_template_message": patient_template_message,
    "patient_message": patient_message,
    "transmit_rx": transmit_rx,
    "send_lab_order": send_lab_order,
    "submit_claim": submit_claim,
    "vendor_order": vendor_order,
    "fill_shift": fill_shift,
    "escalate_to_doctor": escalate_to_doctor,
}
