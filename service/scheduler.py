"""Always-on loop: deterministic sweeps on a timer, plus agent tasks for anything needing judgment."""
from __future__ import annotations

import asyncio
import logging
import os

from . import clinic, mcp_tools
from .db import LISTENERS, now

log = logging.getLogger("cadence.scheduler")
SWEEP_SECONDS = int(os.environ.get("CADENCE_SWEEP_SECONDS", "60"))


def run_sweep(conn, runner) -> dict:
    """One sweep. Creates at most one agent task per item per day (dedupe keys)."""
    day = now().date().isoformat()
    with mcp_tools.LOCK:
        out = clinic.sweep(conn)
        new = []
        jobs = [
            ("documents", out["lab_results"], "Lab results arrived", "Lab results came back for orders {}. The doctor was asked in Slack to RELEASE them; post a short note for the front desk."),
            ("orders", out["unrouted_orders"], "Route doctor orders", "Route these new doctor orders (signed ones to approval; ask for signatures on unsigned): {}"),
            ("inventory", out["low_stock"], "Restock low inventory", "These SKUs are below par; draft reorders: {}"),
            ("staffing", out["open_shifts"], "Fill open shifts", "These shifts have no one assigned; pick eligible staff and propose fills: {}"),
            ("insurance", out["insurance_tomorrow"], "Verify insurance for tomorrow", "Run eligibility for these appointments and flag inactive coverage: {}"),
        ]
        for kind, items, title, brief in jobs:
            if items:
                key = f"sweep:{kind}:{day}:{','.join(sorted(items))}"
                t = clinic.create_task(conn, kind, title, brief.format(", ".join(items)), dedupe_key=key)
                if t["status"] == "running":
                    new.append(t["id"])
        from . import ccm
        out["remittances"] = ccm.remittances_due(conn)
        # Retry tasks still waiting on the agent (e.g. model was not up yet). Dedup by inflight set.
        retry = [r["id"] for r in conn.execute("SELECT id FROM tasks WHERE status='running' ORDER BY created_at").fetchall()]
    for tid in dict.fromkeys(new + retry):
        runner.wake(tid)
    out["agent_tasks"] = new
    return out


def on_event(conn, runner):
    """Event triggers: patient replies wake the agent immediately."""
    def handle(ev: dict) -> None:
        if ev["kind"] == "patient.reply":
            pid = ev["data"]["patient_id"]
            t = clinic.create_task(conn, "patient_reply", f"Patient {pid} replied",
                                   f"Patient {pid} sent: \"{ev['summary']}\". Read patient_context({pid}) and act on their intent.",
                                   dedupe_key=f"reply:{ev['data']['message_id']}")
            runner.wake(t["id"])
    return handle


async def slack_loop(conn) -> None:
    """Doctor CONFIRM/RELEASE/CANCEL replies, verified against Slack every few seconds."""
    from . import slack_sync
    try:
        await asyncio.to_thread(slack_sync.announce, conn, mcp_tools.LOCK)
    except Exception:  # noqa: BLE001
        log.exception("slack announce failed")
    while True:
        try:
            await asyncio.to_thread(slack_sync.sync, conn, mcp_tools.LOCK)
        except Exception:  # noqa: BLE001 - Slack hiccups must not stop the clinic loop
            log.exception("slack sync failed")
        await asyncio.sleep(int(os.environ.get("CADENCE_SLACK_POLL_SECONDS", "8")))


async def loop(conn, runner) -> None:
    LISTENERS.append(on_event(conn, runner))
    while True:
        try:
            r = run_sweep(conn, runner)
            log.info("sweep: %s", {k: len(v) for k, v in r.items() if isinstance(v, list)})
        except Exception:  # noqa: BLE001 - never let the always-on loop die
            log.exception("sweep failed")
        await asyncio.sleep(SWEEP_SECONDS)
