"""Doctor signatures and releases, verified by the service directly against Slack.

The agent can draft orders but cannot sign them. A doctor signs by replying `CONFIRM O-…` (or
`RELEASE D-…` for lab results, `CANCEL O-…`) in their DM with the Cadence bot. This module reads
that DM through the Slack Web API with the bot token and only accepts messages authored by the
provider's own Slack user id. The Slack message timestamp is stored as the signature reference.
"""
from __future__ import annotations

import logging
import os
import re

from . import clinic
from .db import emit, one, rows

log = logging.getLogger("cadence.slack")
COMMAND = re.compile(r"\b(CONFIRM|RELEASE|CANCEL)\b((?:[\s,]+[OD]-[\w-]+)+)", re.IGNORECASE)
IDS = re.compile(r"\b[OD]-[\w-]+", re.IGNORECASE)


def parse_commands(text: str) -> list[tuple[str, str]]:
    out = []
    for verb, ids in COMMAND.findall(text or ""):
        out += [(verb.upper(), i[0].upper() + i[1:].lower()) for i in IDS.findall(ids)]
    return out


def apply_command(conn, provider_id: str, verb: str, ref_id: str, signature_ref: str) -> str:
    if verb == "CONFIRM" and ref_id.startswith("O-"):
        o = clinic.sign_order(conn, ref_id, provider_id, signature_ref)
        return f"{ref_id}: {(o or {}).get('status', 'not found')}"
    if verb == "CANCEL" and ref_id.startswith("O-"):
        clinic.cancel_order(conn, ref_id, provider_id)
        return f"{ref_id}: cancelled"
    if verb == "RELEASE" and ref_id.startswith("D-"):
        clinic.release_document(conn, ref_id, provider_id)
        return f"{ref_id}: released"
    return f"{ref_id}: ignored"


def _client():
    from slack_sdk import WebClient
    return WebClient(token=os.environ["SLACK_BOT_TOKEN"], timeout=10)


def sync(conn, lock) -> list[str]:
    """Poll each provider's DM with the bot for new signature commands."""
    if not os.environ.get("SLACK_BOT_TOKEN"):
        return []
    done: list[str] = []
    client = _client()
    with lock:
        providers = rows(conn.execute("SELECT id, slack_user FROM providers WHERE slack_user IS NOT NULL"))
    for prov in providers:
        key = f"slack_cursor:{prov['id']}"
        with lock:
            cur = one(conn.execute("SELECT v FROM kv WHERE k=?", (key,)))
            ch = one(conn.execute("SELECT v FROM kv WHERE k=?", (f"slack_dm_channel:{prov['id']}",)))
        if not ch:
            continue  # learned on the first DM Cadence sends this doctor (adapters.slack_dm)
        channel = ch["v"]
        if not cur:  # first run: start from now so old messages aren't replayed
            latest = client.conversations_history(channel=channel, limit=1).get("messages") or [{"ts": "0"}]
            with lock:
                conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, latest[0]["ts"]))
            continue
        msgs = client.conversations_history(channel=channel, oldest=cur["v"], limit=50).get("messages") or []
        for m in sorted(msgs, key=lambda m: float(m["ts"])):
            if m.get("user") != prov["slack_user"] or m.get("bot_id"):
                continue  # only the doctor's own messages count as signatures
            results = []
            with lock:
                for verb, ref_id in parse_commands(m.get("text", "")):
                    results.append(apply_command(conn, prov["id"], verb, ref_id, m["ts"]))
                conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, m["ts"]))
            if results:
                done += results
                client.chat_postMessage(channel=channel, thread_ts=m["ts"], text="Recorded: " + "; ".join(results))
        if msgs:
            with lock:
                newest = max(msgs, key=lambda m: float(m["ts"]))["ts"]
                conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, newest))
    if done:
        with lock:
            emit(conn, None, "slack.sync", "service", "; ".join(done)[:300])
    return done
