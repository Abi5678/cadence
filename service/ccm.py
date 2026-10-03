"""Chronic Care Management (CCM) and Remote Patient Monitoring (RPM): monitoring, audit packets, mock Medicare claims.

Billing rules encoded here (simplified, demo fee schedule, synthetic data):
- CCM 99490: >= 20 min of clinical staff time in the calendar month; 99439: each additional 20 min (max 2).
- RPM 99454: device readings on >= 16 days in the month; 99457: >= 20 min interactive staff time;
  99458: each additional 20 min (max 2).
- Requires >= 2 chronic conditions, documented consent with cost-share disclosure, care plan updated
  within 12 months.
- Only human staff/provider minutes count. Agent work is logged and shown, never billed.
- A time-log entry belongs to exactly one program, so minutes are never counted twice.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

import os

from . import adapters

CLINIC_TZ_NAME = os.environ.get("CADENCE_TZ", "America/New_York")
from .db import emit, iso, new_id, now, one, parse, rows

FEE = {"99490": 60.49, "99439": 45.93, "99454": 46.88, "99457": 47.87, "99458": 38.49}  # demo fee schedule, not real rates
CODE_TEXT = {"99490": "CCM, first 20 min clinical staff", "99439": "CCM, each additional 20 min",
             "99454": "RPM device supply, 16+ days", "99457": "RPM management, first 20 min", "99458": "RPM management, each additional 20 min"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS conditions (patient_id TEXT, code TEXT, description TEXT, chronic INTEGER, onset TEXT);
CREATE TABLE IF NOT EXISTS ccm_enrollments (
  patient_id TEXT PRIMARY KEY, programs TEXT, consent_at TEXT, consent_method TEXT, cost_share_disclosed INTEGER,
  care_plan_version INTEGER, care_plan_updated_at TEXT, care_plan TEXT, enrolled_by TEXT, status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, patient_id TEXT, kind TEXT, metric TEXT, unit TEXT);
CREATE TABLE IF NOT EXISTS time_logs (
  id TEXT PRIMARY KEY, patient_id TEXT, actor_type TEXT, actor_id TEXT, minutes REAL, activity TEXT,
  program TEXT, interactive INTEGER DEFAULT 0, logged_at TEXT);
CREATE TABLE IF NOT EXISTS ccm_packets (
  id TEXT PRIMARY KEY, patient_id TEXT, month TEXT, status TEXT, result TEXT, document_id TEXT,
  reviewed_by TEXT, attested_by TEXT, attestation_ref TEXT, created_at TEXT, UNIQUE(patient_id, month));
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY, packet_id TEXT, patient_id TEXT, payer TEXT, codes TEXT, billed REAL, status TEXT,
  receipt TEXT, remit TEXT, submitted_at TEXT);
"""

CHRONIC = [("E11.9", "Type 2 diabetes"), ("I10", "Hypertension"), ("I50.9", "Heart failure"),
           ("J44.9", "COPD"), ("N18.3", "Chronic kidney disease, stage 3"), ("E78.5", "Hyperlipidemia")]
DEVICE_FOR = {"E11.9": ("glucometer", "glucose", "mg/dL"), "I10": ("bp_cuff", "systolic_bp", "mmHg"),
              "I50.9": ("scale", "weight_kg", "kg"), "J44.9": ("pulse_ox", "spo2", "%")}
FIRST = ["Alex", "Blair", "Cameron", "Dana", "Ellis", "Finley", "Gray", "Harper", "Indigo", "Jules",
         "Kai", "Lane", "Marlow", "Noel", "Oakley", "Parker", "Reese", "Sage", "Tatum", "Wren"]
LAST = ["Synthwell", "Mockford", "Datafield", "Sampleton", "Fixtures", "Placeholm", "Testa", "Demoir"]


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def prev_month(today: date | None = None) -> str:
    d = (today or now().date()).replace(day=1) - timedelta(days=1)
    return d.strftime("%Y-%m")


