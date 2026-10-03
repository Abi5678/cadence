import unittest

from service import clinic, db


def fresh():
    conn = db.connect(":memory:")
    db.seed(conn)
    return conn


def approvals(conn, action=None):
    q = "SELECT * FROM approvals" + (" WHERE action=?" if action else "")
    return db.rows(conn.execute(q, (action,) if action else ()))


class AppointmentLoop(unittest.TestCase):
    def test_sweep_requests_confirmations_once(self):
        conn = fresh()
        first = clinic.sweep(conn)
        self.assertIn("A-203", first["confirmations_requested"])
        second = clinic.sweep(conn)
        self.assertEqual(second["confirmations_requested"], [])
        sms = [a for a in approvals(conn, "patient_template_message") if "confirming your visit" in a["payload"]]
        self.assertEqual(len(sms), len(first["confirmations_requested"]))
        self.assertTrue(all(a["state"] == "confirmed" for a in sms))

    def test_cancel_backfills_from_waitlist_by_priority(self):
        conn = fresh()
        r = clinic.cancel_appointment(conn, "A-204")
        offer = clinic.offer_slot_to_waitlist(conn, r["open_slot"])
        self.assertEqual(offer["offered_to"], "P-107")  # priority 2 beats priority 3
        booked = clinic.book_appointment(conn, "P-107", r["open_slot"], "Earlier follow-up")
        self.assertEqual(booked["status"], "confirmed")
        w = db.one(conn.execute("SELECT status FROM waitlist WHERE id='W-1'"))
        self.assertEqual(w["status"], "placed")

    def test_offer_reserved_for_offered_patient(self):
        conn = fresh()
        slot = clinic.cancel_appointment(conn, "A-204")["open_slot"]
        clinic.offer_slot_to_waitlist(conn, slot)
        with self.assertRaises(clinic.ClinicError):
            clinic.book_appointment(conn, "P-103", slot, "jump the queue")

    def test_decline_moves_to_next_candidate(self):
        conn = fresh()
        slot = clinic.cancel_appointment(conn, "A-204")["open_slot"]
        clinic.offer_slot_to_waitlist(conn, slot)
        conn.execute("UPDATE appointments SET status='cancelled' WHERE id='A-203'")  # P-103 has no earlier visit
        nxt = clinic.decline_offer(conn, slot, "P-107")
        self.assertEqual(nxt["offered_to"], "P-103")

    def test_offer_skips_patients_already_booked_earlier(self):
        conn = fresh()
        conn.execute("UPDATE waitlist SET status='placed' WHERE id='W-1'")  # only P-103 left, booked Sun 09:00
        slot = clinic.cancel_appointment(conn, "A-204")["open_slot"]  # Sun 10:00, later than P-103's visit
        self.assertIsNone(clinic.offer_slot_to_waitlist(conn, slot)["offered_to"])

    def test_messages_use_clinic_time(self):
        conn = fresh()
        clinic.request_confirmation(conn, "A-203")
        body = db.one(conn.execute("SELECT body FROM messages WHERE ref='A-203'"))["body"]
        self.assertNotIn("UTC", body)
        self.assertIn(" at ", body)

    def test_reschedule(self):
        conn = fresh()
        booked = clinic.reschedule_appointment(conn, "A-203", "A-207")
        self.assertEqual(booked["patient_id"], "P-103")
        self.assertEqual(clinic._appt(conn, "A-203")["status"], "cancelled")


