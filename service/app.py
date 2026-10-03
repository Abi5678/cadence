"""Cadence service: REST + SSE + static UI on 127.0.0.1:8080, MCP for Hermes on the docker0 bridge.

Run:  python -m service.app            (from the cadence/ folder)
Env:  CADENCE_MCP_TOKEN (required for MCP), CADENCE_MCP_HOST (default 172.17.0.1),
      CADENCE_TLS_DIR (default ~/cadence-tls; plain HTTP on 127.0.0.1 if no cert there),
      CADENCE_AGENT_BACKEND hermes|direct|off, HERMES_API_TOKEN, SLACK_BOT_TOKEN.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, clinic, db, mcp_tools, scheduler
from .db import LISTENERS, rows

log = logging.getLogger("cadence")
ROOT = Path(__file__).resolve().parent.parent

conn = db.connect()
db.seed(conn)
mcp_tools.bind(conn)
runner = agent.AgentRunner(conn)
subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()


def _fanout(ev: dict) -> None:
    for sub in list(subscribers):
        loop, q = sub
        try:
            loop.call_soon_threadsafe(q.put_nowait, ev)
        except RuntimeError:  # loop closed
            subscribers.discard(sub)


LISTENERS.append(_fanout)
api = FastAPI(title="Cadence")


@api.on_event("startup")
async def _start() -> None:
    runner.start()
    asyncio.create_task(scheduler.loop(conn, runner))


def locked(fn, *a, **kw):
    with mcp_tools.LOCK:
        try:
            return fn(*a, **kw)
        except clinic.ClinicError as e:
            raise HTTPException(409, str(e)) from e


@api.get("/")
def root():
    return RedirectResponse("/web/")


@api.get("/api/state")
def state():
    def q(sql, *a):
        return rows(conn.execute(sql, a))
    with mcp_tools.LOCK:
        dash = json.loads(mcp_tools.TOOLS["clinic_dashboard"]())
        return {
            **dash,
            "appointments": clinic.list_appointments(conn),
            "tasks": q("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50"),
            "approvals": [dict(a, payload=json.loads(a["payload"])) for a in q("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 80")],
            "messages": q("SELECT * FROM messages ORDER BY created_at DESC LIMIT 80"),
            "events": q("SELECT * FROM events ORDER BY seq DESC LIMIT 120"),
            "aftercare": q("SELECT * FROM aftercare ORDER BY due_at"),
            "inventory": clinic.inventory_status(conn),
            "staffing": clinic.staffing_overview(conn),
            "orders": clinic.list_orders(conn),
            "charges": clinic.billing_summary(conn),
            "patients": q("SELECT id,name,plan_id FROM patients"),
            "agent_backend": agent.backend(),
        }


@api.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    sub = (asyncio.get_running_loop(), q)
    subscribers.add(sub)

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subscribers.discard(sub)
    return StreamingResponse(gen(), media_type="text/event-stream")


class NewTask(BaseModel):
    title: str
    brief: str = ""
    kind: str = "coordinator"


@api.post("/api/tasks")
def new_task(body: NewTask):
    t = locked(clinic.create_task, conn, body.kind, body.title[:200], body.brief[:2000])
    runner.wake(t["id"])
    return t


@api.get("/api/tasks/{task_id}")
def task(task_id: str):
    return locked(clinic.get_task, conn, task_id)


class Decision(BaseModel):
    approve: bool
    by: str = "coordinator"


@api.post("/api/approvals/{aid}/decide")
def decide(aid: str, body: Decision):
    return locked(clinic.decide, conn, aid, body.approve, body.by)


@api.post("/api/sweep")
def sweep_now():
    return scheduler.run_sweep(conn, runner)


class PatientReply(BaseModel):
    patient_id: str
    body: str


@api.post("/api/sim/patient-reply")
def patient_reply(body: PatientReply):
    return locked(clinic.record_patient_reply, conn, body.patient_id, body.body)


class Vitals(BaseModel):
    patient_id: str
    kind: str
    value: float
    unit: str = ""


@api.post("/api/sim/vitals")
def vitals(body: Vitals):
    return locked(clinic.record_vitals, conn, body.patient_id, body.kind, body.value, body.unit)


class DoctorOrder(BaseModel):
    kind: str
    patient_id: str
    provider_id: str = "DR-CHEN"
    detail: str


@api.post("/api/sim/doctor-order")
def doctor_order(body: DoctorOrder):
    o = locked(clinic.record_signed_order, conn, body.kind, body.patient_id, body.provider_id, body.detail, "ui-sim")
    scheduler.run_sweep(conn, runner)
    return o


@api.post("/api/visits/{appt_id}/checkin")
def checkin(appt_id: str):
    return locked(clinic.checkin_patient, conn, appt_id)


@api.post("/api/visits/{appt_id}/complete")
def complete(appt_id: str):
    return locked(clinic.complete_visit, conn, appt_id)


@api.get("/api/health")
async def health():
    out = {"agent_backend": agent.backend(), "pending_approvals": mcp_tools.pending_count(conn)}
    async with httpx.AsyncClient(timeout=3) as c:
        for name, url, hdr in [("vllm", f"{agent.VLLM_URL}/models", {}),
                               ("hermes", f"{agent.HERMES_URL}/models",
                                {"Authorization": f"Bearer {os.environ.get('HERMES_API_TOKEN', '')}"})]:
            try:
                r = await c.get(url, headers=hdr)
                out[name] = "ok" if r.status_code == 200 else f"http {r.status_code}"
            except Exception as e:  # noqa: BLE001
                out[name] = f"down ({type(e).__name__})"
    return out


api.mount("/", StaticFiles(directory=ROOT, html=True), name="static")


class BearerAuth:
    """Constant-time bearer check in front of the MCP app."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            if not (auth.startswith(b"Bearer ") and hmac.compare_digest(auth[7:], self.token)):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def mcp_app(host: str, port: int):
    from mcp.server.transport_security import TransportSecuritySettings
    server = mcp_tools.build_mcp_server()
    sec = TransportSecuritySettings(allowed_hosts=[host, f"{host}:{port}", "127.0.0.1", f"127.0.0.1:{port}"],
                                    allowed_origins=[])
    return server.streamable_http_app(stateless_http=True, json_response=True, transport_security=sec, host=host)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    servers = [uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=int(os.environ.get("CADENCE_PORT", 8080)), log_level="warning"))]
    token = os.environ.get("CADENCE_MCP_TOKEN")
    if token:
        tls = Path(os.environ.get("CADENCE_TLS_DIR", Path.home() / "cadence-tls"))
        cert, key = tls / "server.pem", tls / "server-key.pem"
        host = os.environ.get("CADENCE_MCP_HOST", "172.17.0.1") if cert.exists() else "127.0.0.1"
        port = int(os.environ.get("CADENCE_MCP_PORT", 8443))
        ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)} if cert.exists() else {}
        servers.append(uvicorn.Server(uvicorn.Config(BearerAuth(mcp_app(host, port), token), host=host, port=port, log_level="warning", **ssl)))
        log.info("MCP on %s://%s:%s/mcp", "https" if ssl else "http", host, port)
    else:
        log.warning("CADENCE_MCP_TOKEN not set: MCP endpoint disabled")
    log.info("UI on http://127.0.0.1:%s/web/  agent backend: %s", servers[0].config.port, agent.backend())
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
