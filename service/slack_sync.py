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
import threading

from . import clinic
from .db import emit, one, rows

log = logging.getLogger("cadence.slack")
COMMAND = re.compile(r"\b(CONFIRM|RELEASE|CANCEL)\b((?:[\s,]+[ODK]-[\w-]+)+)", re.IGNORECASE)
IDS = re.compile(r"\b[ODK]-[\w-]+", re.IGNORECASE)


def parse_commands(text: str) -> list[tuple[str, str]]:
    out = []
    for verb, ids in COMMAND.findall(text or ""):
        out += [(verb.upper(), i[0].upper() + i[1:].lower()) for i in IDS.findall(ids)]
    return out


def apply_command(conn, provider_id: str, verb: str, ref_id: str, signature_ref: str) -> str:
    if verb == "CONFIRM" and ref_id.startswith("O-"):
        o = clinic.sign_order(conn, ref_id, provider_id, signature_ref)
        return f"{ref_id}: {(o or {}).get('status', 'not found')}"
    if verb == "CONFIRM" and ref_id.startswith("K-"):
        from . import ccm
        ccm.attest_packet(conn, ref_id, provider_id, f"slack:{signature_ref}")
        k = one(conn.execute("SELECT status FROM ccm_packets WHERE id=?", (ref_id,)))
        return f"{ref_id}: {(k or {}).get('status', 'not found')}"
    if verb == "CANCEL" and ref_id.startswith("O-"):
        clinic.cancel_order(conn, ref_id, provider_id)
        return f"{ref_id}: cancelled"
    if verb == "RELEASE" and ref_id.startswith("D-"):
        clinic.release_document(conn, ref_id, provider_id)
        return f"{ref_id}: released"
    return f"{ref_id}: ignored"


def _handle_clip(conn, lock, client, channel, prov, f, m) -> None:
    """Download a doctor's voice/video clip with the bot token and run the GB10 visit pipeline."""
    from . import visits
    import httpx

    def reply(text):
        client.chat_postMessage(channel=channel, thread_ts=m["ts"], text=text)
    try:
        if (f.get("size") or 0) > visits.MAX_CLIP_BYTES:
            return reply("That clip is over 50 MB; please send a shorter one.")
        url = f.get("url_private_download") or f.get("url_private")
        audio = httpx.get(url, headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"}, timeout=120, follow_redirects=True).content
        visits.process_clip(conn, prov["id"], prov["slack_user"], f, audio, m.get("text", ""), m["ts"], reply)
    except Exception as e:  # noqa: BLE001 - tell the doctor rather than failing silently
        log.exception("clip failed")
        with lock:
            emit(conn, None, "voice.error", "service", f"Voice clip failed: {type(e).__name__}: {e}"[:300])
        reply(f"Sorry, I couldn't process that clip ({type(e).__name__}). The care team has been notified.")


def announce(conn, lock) -> None:
    """Send one 'on duty' DM per doctor so we learn the DM channel (the bot token has no im:write)."""
    from . import adapters
    if not os.environ.get("SLACK_BOT_TOKEN"):
        return
    with lock:
        for prov in rows(conn.execute("SELECT id FROM providers WHERE slack_user IS NOT NULL")):
            if not one(conn.execute("SELECT v FROM kv WHERE k=?", (f"slack_dm_channel:{prov['id']}",))):
                adapters.slack_dm(conn, prov["id"], "Cadence is on duty on the clinic GB10. Send me orders by patient ID, or a voice clip "
                                                    "of a visit, and I'll draft the note and orders for you to sign.")


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
            for f in m.get("files") or []:
                if (f.get("mimetype") or "").startswith(("audio/", "video/")):
                    threading.Thread(target=_handle_clip, args=(conn, lock, client, channel, prov, f, m), daemon=True).start()
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