class Aftercare(unittest.TestCase):
    def test_due_aftercare_is_sent_and_flagged_answer_escalates(self):
        conn = fresh()
        out = clinic.sweep(conn)
        self.assertIn("AC-1", out["aftercare_sent"])
        ac = clinic.record_aftercare_answer(conn, "AC-1", "Yes, some redness and a fever since last night")
        self.assertEqual(ac["status"], "escalated")
        esc = approvals(conn, "escalate_to_doctor")
        self.assertEqual(len(esc), 1)
        self.assertEqual(esc[0]["state"], "confirmed")

    def test_fine_answer_closes(self):
        conn = fresh()
        clinic.sweep(conn)
        self.assertEqual(clinic.record_aftercare_answer(conn, "AC-1", "No, all good thanks")["status"], "closed")

    def test_out_of_range_vitals_escalate(self):
        conn = fresh()
        self.assertTrue(clinic.record_vitals(conn, "P-106", "spo2", 88, "%")["out_of_range"])
        self.assertFalse(clinic.record_vitals(conn, "P-106", "spo2", 97, "%")["out_of_range"])
        self.assertEqual(len(approvals(conn, "escalate_to_doctor")), 1)


class CheckinInsuranceBilling(unittest.TestCase):
    def test_checkin_active_coverage_creates_copay(self):
        conn = fresh()
        out = clinic.checkin_patient(conn, "A-201")
        self.assertEqual(out["eligibility"]["status"], "active")
        self.assertEqual(out["copay_charge"]["amount"], 30)

    def test_checkin_inactive_coverage_flags_desk(self):
        conn = fresh()
        out = clinic.checkin_patient(conn, "A-205")
        self.assertEqual(out["eligibility"]["status"], "inactive")
        self.assertIn("action_needed", out)

    def test_claim_needs_approval(self):
        conn = fresh()
        clinic.checkin_patient(conn, "A-201")
        out = clinic.complete_visit(conn, "A-201")
        ap = clinic.get_approval(conn, out["claim_approval"])
        self.assertEqual(ap["state"], "prepared")
        clinic.decide(conn, ap["id"], True, "coordinator")
        self.assertEqual(clinic.get_approval(conn, ap["id"])["state"], "confirmed")


class Orders(unittest.TestCase):
    def test_unsigned_rx_is_blocked(self):
        conn = fresh()
        with self.assertRaises(clinic.ClinicError):
            clinic.route_order(conn, "O-303")
        self.assertEqual(approvals(conn, "transmit_rx"), [])

    def test_signed_rx_waits_for_approval_then_transmits(self):
        conn = fresh()
        r = clinic.route_order(conn, "O-302")
        self.assertEqual(r["state"], "prepared")
        self.assertEqual(clinic.route_order(conn, "O-302")["status"], "pending_approval")  # no duplicate
        clinic.decide(conn, r["approval_id"], True, "coordinator")
        self.assertEqual(db.one(conn.execute("SELECT status FROM orders WHERE id='O-302'"))["status"], "transmitted")

    def test_rejected_order_is_not_sent(self):
        conn = fresh()
        r = clinic.route_order(conn, "O-301")
        clinic.decide(conn, r["approval_id"], False, "coordinator")
        self.assertEqual(db.one(conn.execute("SELECT status FROM orders WHERE id='O-301'"))["status"], "pending_approval")


class InventoryStaffing(unittest.TestCase):
    def test_low_stock_reorder(self):
        conn = fresh()
        self.assertIn("GLV-M", clinic.sweep(conn)["low_stock"])
        ap = clinic.draft_reorder(conn, "GLV-M")
        clinic.decide(conn, ap["id"], True, "coordinator")
        self.assertEqual(db.one(conn.execute("SELECT on_hand FROM inventory WHERE sku='GLV-M'"))["on_hand"], 46)

    def test_shift_fill_respects_overlap_and_role(self):
        conn = fresh()
        cands = [c["id"] for c in clinic.suggest_shift_fill(conn, "SH-1-RN2")]
        self.assertEqual(cands, ["S-2"])  # S-1 overlaps
        with self.assertRaises(clinic.ClinicError):
            clinic.propose_shift_fill(conn, "SH-1-RN2", "S-1")
        ap = clinic.propose_shift_fill(conn, "SH-1-RN2", "S-2")
        clinic.decide(conn, ap["id"], True, "coordinator")
        self.assertEqual(clinic.staffing_overview(conn)["open_shifts"], [])


