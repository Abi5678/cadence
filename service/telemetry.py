"""Live GB10 telemetry for mission control: GPU, vLLM token throughput, agent and device activity."""
from __future__ import annotations

import json
import re
import subprocess
import time

import httpx

from .db import iso, now, rows

_last = {"t": 0.0, "gen": None, "prompt": None, "tps": 0.0, "pps": 0.0}


def gpu() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,power.draw",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout.strip()
        name, util, temp, power = [x.strip() for x in out.split(",")][:4]
        num = lambda v: float(v) if re.match(r"^[\d.]+$", v) else None
        return {"name": name, "util": num(util), "temp_c": num(temp), "power_w": num(power)}
    except Exception:  # noqa: BLE001
        return {"name": "GB10", "util": None}


def vllm(url: str) -> dict:
    try:
        text = httpx.get(url.replace("/v1", "") + "/metrics", timeout=2).text
    except Exception:  # noqa: BLE001
        return {"up": False, "tokens_per_s": 0}
    total = lambda name: sum(float(m) for m in re.findall(rf"^{name}(?:{{[^}}]*}})?\s+([\d.e+]+)$", text, re.M))
    gen, prompt = total("vllm:generation_tokens_total"), total("vllm:prompt_tokens_total")
    running = total("vllm:num_requests_running")
    t = time.time()
    if _last["gen"] is not None and t - _last["t"] > 0.5:
        _last["tps"] = max(0.0, (gen - _last["gen"]) / (t - _last["t"]))
        _last["pps"] = max(0.0, (prompt - _last["prompt"]) / (t - _last["t"]))
    _last.update(t=t, gen=gen, prompt=prompt)
    return {"up": True, "tokens_per_s": round(_last["tps"], 1), "prompt_tokens_per_s": round(_last["pps"], 1),
            "generated_total": int(gen), "requests_running": int(running)}


def activity(conn) -> dict:
    hour_ago = iso(now().replace(microsecond=0) - __import__("datetime").timedelta(hours=1))
    count = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    rec = rows(conn.execute("SELECT timings FROM recordings WHERE timings IS NOT NULL ORDER BY created_at DESC LIMIT 1"))
    return {
        "events_last_hour": count("SELECT COUNT(*) FROM events WHERE created_at>=?", hour_ago),
        "agent_tool_calls_last_hour": count("SELECT COUNT(*) FROM events WHERE kind='agent.tool_call' AND created_at>=?", hour_ago),
        "tasks_running": count("SELECT COUNT(*) FROM tasks WHERE status='running'"),
        "tasks_done_today": count("SELECT COUNT(*) FROM tasks WHERE status='completed' AND created_at>=?", now().strftime("%Y-%m-%d")),
        "approvals_waiting": count("SELECT COUNT(*) FROM approvals WHERE state='prepared'"),
        "readings_last_hour": count("SELECT COUNT(*) FROM vitals WHERE recorded_at>=?", hour_ago),
        "alerts_last_hour": count("SELECT COUNT(*) FROM events WHERE kind='vitals.recorded' AND summary LIKE '%OUT OF RANGE%' AND created_at>=?", hour_ago),
        "ccm_ready_usd": round(sum(json.loads(r["result"])["billed"] for r in rows(conn.execute(
            "SELECT result FROM ccm_packets WHERE status IN ('needs_review','awaiting_attestation')"))), 2),
        "medicare_paid_usd": round(sum(json.loads(r["remit"])["paid"] for r in rows(conn.execute(
            "SELECT remit FROM claims WHERE status='paid'"))), 2),
        "last_voice_timings": json.loads(rec[0]["timings"]) if rec else None,
    }


def replay_summary(events: list[dict], vllm_url: str, model: str) -> str:
    """Ask the local model for a short night-shift handover. Falls back to counts."""
    counts: dict[str, int] = {}
    for e in events:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    notable = [e["summary"] for e in events if e["kind"] in ("vitals.recorded", "approval.prepared", "task.completed", "ccm.packet",
                                                             "voice.drafted", "order.signed", "claim.paid", "aftercare.answered")
               and ("OUT OF RANGE" in e["summary"] or e["kind"] != "vitals.recorded")][:60]
    try:
        r = httpx.post(f"{vllm_url}/chat/completions", timeout=90, json={
            "model": model, "temperature": 0.2, "max_tokens": 400, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": "Write a calm 5-bullet handover for the morning clinic team from these "
                                                       "overnight agent events. Facts only, no clinical advice. Mention what needs a human decision."},
                         {"role": "user", "content": json.dumps({"counts": counts, "notable": notable})}]})
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001
        return "\n".join(f"- {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:8])