def month_bounds(month: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def seed(conn, base: datetime | None = None) -> None:
    """~20 synthetic chronic-care patients with last month's readings and staff time. Idempotent."""
    ensure_schema(conn)
    if one(conn.execute("SELECT 1 AS x FROM ccm_enrollments LIMIT 1")):
        return
    base = base or now()
    rng = random.Random(42)
    month = prev_month(base.date())
    start, end = month_bounds(month)
    days = (end - start).days
    plans = ["PLN-A", "PLN-B", "PLN-C"]
    for i in range(20):
        pid = f"P-{201 + i}"
        name = f"{FIRST[i]} {LAST[i % len(LAST)]}"
        conn.execute("INSERT OR IGNORE INTO patients (id,name,dob,phone,plan_id,member_id) VALUES (?,?,?,?,?,?)",
                     (pid, name, f"19{40 + i}-0{1 + i % 9}-1{i % 9}", f"+1-555-02{i:02d}", rng.choice(plans), f"MBI-{1000 + i}"))
        for kind in ("sms", "documents"):
            conn.execute("INSERT OR IGNORE INTO consents VALUES (?,?,?,?,?)", (pid, kind, 1, iso(base - timedelta(days=90)), "kiosk"))
        n_cond = 1 if i == 7 else 2 + (i % 3)  # P-208 has only one chronic condition
        conds = rng.sample(CHRONIC, n_cond)
        for code, desc in conds:
            conn.execute("INSERT INTO conditions VALUES (?,?,?,?,?)", (pid, code, desc, 1, "2019-01-01"))
        consent_missing = i == 11  # P-212: consent never documented
        plan_stale = i == 14       # P-215: care plan older than 12 months
        conn.execute("INSERT INTO ccm_enrollments VALUES (?,?,?,?,?,?,?,?,?,?)", (
            pid, "ccm+rpm", None if consent_missing else iso(start - timedelta(days=120)), None if consent_missing else "verbal, documented by RN",
            0 if consent_missing else 1, 3, iso(start - timedelta(days=500 if plan_stale else 40)),
            "Goals: stable readings, medication adherence, monthly check-in. Escalate out-of-range readings to PCP.",
            "S-1", "active"))
        devs = [DEVICE_FOR[c] for c, _ in conds if c in DEVICE_FOR] or [("bp_cuff", "systolic_bp", "mmHg")]
        reading_days = 9 if i in (3, 16) else rng.randint(18, days)  # two patients short on device days
        for j, (kind, metric, unit) in enumerate(devs):
            did = f"DEV-{pid}-{j}"
            conn.execute("INSERT INTO devices VALUES (?,?,?,?,?)", (did, pid, kind, metric, unit))
            for d in sorted(rng.sample(range(days), reading_days)):
                ts = start + timedelta(days=d, hours=13 + rng.random() * 3)
                conn.execute("INSERT INTO vitals VALUES (?,?,?,?,?,?,?)",
                             (new_id("V"), pid, metric, normal_value(metric, rng), unit, iso(ts), did))
        # Human staff time this month. A few patients fall short of thresholds on purpose.
        target_ccm = {5: 14, 9: 18}.get(i, rng.choice([22, 25, 31, 42, 47]))
        target_rpm = {2: 12}.get(i, rng.choice([21, 24, 28, 41]))
        for program, total, interactive in (("ccm", target_ccm, 0), ("rpm", target_rpm, 1)):
            left = total
            while left > 0:
                m = min(left, rng.choice([5, 7, 8, 10, 12]))
                ts = start + timedelta(days=rng.randrange(days), hours=15 + rng.random() * 4)
                staff = rng.choice(["S-1", "S-2"])
                act = ("Care-plan review call with patient" if program == "ccm" else "Reviewed readings and called patient") if interactive or program == "ccm" else "Reviewed device data"
                conn.execute("INSERT INTO time_logs VALUES (?,?,?,?,?,?,?,?,?)",
                             (new_id("TL"), pid, "staff", staff, m, act, program, interactive, iso(ts)))
                left -= m
        # The agent also worked on this patient: logged, never billable.
        for _ in range(rng.randint(6, 14)):
            ts = start + timedelta(days=rng.randrange(days), hours=rng.random() * 24)
            conn.execute("INSERT INTO time_logs VALUES (?,?,?,?,?,?,?,?,?)",
                         (new_id("TL"), pid, "agent", "cadence", 2, "Triaged device reading / drafted outreach", "rpm", 0, iso(ts)))


def normal_value(metric: str, rng: random.Random) -> float:
    return round({"glucose": rng.gauss(135, 18), "systolic_bp": rng.gauss(132, 9), "weight_kg": rng.gauss(84, 1.2),
                  "spo2": min(99, rng.gauss(95.5, 1.2))}.get(metric, 0), 1)


def monitored(conn) -> list[dict]:
    return rows(conn.execute(
        "SELECT e.patient_id, p.name, e.programs, e.status, "
        "(SELECT GROUP_CONCAT(description, ', ') FROM conditions c WHERE c.patient_id=e.patient_id AND chronic=1) AS conditions, "
        "(SELECT GROUP_CONCAT(metric, ',') FROM devices d WHERE d.patient_id=e.patient_id) AS metrics "
        "FROM ccm_enrollments e JOIN patients p ON p.id=e.patient_id WHERE e.status='active' ORDER BY e.patient_id"))


def minutes(conn, patient_id: str, month: str) -> dict:
    s, e = (iso(x) for x in month_bounds(month))
    q = rows(conn.execute("SELECT * FROM time_logs WHERE patient_id=? AND logged_at>=? AND logged_at<?", (patient_id, s, e)))
    human = [t for t in q if t["actor_type"] in ("staff", "provider")]
    return {
        "ccm": sum(t["minutes"] for t in human if t["program"] == "ccm"),
        "rpm_interactive": sum(t["minutes"] for t in human if t["program"] == "rpm" and t["interactive"]),
        "agent": sum(t["minutes"] for t in q if t["actor_type"] == "agent"),
        "agent_actions": sum(1 for t in q if t["actor_type"] == "agent"),
        "rows": q,
    }


def build_packet(conn, patient_id: str, month: str | None = None, task_id: str | None = None) -> dict:
    """Audit one patient-month and produce a review packet with evidence. Never submits anything."""
    month = month or prev_month()
    s, e = month_bounds(month)
    p = one(conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)))
    enr = one(conn.execute("SELECT * FROM ccm_enrollments WHERE patient_id=?", (patient_id,)))
    if not p or not enr:
        raise ValueError(f"{patient_id} is not enrolled in CCM/RPM")
    conds = rows(conn.execute("SELECT code, description FROM conditions WHERE patient_id=? AND chronic=1", (patient_id,)))
    mins = minutes(conn, patient_id, month)
    readings = rows(conn.execute("SELECT id, recorded_at FROM vitals WHERE patient_id=? AND recorded_at>=? AND recorded_at<? AND source LIKE 'DEV-%'",
                                 (patient_id, iso(s), iso(e))))
    reading_days = len({r["recorded_at"][:10] for r in readings})
    dup = [t["id"] for t in mins["rows"] if t["actor_type"] != "agent" and
           sum(1 for u in mins["rows"] if u["actor_id"] == t["actor_id"] and u["logged_at"] == t["logged_at"] and u["program"] != t["program"])]

    checks, gaps, codes = [], [], []

    def check(name, ok, detail, evidence=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "evidence": evidence or []})
        if not ok:
            gaps.append(f"NOT MET: {name} (found: {detail})")

    check("Two or more chronic conditions", len(conds) >= 2,
          f"{len(conds)} chronic condition(s): " + (", ".join(f"{c['code']} {c['description']}" for c in conds) or "none"),
          [c["code"] for c in conds])
    consent_ok = bool(enr["consent_at"]) and enr["cost_share_disclosed"] and parse(enr["consent_at"]) < e
    check("Consent documented with cost-share disclosure", consent_ok,
          f"{enr['consent_method']} on {enr['consent_at'][:10]}" if consent_ok else "no documented consent on file", [f"enrollment:{patient_id}"])
    plan_ok = parse(enr["care_plan_updated_at"]) >= e - timedelta(days=365)
    check("Care plan updated within 12 months", plan_ok, f"version {enr['care_plan_version']}, updated {enr['care_plan_updated_at'][:10]}",
          [f"care_plan:v{enr['care_plan_version']}"])
    check("No minutes counted in two programs", not dup, "each time-log entry belongs to one program" if not dup else f"{len(dup)} entries double-counted",
          dup or [f"time_logs:{len(mins['rows'])} entries checked"])
    eligible = len(conds) >= 2 and consent_ok and plan_ok and not dup

    ccm_ids = [t["id"] for t in mins["rows"] if t["actor_type"] != "agent" and t["program"] == "ccm"]
    check("CCM clinical staff time >= 20 min", mins["ccm"] >= 20, f"{mins['ccm']:.0f} min by staff", ccm_ids)
    rpm_ids = [t["id"] for t in mins["rows"] if t["actor_type"] != "agent" and t["program"] == "rpm" and t["interactive"]]
    check("RPM device readings on >= 16 days", reading_days >= 16, f"{reading_days} days with readings", [r["id"] for r in readings[:5]])
    check("RPM interactive time >= 20 min", mins["rpm_interactive"] >= 20, f"{mins['rpm_interactive']:.0f} min interactive", rpm_ids)

    if eligible:
        if mins["ccm"] >= 20:
            codes.append({"code": "99490", "units": 1})
            extra = min(2, int((mins["ccm"] - 20) // 20))
            if extra:
                codes.append({"code": "99439", "units": extra})
        if reading_days >= 16:
            codes.append({"code": "99454", "units": 1})
        if mins["rpm_interactive"] >= 20:
            codes.append({"code": "99457", "units": 1})
            extra = min(2, int((mins["rpm_interactive"] - 20) // 20))
            if extra:
                codes.append({"code": "99458", "units": extra})
    for c in codes:
        c["description"] = CODE_TEXT[c["code"]]
        c["amount"] = round(FEE[c["code"]] * c["units"], 2)
    total = round(sum(c["amount"] for c in codes), 2)
    result = {"patient_id": patient_id, "patient": p["name"], "month": month, "codes": codes, "billed": total,
              "checks": checks, "gaps": gaps, "reading_days": reading_days,
              "staff_minutes": {"ccm": mins["ccm"], "rpm_interactive": mins["rpm_interactive"]},
              "agent": {"minutes": mins["agent"], "actions": mins["agent_actions"], "billable": False},
              "fee_schedule": "demo values, not CMS rates"}
    lines = [f"CCM/RPM audit packet: {p['name']} ({patient_id}), {month}", "",
             *(f"[{'PASS' if c['ok'] else 'GAP '}] {c['check']}: {c['detail']}" for c in checks), "",
             "Proposed codes: " + (", ".join(f"{c['code']} x{c['units']} (${c['amount']:.2f})" for c in codes) or "none: not billable this month"),
             f"Total: ${total:.2f}",
             f"Agent work: {mins['agent_actions']} actions / {mins['agent']:.0f} min, excluded from billable time.",
             "Requires coordinator review and provider attestation before submission."]
    existing = one(conn.execute("SELECT * FROM ccm_packets WHERE patient_id=? AND month=?", (patient_id, month)))
    if existing and existing["status"] not in ("draft", "needs_review"):
        return dict(existing, result=json.loads(existing["result"]))
    from .clinic import create_document
    doc = create_document(conn, patient_id, "ccm_packet", f"CCM/RPM packet {month}", "\n".join(lines), status="needs_release")
    status = "needs_review" if codes else "not_billable"
    if existing:
        conn.execute("UPDATE ccm_packets SET status=?, result=?, document_id=? WHERE id=?", (status, json.dumps(result), doc["id"], existing["id"]))
        kid = existing["id"]
    else:
        kid = new_id("K")
        conn.execute("INSERT INTO ccm_packets (id,patient_id,month,status,result,document_id,created_at) VALUES (?,?,?,?,?,?,?)",
                     (kid, patient_id, month, status, json.dumps(result), doc["id"], iso(now())))
    emit(conn, task_id, "ccm.packet", "agent", f"{patient_id} {month}: {status.replace('_', ' ')}, ${total:.2f}" + (f", {len(gaps)} gap(s)" if gaps else ""),
         {"packet_id": kid})
    return {"id": kid, "status": status, "result": result}


def month_end_close(conn, month: str | None = None, task_id: str | None = None) -> dict:
    month = month or prev_month()
    out = [build_packet(conn, m["patient_id"], month, task_id) for m in monitored(conn)]
    ready = [o for o in out if o["status"] == "needs_review"]
    return {"month": month, "packets": len(out), "ready_for_review": len(ready),
            "billable_total": round(sum(o["result"]["billed"] for o in ready), 2),
            "not_billable": [{"patient_id": o["result"]["patient_id"], "gaps": o["result"]["gaps"]} for o in out if o["status"] == "not_billable"]}


def review_packet(conn, packet_id: str, approve: bool, by: str) -> dict:
    k = one(conn.execute("SELECT * FROM ccm_packets WHERE id=?", (packet_id,)))
    if not k or k["status"] != "needs_review":
        raise ValueError("packet is not awaiting review")
    if not approve:
        conn.execute("UPDATE ccm_packets SET status='returned', reviewed_by=? WHERE id=?", (by, packet_id))
        emit(conn, None, "ccm.returned", by, f"Packet {packet_id} returned for correction")
        return one(conn.execute("SELECT * FROM ccm_packets WHERE id=?", (packet_id,)))
    conn.execute("UPDATE ccm_packets SET status='awaiting_attestation', reviewed_by=? WHERE id=?", (by, packet_id))
    r = json.loads(k["result"])
    adapters.slack_dm(conn, "DR-CHEN", f"CCM/RPM packet *{packet_id}* for {r['patient']} ({k['month']}) reviewed by the coordinator: "
                                       f"{', '.join(c['code'] for c in r['codes'])}, ${r['billed']:.2f}.\nReply `CONFIRM {packet_id}` to attest and submit to Medicare.")
    emit(conn, None, "ccm.reviewed", by, f"Packet {packet_id} approved by coordinator; awaiting provider attestation")
    return one(conn.execute("SELECT * FROM ccm_packets WHERE id=?", (packet_id,)))


def attest_packet(conn, packet_id: str, provider_id: str, ref: str) -> dict:
    """Provider attestation (verified Slack CONFIRM, or the UI's demo attest). Submits the mock claim."""
    k = one(conn.execute("SELECT * FROM ccm_packets WHERE id=?", (packet_id,)))
    if not k or k["status"] != "awaiting_attestation":
        return k or {}
    conn.execute("UPDATE ccm_packets SET status='attested', attested_by=?, attestation_ref=? WHERE id=?", (provider_id, ref, packet_id))
    conn.execute("UPDATE documents SET status='released', released_by=? WHERE id=?", (provider_id, k["document_id"]))
    emit(conn, None, "ccm.attested", provider_id, f"Packet {packet_id} attested ({ref})")
    from .clinic import decide, propose
    ap = propose(conn, None, "submit_medicare_claim", {"packet_id": packet_id}, f"Submit Medicare claim for packet {packet_id}",
                 dedupe_key=f"claim:{packet_id}")
    return decide(conn, ap["id"], True, "policy:coordinator-reviewed+provider-attested")


def remittances_due(conn) -> list[str]:
    """Mock MAC: 835 remittance a minute after submission (80% Medicare, 20% patient coinsurance)."""
    done = []
    for c in rows(conn.execute("SELECT * FROM claims WHERE status='submitted' AND submitted_at <= ?", (iso(now() - timedelta(seconds=60)),))):
        paid = round(c["billed"] * 0.8, 2)
        remit = {"paid": paid, "patient_coinsurance": round(c["billed"] - paid, 2), "era": new_id("ERA")}
        conn.execute("UPDATE claims SET status='paid', remit=? WHERE id=?", (json.dumps(remit), c["id"]))
        conn.execute("UPDATE ccm_packets SET status='paid' WHERE id=?", (c["packet_id"],))
        emit(conn, None, "claim.paid", "medicare-mock", f"Claim {c['id']} paid ${paid:.2f} (835 {remit['era']})")
        done.append(c["id"])
    return done


def gaps_this_month(conn) -> list[dict]:
    """Patients at risk of missing thresholds before month end, so staff can schedule care calls."""
    month = now().strftime("%Y-%m")
    s, e = month_bounds(month)
    left = (e - now()).days
    out = []
    for m in monitored(conn):
        mins = minutes(conn, m["patient_id"], month)
        days = len({r["recorded_at"][:10] for r in rows(conn.execute(
            "SELECT recorded_at FROM vitals WHERE patient_id=? AND recorded_at>=? AND source LIKE 'DEV-%'", (m["patient_id"], iso(s))))})
        need = []
        if mins["ccm"] < 20:
            need.append(f"{20 - mins['ccm']:.0f} more CCM staff minutes")
        if mins["rpm_interactive"] < 20:
            need.append(f"{20 - mins['rpm_interactive']:.0f} more interactive RPM minutes")
        if days + left < 16:
            need.append(f"cannot reach 16 reading days ({days} so far)")
        if need:
            out.append({"patient_id": m["patient_id"], "name": m["name"], "days_left": left, "needs": need})
    return out


def log_staff_time(conn, patient_id: str, staff_id: str, minutes_: float, activity: str, program: str, interactive: bool) -> dict:
    """Human staff log their own time from the UI. The agent has no tool for this."""
    if program not in ("ccm", "rpm"):
        raise ValueError("program must be ccm or rpm")
    if not one(conn.execute("SELECT id FROM staff WHERE id=?", (staff_id,))):
        raise ValueError("unknown staff member")
    tid = new_id("TL")
    conn.execute("INSERT INTO time_logs VALUES (?,?,?,?,?,?,?,?,?)",
                 (tid, patient_id, "staff", staff_id, float(minutes_), activity[:200], program, int(interactive), iso(now())))
    emit(conn, None, "ccm.time", staff_id, f"{staff_id} logged {minutes_:.0f} min {program.upper()} for {patient_id}")
    return {"id": tid}


def log_agent_action(conn, patient_id: str, activity: str) -> None:
    conn.execute("INSERT INTO time_logs VALUES (?,?,?,?,?,?,?,?,?)",
                 (new_id("TL"), patient_id, "agent", "cadence", 1, activity[:200], "rpm", 0, iso(now())))