class DoctorSlackOrders(unittest.TestCase):
    def setUp(self):
        self.conn = fresh()
        self.conn.execute("UPDATE providers SET slack_user='UDOC' WHERE id='DR-CHEN'")

    def test_draft_is_unsigned_until_doctor_confirms(self):
        from service import slack_sync
        o = clinic.draft_doctor_order(self.conn, "UDOC", "P-104", "rx", "Amoxi-synth 500 mg BID x7d")
        self.assertEqual(o["status"], "awaiting_signature")
        self.assertIsNone(o["signed_by"])
        with self.assertRaises(clinic.ClinicError):
            clinic.route_order(self.conn, o["id"])  # agent cannot route an unsigned draft
        cmds = slack_sync.parse_commands(f"confirm {o['id'].lower()} thanks")
        self.assertEqual(cmds, [("CONFIRM", o["id"])])
        slack_sync.apply_command(self.conn, "DR-CHEN", "CONFIRM", o["id"], "1700000000.0001")
        o2 = db.one(self.conn.execute("SELECT * FROM orders WHERE id=?", (o["id"],)))
        self.assertEqual((o2["status"], o2["signed_by"], o2["signature_ref"]), ("pending_approval", "DR-CHEN", "1700000000.0001"))

    def test_other_provider_cannot_sign(self):
        o = clinic.draft_doctor_order(self.conn, "UDOC", "P-104", "lab", "Lipid panel")
        clinic.sign_order(self.conn, o["id"], "DR-PATEL", "x")
        self.assertEqual(db.one(self.conn.execute("SELECT status FROM orders WHERE id=?", (o["id"],)))["status"], "awaiting_signature")

    def test_unknown_slack_user_rejected(self):
        with self.assertRaises(clinic.ClinicError):
            clinic.draft_doctor_order(self.conn, "UNOBODY", "P-104", "lab", "CBC")

    def test_lab_result_needs_release_before_sending(self):
        r = clinic.route_order(self.conn, "O-301")
        clinic.decide(self.conn, r["approval_id"], True, "receptionist")
        self.conn.execute("UPDATE orders SET result_due_at='2000-01-01T00:00:00+00:00' WHERE id='O-301'")
        self.assertEqual(clinic.lab_results_due(self.conn), ["O-301"])
        doc = [d for d in clinic.list_documents(self.conn, "P-104") if d["kind"] == "lab_result"][0]
        with self.assertRaises(clinic.ClinicError):
            clinic.send_document(self.conn, doc["id"])
        clinic.release_document(self.conn, doc["id"], "DR-CHEN")
        ap = clinic.send_document(self.conn, doc["id"])
        clinic.decide(self.conn, ap["id"], True, "receptionist")
        self.assertEqual(db.one(self.conn.execute("SELECT status FROM documents WHERE id=?", (doc["id"],)))["status"], "sent")


class Consent(unittest.TestCase):
    def test_documents_blocked_without_consent(self):
        conn = fresh()
        d = clinic.create_document(conn, "P-105", "statement", "Statement", "x")
        with self.assertRaises(clinic.ClinicError):
            clinic.send_document(conn, d["id"])

    def test_stop_withdraws_sms_and_confirmation_falls_back_to_call(self):
        conn = fresh()
        clinic.record_patient_reply(conn, "P-103", "STOP")
        self.assertFalse(clinic.has_consent(conn, "P-103", "sms"))
        self.assertEqual(clinic.request_confirmation(conn, "A-203")["confirmation"], "call_needed")

    def test_rx_transmit_creates_patient_copy(self):
        conn = fresh()
        r = clinic.route_order(conn, "O-302")
        clinic.decide(conn, r["approval_id"], True, "receptionist")
        self.assertTrue(any(d["kind"] == "rx_copy" for d in clinic.list_documents(conn, "P-104")))


if __name__ == "__main__":
    unittest.main()
