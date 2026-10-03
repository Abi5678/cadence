"""SQLite schema, connection helpers and synthetic seed data for the Cadence clinic service.

All people, plans and records here are synthetic. Never load real patient data.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("CADENCE_DB", Path.home() / ".local/share/cadence/cadence.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS patients (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, dob TEXT, phone TEXT,
  preferred_channel TEXT DEFAULT 'sms', plan_id TEXT, member_id TEXT, synthetic INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT, slack_user TEXT
);
CREATE TABLE IF NOT EXISTS staff (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, max_weekly_hours REAL DEFAULT 40
);
CREATE TABLE IF NOT EXISTS shifts (
  id TEXT PRIMARY KEY, role TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  staff_id TEXT REFERENCES staff(id), status TEXT DEFAULT 'scheduled'
);
CREATE TABLE IF NOT EXISTS appointments (
  id TEXT PRIMARY KEY, patient_id TEXT REFERENCES patients(id), provider_id TEXT REFERENCES providers(id),
  starts_at TEXT NOT NULL, minutes INTEGER DEFAULT 30, reason TEXT,
  status TEXT NOT NULL DEFAULT 'booked',   -- open|booked|confirmed|checked_in|completed|cancelled|no_show
  confirmation TEXT DEFAULT 'none',        -- none|requested|confirmed|declined
  offered_to TEXT, offer_expires_at TEXT, version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS waitlist (
  id TEXT PRIMARY KEY, patient_id TEXT REFERENCES patients(id), provider_id TEXT,
  reason TEXT, priority INTEGER DEFAULT 3, added_at TEXT, status TEXT DEFAULT 'waiting'  -- waiting|offered|placed|removed
);
CREATE TABLE IF NOT EXISTS slot_declines (
  slot_id TEXT, patient_id TEXT, PRIMARY KEY (slot_id, patient_id)
);
CREATE TABLE IF NOT EXISTS insurance_plans (
  id TEXT PRIMARY KEY, payer TEXT, name TEXT, copay REAL, deductible REAL, coinsurance REAL
);
CREATE TABLE IF NOT EXISTS eligibility_checks (
  id TEXT PRIMARY KEY, patient_id TEXT, appointment_id TEXT, status TEXT, detail TEXT, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS charges (
  id TEXT PRIMARY KEY, appointment_id TEXT, patient_id TEXT, code TEXT, description TEXT,
  amount REAL, patient_responsibility REAL, status TEXT DEFAULT 'draft', created_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL,           -- lab|rx
  patient_id TEXT, provider_id TEXT, detail TEXT NOT NULL,
  signed_by TEXT, signed_at TEXT, status TEXT DEFAULT 'received', created_at TEXT, source TEXT,
  signature_ref TEXT, result_due_at TEXT   -- signature_ref: Slack ts of the doctor's CONFIRM message
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, patient_id TEXT, kind TEXT, title TEXT, body TEXT,
  status TEXT DEFAULT 'released',   -- needs_release|released|sent
  ref TEXT, released_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS consents (
  patient_id TEXT, kind TEXT, granted INTEGER, recorded_at TEXT, recorded_by TEXT,
  PRIMARY KEY (patient_id, kind)     -- kind: sms|documents
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS inventory (
  sku TEXT PRIMARY KEY, name TEXT, on_hand INTEGER, par INTEGER, reorder_qty INTEGER, vendor TEXT, unit_cost REAL
);
CREATE TABLE IF NOT EXISTS vitals (
  id TEXT PRIMARY KEY, patient_id TEXT, kind TEXT, value REAL, unit TEXT, recorded_at TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS aftercare (
  id TEXT PRIMARY KEY, patient_id TEXT, appointment_id TEXT, due_at TEXT, question TEXT,
  status TEXT DEFAULT 'scheduled',   -- scheduled|sent|answered|escalated|closed
  answer TEXT, flagged INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, channel TEXT, direction TEXT, party TEXT, body TEXT, ref TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, kind TEXT, title TEXT, brief TEXT, status TEXT DEFAULT 'running',
  created_at TEXT, updated_at TEXT, dedupe_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT, actor TEXT, summary TEXT,
  data TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, task_id TEXT, action TEXT NOT NULL, payload TEXT NOT NULL, summary TEXT,
  state TEXT NOT NULL DEFAULT 'prepared',  -- prepared|approved|rejected|attempted|confirmed|failed
  decided_by TEXT, decided_at TEXT, receipt TEXT, created_at TEXT, dedupe_key TEXT UNIQUE
);
"""

