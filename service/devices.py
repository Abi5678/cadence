"""Always-on remote monitoring stream: simulated BP cuffs, glucometers, scales and pulse oximeters.

Each tick emits one reading per monitored device (the clock can be sped up for the night-shift demo).
Out-of-range readings go through clinic.record_vitals, which escalates to the doctor, and open an agent
triage task. Readings are synthetic and labeled as coming from a device id.
"""
from __future__ import annotations

import asyncio
import os
import random

from . import ccm, clinic, mcp_tools
from .db import emit, iso, new_id, now

TICK_SECONDS = float(os.environ.get("CADENCE_DEVICE_TICK", "6"))
ANOMALY_RATE = float(os.environ.get("CADENCE_ANOMALY_RATE", "0.002"))  # ~1 anomaly every few minutes at 1x
ABNORMAL = {"glucose": lambda r: r.choice([52, 61, 288, 312]), "systolic_bp": lambda r: r.choice([171, 184, 86]),
            "spo2": lambda r: r.choice([87, 89, 90]), "weight_kg": lambda r: None}
STATE = {"speed": 1.0, "readings": 0, "anomalies": 0, "until": None, "quiet": False}


def set_quiet(on: bool) -> dict:
    """Recording mode: no random out-of-range readings (so the doctor's Slack stays clean); 'A' still triggers one on cue."""
    STATE["quiet"] = bool(on)
    return {"quiet": STATE["quiet"]}
_rng = random.Random()


def alert(conn, runner, d, val) -> dict:
    """One out-of-range reading: record (escalates to the doctor), log agent triage, open a monitoring task."""
    r = clinic.record_vitals(conn, d["patient_id"], d["metric"], val, d["unit"], source=d["id"])
    STATE["anomalies"] += 1
    ccm.log_agent_action(conn, d["patient_id"], f"Triaged out-of-range {d['metric']} {val}")
    t = clinic.create_task(conn, "monitoring", f"Out-of-range {d['metric']} for {d['patient_id']}",
                           f"Device {d['id']} reported {d['metric']}={val}{d['unit']} for {d['name']} ({d['patient_id']}). "
                           "The doctor was alerted automatically. Check recent_vitals for a trend, post a one-line note for the "
                           "care team, and if the patient has SMS consent draft a short message that the care team will call.",
                           dedupe_key=f"anomaly:{d['patient_id']}:{now().strftime('%Y%m%d%H')}{now().minute // 30}")
    runner.wake(t["id"])
    return {"patient_id": d["patient_id"], "metric": d["metric"], "value": val, "reading": r}


def inject(conn, runner, patient_id: str | None = None) -> dict:
    """Director mode: an out-of-range reading on demand (for the demo video), from a device that can go abnormal."""
    q = ("SELECT d.*, p.name FROM devices d JOIN patients p ON p.id=d.patient_id WHERE d.metric != 'weight_kg'"
         + (" AND d.patient_id=?" if patient_id else "") + " ORDER BY d.patient_id LIMIT 1")
    d = conn.execute(q, (patient_id,) if patient_id else ()).fetchone()
    if not d:
        raise ValueError("no monitored device for that patient")
    return alert(conn, runner, d, ABNORMAL[d["metric"]](_rng))


def tick(conn, runner) -> list[dict]:
    out = []
    with mcp_tools.LOCK:
        devices = conn.execute("SELECT d.*, p.name FROM devices d JOIN ccm_enrollments e ON e.patient_id=d.patient_id "
                               "JOIN patients p ON p.id=d.patient_id WHERE e.status='active'").fetchall()
    for d in devices:
        if _rng.random() > 0.35:  # not every device reports every tick
            continue
        abnormal = not STATE["quiet"] and _rng.random() < ANOMALY_RATE * STATE["speed"] ** 0.5
        val = ABNORMAL[d["metric"]](_rng) if abnormal else None
        if val is None:
            abnormal, val = False, ccm.normal_value(d["metric"], _rng)
        with mcp_tools.LOCK:
            if abnormal:
                alert(conn, runner, d, val)
            else:
                conn.execute("INSERT INTO vitals VALUES (?,?,?,?,?,?,?)",
                             (new_id("V"), d["patient_id"], d["metric"], val, d["unit"], iso(now()), d["id"]))
                emit(conn, None, "device.reading", d["id"], f"{d['patient_id']} {d['metric']}={val}{d['unit']}",
                     {"patient_id": d["patient_id"], "metric": d["metric"], "value": val, "ok": True})
        STATE["readings"] += 1
        out.append({"patient_id": d["patient_id"], "metric": d["metric"], "value": val, "abnormal": abnormal})
    return out


async def loop(conn, runner) -> None:
    while True:
        if STATE["until"] and now() >= STATE["until"]:
            STATE["speed"], STATE["until"] = 1.0, None
            with mcp_tools.LOCK:
                emit(conn, None, "nightshift.end", "service", "Night-shift simulation finished")
        try:
            await asyncio.to_thread(tick, conn, runner)
        except Exception:  # noqa: BLE001 - keep streaming
            pass
        await asyncio.sleep(TICK_SECONDS / STATE["speed"])


def start_night_shift(conn, minutes: float = 2, speed: float = 20) -> dict:
    from datetime import timedelta
    STATE["speed"], STATE["until"] = speed, now() + timedelta(minutes=minutes)
    STATE["night_start"] = iso(now())
    emit(conn, None, "nightshift.start", "service", f"Night-shift simulation: {speed:.0f}x device stream for {minutes:.0f} min")
    return {"speed": speed, "until": iso(STATE["until"]), "since": STATE["night_start"]}
