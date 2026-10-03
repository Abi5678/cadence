"""Measured work the agent did today, converted to estimated staff time saved.

The counts are real (from the event log). The minutes per action are stated ESTIMATES of the manual
work each replaces, shown next to every number so nobody mistakes them for measurements.
"""
from __future__ import annotations

import json

from .db import now, rows

# action -> (label, SQL count over events since start of day, estimated manual minutes each)
ACTIONS = [
    ("confirmations", "Appointment confirmations sent", "kind='approval.confirmed' AND summary LIKE 'Confirmation request%'", 3),
    ("reminders", "Day-of reminders sent", "kind='approval.confirmed' AND summary LIKE 'Day-of reminder%'", 2),
    ("backfills", "Freed slots offered to the waitlist", "kind='approval.confirmed' AND summary LIKE 'Offer %'", 5),
    ("bookings", "Waitlist patients booked", "kind='appointment.booked'", 4),
    ("aftercare", "Aftercare check-ins sent", "kind='approval.confirmed' AND summary LIKE 'Aftercare check-in%'", 4),
    ("triage", "Device alerts triaged", "kind='vitals.recorded' AND summary LIKE '%OUT OF RANGE%'", 3),
    ("notes", "Visit notes drafted from voice", "kind='voice.drafted'", 10),
    ("orders", "Orders routed to pharmacy or lab", "kind='order.signed'", 4),
    ("eligibility", "Insurance checks run", "kind='insurance.checked'", 5),
    ("packets", "Chronic-care billing packets audited", "kind='ccm.packet'", 15),
]


def today(conn) -> dict:
    start = now().strftime("%Y-%m-%d")
    items, minutes = [], 0.0
    for key, label, where, each in ACTIONS:
        if key == "packets":  # rebuilt packets count once
            n = conn.execute("SELECT COUNT(*) FROM ccm_packets WHERE created_at >= ?", (start,)).fetchone()[0]
        else:
            n = conn.execute(f"SELECT COUNT(*) FROM events WHERE created_at >= ? AND {where}", (start,)).fetchone()[0]
        items.append({"key": key, "label": label, "count": n, "minutes_each_estimate": each, "minutes": n * each})
        minutes += n * each
    ready = sum(json.loads(r["result"])["billed"] for r in rows(conn.execute(
        "SELECT result FROM ccm_packets WHERE status IN ('needs_review','awaiting_attestation','attested','submitted','paid')")))
    paid = sum(json.loads(r["remit"])["paid"] for r in rows(conn.execute("SELECT remit FROM claims WHERE status='paid'")))
    return {"items": items, "hours_saved_estimate": round(minutes / 60, 1), "actions": sum(i["count"] for i in items),
            "ccm_identified_usd": round(ready, 2), "medicare_paid_usd": round(paid, 2),
            "note": "Counts are measured from the agent's event log; minutes per action are estimates of the manual work replaced. "
                    "Dollar amounts use a demo fee schedule, not CMS rates."}
