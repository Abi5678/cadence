"""Wakes the business agent for a task.

Backends (CADENCE_AGENT_BACKEND):
- hermes: send the task to the Hermes gateway's OpenAI-compatible API (NemoClaw forwards it to
  127.0.0.1:8642). Hermes calls our tools itself over MCP.
- direct: fallback loop that talks to vLLM (:8000) and executes the same tool registry in-process.
  Used when the Hermes MCP route is unavailable (GB10-HERMES-PLAN §8).
- off: tasks are created but no model runs (unit tests, UI-only demo).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import httpx

from . import mcp_tools
from .db import emit
from .prompt import AGENT_INSTRUCTIONS

log = logging.getLogger("cadence.agent")

VLLM_URL = os.environ.get("CADENCE_VLLM_URL", "http://127.0.0.1:8000/v1")
VLLM_MODEL = os.environ.get("CADENCE_VLLM_MODEL", "nvidia/Qwen3.6-35B-A3B-NVFP4")
HERMES_URL = os.environ.get("CADENCE_HERMES_URL", "http://127.0.0.1:8642/v1")
MAX_STEPS = int(os.environ.get("CADENCE_AGENT_MAX_STEPS", "12"))


def backend() -> str:
    b = os.environ.get("CADENCE_AGENT_BACKEND")
    if b:
        return b
    return "hermes" if os.environ.get("HERMES_API_TOKEN") else "direct"


def task_prompt(task: dict) -> str:
    return (f"New Cadence task {task['id']} ({task['kind']}): {task['title']}\n"
            f"Brief: {task['brief'] or '(none)'}\n"
            "Call get_task first, act with the cadence tools, then finish_task.")


async def _tool_schemas() -> list[dict]:
    server = mcp_tools.build_mcp_server()
    return [{"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema}}
            for t in await server.list_tools()]


async def run_direct(conn, task: dict) -> str:
    tools = await _tool_schemas()
    messages = [{"role": "system", "content": AGENT_INSTRUCTIONS}, {"role": "user", "content": task_prompt(task)}]
    async with httpx.AsyncClient(timeout=180) as client:
        for step in range(MAX_STEPS):
            t0 = time.monotonic()
            r = await client.post(f"{VLLM_URL}/chat/completions", json={
                "model": VLLM_MODEL, "messages": messages, "tools": tools, "tool_choice": "auto",
                "temperature": 0.2, "max_tokens": 1024})
            r.raise_for_status()
            body = r.json()
            msg = body["choices"][0]["message"]
            usage = body.get("usage", {})
            emit(conn, task["id"], "agent.model_call", "model",
                 f"step {step + 1}: {usage.get('completion_tokens', '?')} tokens in {time.monotonic() - t0:.1f}s",
                 {"latency_s": round(time.monotonic() - t0, 2), "usage": usage, "backend": "direct"})
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v is not None})
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content") or ""
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                    fn = mcp_tools.TOOLS[name]
                    result = await asyncio.to_thread(fn, **args)
                except Exception as e:  # noqa: BLE001 - feed tool failures back to the model
                    result = json.dumps({"error": f"{type(e).__name__}: {e}"})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result[:6000]})
    return "step limit reached"


async def run_hermes(conn, task: dict) -> str:
    headers = {"Authorization": f"Bearer {os.environ['HERMES_API_TOKEN']}"}
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=600) as client:
        r = await client.post(f"{HERMES_URL}/chat/completions", headers=headers, json={
            "model": "hermes", "messages": [{"role": "user", "content": task_prompt(task)}]})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"].get("content") or ""
    emit(conn, task["id"], "agent.model_call", "hermes", f"Hermes turn finished in {time.monotonic() - t0:.1f}s",
         {"latency_s": round(time.monotonic() - t0, 2), "backend": "hermes"})
    return text


class AgentRunner:
    """Serializes agent runs through a queue so sweeps and events never overlap on one task."""

    def __init__(self, conn, concurrency: int = 2):
        self.conn = conn
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.inflight: set[str] = set()
        self.concurrency = concurrency

    def wake(self, task_id: str) -> None:
        """Thread-safe: tools may run in worker threads."""
        if task_id not in self.inflight:
            self.inflight.add(task_id)
            self.loop.call_soon_threadsafe(self.queue.put_nowait, task_id)

    async def worker(self) -> None:
        while True:
            task_id = await self.queue.get()
            try:
                await self._run(task_id)
            finally:
                self.inflight.discard(task_id)
                self.queue.task_done()

    async def _run(self, task_id: str) -> None:
        b = backend()
        if b == "off":
            return
        with mcp_tools.LOCK:
            task = json.loads(mcp_tools.TOOLS["get_task"](task_id))
        try:
            text = await (run_hermes(self.conn, task) if b == "hermes" else run_direct(self.conn, task))
            with mcp_tools.LOCK:
                emit(self.conn, task_id, "agent.reply", "agent", (text or "done")[:500])
                row = self.conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row and row["status"] == "running":
                    self.conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (task_id,))
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            # Model not up yet: leave the task running; the next sweep re-wakes it.
            with mcp_tools.LOCK:
                emit(self.conn, task_id, "agent.unavailable", "service", f"{b} backend unreachable ({type(e).__name__}); will retry")
        except Exception as e:  # noqa: BLE001 - keep the always-on loop alive
            log.exception("agent run failed")
            with mcp_tools.LOCK:
                emit(self.conn, task_id, "agent.error", "service", f"{b} backend failed: {type(e).__name__}: {e}"[:400])
                self.conn.execute("UPDATE tasks SET status='failed' WHERE id=?", (task_id,))

    def start(self) -> list[asyncio.Task]:
        self.loop = asyncio.get_running_loop()
        return [asyncio.create_task(self.worker()) for _ in range(self.concurrency)]
