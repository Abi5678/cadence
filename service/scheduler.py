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
    """Event triggers: patient replies and signed doctor orders wake the agent immediately."""
    def handle(ev: dict) -> None:
        if ev["kind"] == "patient.reply":
            pid = ev["data"]["patient_id"]
            bound = ev["data"].get("reschedule")
            if bound:
                brief = (f"Patient {pid} reply was bound to proposal {bound.get('proposal_id')} "
                         f"({bound.get('status')}). Verify the schedule with a fresh read. Do not book or cancel again.")
            else:
                brief = f"Patient {pid} sent: \"{ev['summary']}\". Read patient_context({pid}) and act on their intent."
            t = clinic.create_task(conn, "patient_reply", f"Patient {pid} replied", brief,
                                   dedupe_key=f"reply:{ev['data']['message_id']}")
            runner.wake(t["id"])
        elif ev["kind"] == "order.signed" and ev.get("task_id"):
            # Continue the long-running doctor_order task: confirm routing, then finish when queued.
            oid = (ev.get("data") or {}).get("order_id") or "?"
            with mcp_tools.LOCK:
                clinic.set_task_status(
                    conn, ev["task_id"], "running",
                    f"{oid} signed. Confirm list_orders shows it pending_approval, post a one-line status, "
                    f"and finish_task if no other drafts for this patient are still awaiting_signature.")
            runner.wake(ev["task_id"])
        elif ev["kind"] == "approval.confirmed" and ev.get("task_id"):
            with mcp_tools.LOCK:
                t = clinic.get_task(conn, ev["task_id"])
                if t.get("kind") != "doctor_order":
                    return
                pid = (t.get("title") or "").removeprefix("Doctor orders for ").strip() or None
                awaiting = []
                if pid:
                    awaiting = [o["id"] for o in clinic.list_orders(conn) if o["patient_id"] == pid and o["status"] == "awaiting_signature"]
                open_aps = [a for a in t.get("approvals") or [] if a["state"] == "prepared"]
                if not awaiting and not open_aps and t["status"] != "completed":
                    clinic.set_task_status(conn, ev["task_id"], "completed",
                                          "All signed orders for this patient are transmitted or queued.")
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