_lock = threading.RLock()
_local = threading.local()


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


_counter = {"n": 0}


def new_id(prefix: str) -> str:
    with _lock:
        _counter["n"] += 1
        return f"{prefix}-{now().strftime('%H%M%S')}-{os.urandom(2).hex()}{_counter['n']}"


def seed(conn: sqlite3.Connection, base: datetime | None = None) -> None:
    """Load a small synthetic clinic. Idempotent: skips if patients exist."""
    if conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]:
        return
    base = (base or now()).replace(minute=0, second=0)
    today9 = base.replace(hour=14)  # 09:00 Central expressed in UTC
    ins = conn.executemany
    ins("INSERT INTO insurance_plans VALUES (?,?,?,?,?,?)", [
        ("PLN-A", "Synthetic Mutual", "Silver PPO", 30, 1500, 0.2),
        ("PLN-B", "Demo Health", "Bronze HMO", 50, 4000, 0.3),
        ("PLN-C", "Example Care", "Gold PPO", 15, 500, 0.1),
    ])
    ins("INSERT INTO patients (id,name,dob,phone,plan_id,member_id) VALUES (?,?,?,?,?,?)", [
        ("P-101", "Avery Synth", "1980-04-12", "+1-555-0101", "PLN-A", "SM-88231"),
        ("P-102", "Jordan Fixture", "1972-11-03", "+1-555-0102", "PLN-B", "DH-11902"),
        ("P-103", "Riley Sample", "1990-06-21", "+1-555-0103", "PLN-C", "EC-55410"),
        ("P-104", "Casey Demo", "1965-01-30", "+1-555-0104", "PLN-A", "SM-90012"),
        ("P-105", "Morgan Test", "2001-09-09", "+1-555-0105", "PLN-B", "EXPIRED-1"),
        ("P-106", "Quinn Mock", "1958-02-17", "+1-555-0106", "PLN-C", "EC-77120"),
        ("P-107", "Taylor Placeholder", "1984-12-01", "+1-555-0107", "PLN-A", "SM-34001"),
    ])
    ins("INSERT INTO providers VALUES (?,?,?,?)", [
        ("DR-CHEN", "Dr. Chen", "physician", os.environ.get("CADENCE_DOCTOR_SLACK_USER")),
        ("DR-PATEL", "Dr. Patel", "physician", None),
    ])
    ins("INSERT INTO staff VALUES (?,?,?,?)", [
        ("S-1", "Sam (RN)", "nurse", 40), ("S-2", "Lee (RN)", "nurse", 36),
        ("S-3", "Kim (MA)", "medical_assistant", 40), ("S-4", "Pat (Front desk)", "front_desk", 40),
        ("S-5", "Alex (MA)", "medical_assistant", 24),
    ])
    tomorrow = today9 + timedelta(days=1)
    shifts = []
    for d, day in enumerate([today9, tomorrow]):
        shifts += [
            (f"SH-{d}-RN1", "nurse", iso(day), iso(day + timedelta(hours=8)), "S-1", "scheduled"),
            (f"SH-{d}-MA1", "medical_assistant", iso(day), iso(day + timedelta(hours=8)), "S-3", "scheduled"),
            (f"SH-{d}-FD1", "front_desk", iso(day), iso(day + timedelta(hours=8)), "S-4", "scheduled"),
        ]
    shifts.append(("SH-1-RN2", "nurse", iso(tomorrow + timedelta(hours=4)), iso(tomorrow + timedelta(hours=10)), None, "open"))
    ins("INSERT INTO shifts VALUES (?,?,?,?,?,?)", shifts)
    appts = [
        ("A-201", "P-101", "DR-CHEN", today9 + timedelta(hours=2), "Follow-up", "booked", "none"),
        ("A-202", "P-102", "DR-CHEN", today9 + timedelta(hours=3), "Annual physical", "confirmed", "confirmed"),
        ("A-203", "P-103", "DR-PATEL", tomorrow, "Knee pain", "booked", "none"),
        ("A-204", "P-104", "DR-CHEN", tomorrow + timedelta(hours=1), "Post-op check", "booked", "none"),
        ("A-205", "P-105", "DR-PATEL", tomorrow + timedelta(hours=2), "Consult", "booked", "none"),
        ("A-206", "P-106", "DR-CHEN", today9 - timedelta(days=2), "Minor procedure", "completed", "confirmed"),
        ("A-207", None, "DR-CHEN", tomorrow + timedelta(hours=3), None, "open", "none"),
    ]
    ins("INSERT INTO appointments (id,patient_id,provider_id,starts_at,reason,status,confirmation) VALUES (?,?,?,?,?,?,?)",
        [(a, p, d, iso(t), r, s, c) for a, p, d, t, r, s, c in appts])
    ins("INSERT INTO waitlist (id,patient_id,provider_id,reason,priority,added_at) VALUES (?,?,?,?,?,?)", [
        ("W-1", "P-107", "DR-CHEN", "Earlier follow-up requested", 2, iso(base - timedelta(days=3))),
        ("W-2", "P-103", None, "Any earlier slot", 3, iso(base - timedelta(days=1))),
    ])
    ins("INSERT INTO aftercare (id,patient_id,appointment_id,due_at,question) VALUES (?,?,?,?,?)", [
        ("AC-1", "P-106", "A-206", iso(base - timedelta(minutes=5)),
         "How is your recovery after your procedure? Any fever, increasing pain, redness or swelling (yes/no)?"),
    ])
    ins("INSERT INTO inventory VALUES (?,?,?,?,?,?,?)", [
        ("GLV-M", "Nitrile gloves (M), box", 6, 20, 40, "Synthetic Supply Co", 7.5),
        ("SYR-3", "3 mL syringes, box", 35, 25, 50, "Synthetic Supply Co", 12.0),
        ("FLU-VAX", "Influenza vaccine, dose", 4, 15, 30, "Demo Biologics", 18.0),
        ("GAUZE", "Gauze pads 4x4, pack", 50, 30, 60, "Synthetic Supply Co", 3.2),
    ])
    consents = []
    for pid in ("P-101", "P-102", "P-103", "P-104", "P-106", "P-107"):
        consents += [(pid, "sms", 1, iso(base - timedelta(days=30)), "kiosk"), (pid, "documents", 1, iso(base - timedelta(days=30)), "kiosk")]
    consents += [("P-105", "sms", 1, iso(base), "kiosk"), ("P-105", "documents", 0, iso(base), "kiosk")]
    ins("INSERT INTO consents VALUES (?,?,?,?,?)", consents)
    ins("INSERT INTO documents (id,patient_id,kind,title,body,status,ref,created_at) VALUES (?,?,?,?,?,?,?,?)", [
        ("D-1", "P-106", "visit_summary", "Visit summary: minor procedure", "Administrative visit summary (synthetic): procedure visit with Dr. Chen, aftercare check-in scheduled.", "released", "A-206", iso(base - timedelta(days=2))),
    ])
    ins("INSERT INTO orders (id,kind,patient_id,provider_id,detail,signed_by,signed_at,status,created_at,source) VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("O-301", "lab", "P-104", "DR-CHEN", "CBC and basic metabolic panel", "DR-CHEN", iso(base - timedelta(hours=1)), "received", iso(base), "seed"),
        ("O-302", "rx", "P-104", "DR-CHEN", "Synthetic-cillin 500 mg, 1 cap PO BID x 7 days, #14, 0 refills", "DR-CHEN", iso(base - timedelta(hours=1)), "received", iso(base), "seed"),
        ("O-303", "rx", "P-101", "DR-PATEL", "Placebo-statin 20 mg daily, #30", None, None, "received", iso(base), "seed"),
    ])


def emit(conn: sqlite3.Connection, task_id: str | None, kind: str, actor: str, summary: str, data: dict | None = None) -> dict:
    ts = iso(now())
    cur = conn.execute(
        "INSERT INTO events (task_id,kind,actor,summary,data,created_at) VALUES (?,?,?,?,?,?)",
        (task_id, kind, actor, summary, json.dumps(data or {}), ts),
    )
    ev = {"seq": cur.lastrowid, "task_id": task_id, "kind": kind, "actor": actor, "summary": summary, "data": data or {}, "created_at": ts}
    for fn in list(LISTENERS):
        try:
            fn(ev)
        except Exception:
            pass
    return ev


LISTENERS: list = []
