import json
import unittest

from service import ccm, clinic, db


def fresh():
    conn = db.connect(":memory:")
    db.seed(conn)
    return conn


class Packets(unittest.TestCase):
    def setUp(self):
        self.conn = fresh()
        self.month = ccm.prev_month()

    def packet(self, pid):
        return ccm.build_packet(self.conn, pid, self.month)

    def test_seed_has_twenty_monitored_patients(self):
        self.assertEqual(len(ccm.monitored(self.conn)), 20)

    def test_qualifying_patient_gets_codes_with_evidence(self):
        r = self.packet("P-201")
        codes = {c["code"] for c in r["result"]["codes"]}
        self.assertTrue({"99490", "99454", "99457"} <= codes, r["result"]["gaps"])
        self.assertEqual(r["status"], "needs_review")
        self.assertTrue(all(c["evidence"] for c in r["result"]["checks"] if c["ok"]))

    def test_short_staff_minutes_is_a_gap_not_a_code(self):
        r = self.packet("P-206")  # seeded with 14 CCM minutes
        self.assertNotIn("99490", {c["code"] for c in r["result"]["codes"]})
        self.assertTrue(any("CCM clinical staff time" in g for g in r["result"]["gaps"]))

    def test_agent_minutes_never_billable(self):
        r = self.packet("P-206")
        self.assertGreater(r["result"]["agent"]["minutes"], 0)
        self.assertLess(r["result"]["staff_minutes"]["ccm"], 20)  # agent minutes did not lift it over 20

    def test_one_chronic_condition_not_billable(self):
        r = self.packet("P-208")
        self.assertEqual(r["status"], "not_billable")

    def test_missing_consent_not_billable(self):
        self.assertEqual(self.packet("P-212")["status"], "not_billable")

    def test_stale_care_plan_not_billable(self):
        self.assertEqual(self.packet("P-215")["status"], "not_billable")

    def test_too_few_device_days_drops_99454(self):
        r = self.packet("P-204")  # 9 reading days
        self.assertNotIn("99454", {c["code"] for c in r["result"]["codes"]})

    def test_99439_units_capped(self):
        ccm.log_staff_time(self.conn, "P-201", "S-6", 200, "long call", "ccm", False)
        # logged "now" (this month), so build for this month
        r = ccm.build_packet(self.conn, "P-201", db.now().strftime("%Y-%m"))
        units = {c["code"]: c["units"] for c in r["result"]["codes"]}
        self.assertLessEqual(units.get("99439", 0), 2)


class ClaimFlow(unittest.TestCase):
    def test_review_attest_submit_remit(self):
        conn = fresh()
        k = ccm.build_packet(conn, "P-201", ccm.prev_month())
        with self.assertRaises(RuntimeError):  # cannot submit before review + attestation
            from service import adapters
            adapters.submit_medicare_claim(conn, {"packet_id": k["id"]})
        ccm.review_packet(conn, k["id"], True, "coordinator")
        ap = ccm.attest_packet(conn, k["id"], "DR-CHEN", "slack:123.4")
        self.assertEqual(ap["state"], "confirmed")
        claim = db.one(conn.execute("SELECT * FROM claims WHERE packet_id=?", (k["id"],)))
        self.assertEqual(claim["status"], "submitted")
        conn.execute("UPDATE claims SET submitted_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(ccm.remittances_due(conn), [claim["id"]])
        self.assertAlmostEqual(json.loads(db.one(conn.execute("SELECT remit FROM claims"))["remit"])["paid"], round(claim["billed"] * 0.8, 2))

    def test_unreviewed_packet_cannot_be_attested(self):
        conn = fresh()
        k = ccm.build_packet(conn, "P-201", ccm.prev_month())
        ccm.attest_packet(conn, k["id"], "DR-CHEN", "x")
        self.assertEqual(db.one(conn.execute("SELECT status FROM ccm_packets WHERE id=?", (k["id"],)))["status"], "needs_review")

    def test_slack_confirm_attests_packet(self):
        from service import slack_sync
        conn = fresh()
        k = ccm.build_packet(conn, "P-201", ccm.prev_month())
        ccm.review_packet(conn, k["id"], True, "coordinator")
        cmds = slack_sync.parse_commands(f"CONFIRM {k['id']}")
        slack_sync.apply_command(conn, "DR-CHEN", *cmds[0], "171.1")
        self.assertEqual(db.one(conn.execute("SELECT status FROM ccm_packets WHERE id=?", (k["id"],)))["status"], "submitted")


if __name__ == "__main__":
    unittest.main()
