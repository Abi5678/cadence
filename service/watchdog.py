"""Self-healing for the always-on stack: restarts a frozen model server or a dead speech service.

vLLM counts as frozen when requests are running but no token was generated for STALL_SECONDS (we saw
exactly this hang once today). A service that stops answering health checks is restarted too. After a
restart the watchdog waits GRACE_SECONDS for the model to reload before judging again.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time

import httpx

from . import mcp_tools
from .db import emit

log = logging.getLogger("cadence.watchdog")
CHECK_SECONDS, STALL_SECONDS, GRACE_SECONDS = 20, 90, 420
VLLM = os.environ.get("CADENCE_VLLM_URL", "http://127.0.0.1:8000/v1").replace("/v1", "")
ASR = os.environ.get("CADENCE_ASR_URL", "http://127.0.0.1:8001")
STATE: dict = {"restarts": 0, "last": None}


def _metric(text: str, name: str) -> float:
    return sum(float(v) for v in re.findall(rf"^{name}(?:{{[^}}]*}})?\s+([\d.e+]+)$", text, re.M))


def _restart(conn, container: str, why: str) -> None:
    r = subprocess.run(["docker", "restart", container], capture_output=True, text=True, timeout=120)
    ok = r.returncode == 0
    STATE["restarts"] += 1
    STATE["last"] = f"{container}: {why}"
    with mcp_tools.LOCK:
        emit(conn, None, "watchdog.restart", "watchdog",
             f"Self-healing: restarted {container} ({why})" + ("" if ok else f" FAILED: {r.stderr.strip()[:120]}"))
    log.warning("restarted %s: %s (ok=%s)", container, why, ok)


async def loop(conn) -> None:
    last_tokens, last_progress = None, time.monotonic()
    grace = {"cadence-vllm": 0.0, "cadence-asr": 0.0}
    asr_failures = 0
    async with httpx.AsyncClient(timeout=5) as c:
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            t = time.monotonic()
            try:  # model server
                if t > grace["cadence-vllm"]:
                    try:
                        m = (await c.get(f"{VLLM}/metrics")).text
                        tokens, running = _metric(m, "vllm:generation_tokens_total"), _metric(m, "vllm:num_requests_running")
                        if running == 0 or tokens != last_tokens:
                            last_progress = t
                        last_tokens = tokens
                        if running > 0 and t - last_progress > STALL_SECONDS:
                            await asyncio.to_thread(_restart, conn, "cadence-vllm", f"{int(running)} requests stalled {int(t - last_progress)}s")
                            grace["cadence-vllm"], last_tokens, last_progress = t + GRACE_SECONDS, None, t + GRACE_SECONDS
                    except httpx.HTTPError:
                        await asyncio.to_thread(_restart, conn, "cadence-vllm", "not answering")
                        grace["cadence-vllm"] = t + GRACE_SECONDS
                if t > grace["cadence-asr"]:  # speech service: three failed health checks in a row
                    try:
                        ok = (await c.get(f"{ASR}/health", timeout=30)).status_code == 200
                    except httpx.HTTPError:
                        ok = False
                    asr_failures = 0 if ok else asr_failures + 1
                    if asr_failures >= 3:
                        await asyncio.to_thread(_restart, conn, "cadence-asr", "health check failed 3 times")
                        grace["cadence-asr"], asr_failures = t + 120, 0
            except Exception:  # noqa: BLE001 - the watchdog itself must never die
                log.exception("watchdog check failed")
