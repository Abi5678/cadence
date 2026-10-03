"""Slack doctor path with a stand-in Slack client: signatures and voice clips, no network."""
import os
import unittest
from unittest import mock

from service import clinic, db, slack_sync, visits

DOC, CH = "UDOC", "DTEST"


class FakeSlack:
    def __init__(self, messages):
        self.messages, self.posts = messages, []

    def conversations_history(self, channel, limit=50, oldest=None):
        return {"messages": [m for m in self.messages if not oldest or float(m["ts"]) > float(oldest)]}

    def chat_postMessage(self, channel, text, thread_ts=None):
        self.posts.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return {"ts": "9.9", "channel": channel}


def fresh():
    conn = db.connect(":memory:")
    db.seed(conn)
    conn.execute("UPDATE providers SET slack_user=? WHERE id='DR-CHEN'", (DOC,))
    conn.execute("INSERT INTO kv VALUES ('slack_dm_channel:DR-CHEN', ?)", (CH,))
    conn.execute("INSERT INTO kv VALUES ('slack_cursor:DR-CHEN', '100.0')")
    return conn


class SignaturesFromSlack(unittest.TestCase):
    def run_sync(self, conn, messages):
        fake = FakeSlack(messages)
        with mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}), mock.patch.object(slack_sync, "_client", return_value=fake):
            done = slack_sync.sync(conn, clinic and __import__("threading").RLock())
        return fake, done

    def test_doctor_confirm_signs_and_replies_in_thread(self):
        conn = fresh()
        o = clinic.draft_doctor_order(conn, DOC, "P-104", "rx", "Amoxi-synth 500 mg BID x7d")
        fake, done = self.run_sync(conn, [{"ts": "101.0", "user": DOC, "text": f"CONFIRM {o['id']}"}])
        row = db.one(conn.execute("SELECT status, signed_by, signature_ref FROM orders WHERE id=?", (o["id"],)))
        self.assertEqual((row["status"], row["signed_by"], row["signature_ref"]), ("pending_approval", "DR-CHEN", "101.0"))
        self.assertEqual(fake.posts[-1]["thread_ts"], "101.0")
        self.assertIn("Recorded", fake.posts[-1]["text"])

    def test_other_users_and_bots_cannot_sign(self):
        conn = fresh()
        o = clinic.draft_doctor_order(conn, DOC, "P-104", "lab", "CBC")
        self.run_sync(conn, [{"ts": "101.0", "user": "USOMEONE", "text": f"CONFIRM {o['id']}"},
                             {"ts": "102.0", "user": DOC, "bot_id": "B1", "text": f"CONFIRM {o['id']}"}])
        self.assertEqual(db.one(conn.execute("SELECT status FROM orders WHERE id=?", (o["id"],)))["status"], "awaiting_signature")

    def test_old_messages_are_not_replayed(self):
        conn = fresh()
        o = clinic.draft_doctor_order(conn, DOC, "P-104", "lab", "CBC")
        self.run_sync(conn, [{"ts": "99.0", "user": DOC, "text": f"CONFIRM {o['id']}"}])  # before the cursor
        self.assertEqual(db.one(conn.execute("SELECT status FROM orders WHERE id=?", (o["id"],)))["status"], "awaiting_signature")


class VoiceClipFromSlack(unittest.TestCase):
    def test_clip_is_downloaded_transcribed_drafted_and_answered_in_thread(self):
        conn = fresh()
        fake = FakeSlack([])
        asr = {"segments": [{"start": 0.0, "end": 3.0, "speaker": "speaker_0", "text": "Start Amoxi-synth 500 mg twice daily and get a CBC."},
                            {"start": 3.2, "end": 5.0, "speaker": "speaker_1", "text": "Okay, thank you doctor."}],
               "speakers": 2, "duration_s": 5.0, "timings": {"asr_s": 0.2, "diarization_s": 0.1}}
        ex = {"roles": {"speaker_0": "doctor", "speaker_1": "patient"}, "patient_id": None, "summary": "Antibiotic and CBC ordered.",
              "note": {"subjective": "", "objective": "", "assessment": "", "plan": "Amoxi-synth, CBC"},
              "orders": [{"kind": "rx", "detail": "Amoxi-synth 500 mg twice daily"}, {"kind": "lab", "detail": "CBC"}],
              "follow_up": {"when": "2 weeks", "reason": "recheck"}, "chronic_conditions": [], "patient_instructions": ""}
        get = mock.Mock(return_value=mock.Mock(content=b"RIFF....fake-audio"))
        with mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}), mock.patch("httpx.get", get), \
                mock.patch.object(visits, "transcribe", return_value=asr), mock.patch.object(visits, "extract", return_value=(ex, 1.5)):
            slack_sync._handle_clip(conn, __import__("threading").RLock(), fake, CH, {"id": "DR-CHEN", "slack_user": DOC},
                                    {"id": "F1", "name": "clip.mp4", "mimetype": "video/mp4", "size": 18, "url_private_download": "https://files/x"},
                                    {"ts": "105.0", "user": DOC, "text": "Visit with P-104"})
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer xoxb-test")
        orders = db.rows(conn.execute("SELECT kind, detail, status FROM orders WHERE source='slack' ORDER BY kind"))
        self.assertEqual([(o["kind"], o["detail"], o["status"]) for o in orders],
                         [("lab", "CBC", "awaiting_signature"), ("rx", "Amoxi-synth 500 mg twice daily", "awaiting_signature")])
        thread = [p for p in fake.posts if p["thread_ts"] == "105.0"]
        self.assertIn("Transcribing", thread[0]["text"])
        self.assertEqual(thread[-1]["text"], "Notes ready for review.")
        self.assertNotIn("Amoxi-synth", thread[-1]["text"])
        self.assertNotIn("CONFIRM", thread[-1]["text"])
        note = db.one(conn.execute("SELECT body FROM documents WHERE kind='visit_note'"))
        self.assertIn("Amoxi-synth", note["body"])
        self.assertEqual(db.one(conn.execute("SELECT status FROM recordings"))["status"], "drafted")

    def test_same_clip_twice_is_processed_once(self):
        conn = fresh()
        asr = {"segments": [], "speakers": 0, "duration_s": 1.0, "timings": {"asr_s": 0.1, "diarization_s": 0.1}}
        with mock.patch.object(visits, "transcribe", return_value=asr), mock.patch.object(visits, "extract", return_value=({}, 0.1)):
            visits.process_clip(conn, "DR-CHEN", DOC, {"id": "F2", "name": "a.m4a"}, b"x", "", "1", lambda t: None)
            self.assertIn("duplicate", visits.process_clip(conn, "DR-CHEN", DOC, {"id": "F2", "name": "a.m4a"}, b"x", "", "1", lambda t: None))


if __name__ == "__main__":
    unittest.main()
