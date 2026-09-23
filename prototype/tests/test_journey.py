"""Phase D: end-to-end DEFINE->SHIP journey.

Tests that the phase transition table enforces the full journey in order
with no skips. The scripted backend proves the mechanism; the live 7B
journey is documented as future work (Phase D design).

Track G: VERIFY -> REVIEW additionally requires the verifier worker's
journaled pass verdict — exit 0 alone no longer unlocks REVIEW.
"""
import unittest

from tests.common import make_loop, drive_verification


class TestEndToEndJourney(unittest.TestCase):
    def test_full_journey_order(self):
        """DEFINE->PLAN->BUILD->VERIFY->REVIEW->SHIP in order, no skips."""
        loop, _ = make_loop()
        loop.set_mission("M", ["manual"])

        # The legal journey
        journey = ["PLAN", "BUILD", "VERIFY", "REVIEW", "SHIP"]
        for target in journey:
            # PLAN and BUILD go through approve_contract (elevator gate)
            if target == "PLAN":
                r = loop.approve_contract()
                self.assertEqual(r["status"], "ok")
            elif target == "BUILD":
                r = loop.approve_contract(["out.txt"])
                self.assertEqual(r["status"], "ok")
            elif target == "REVIEW":
                # Track G: VERIFY -> REVIEW needs the verifier's journaled
                # pass verdict — the builder's word is not enough.
                res = drive_verification(
                    loop, evidence_links={"manual (operator sign-off)":
                                          "tests/common.py"})
                self.assertTrue(res["passed"], res.get("said"))
                r = loop.request_phase(target, reason="verifier passed")
                self.assertEqual(r["status"], "ok", f"failed at {target}")
            else:
                r = loop.request_phase(target, reason="test journey")
                self.assertEqual(r["status"], "ok", f"failed at {target}")
            self.assertEqual(loop.state.snapshot["phase"], target)

        # Verify the event log shows the journey in order
        phases = []
        for e in loop.state.events:
            if e["type"] == "phase_changed":
                phases.append(e["data"]["phase"])
        for expected in journey:
            self.assertIn(expected, phases)
        idx = [phases.index(p) for p in journey]
        self.assertEqual(idx, sorted(idx), "phases visited out of order")

    def test_no_phase_skips(self):
        """Illegal jumps are refused; the journey cannot skip floors."""
        loop, _ = make_loop()
        loop.set_mission("M", ["manual"])
        # DEFINE -> BUILD is illegal (must go through PLAN)
        r = loop.request_phase("BUILD", reason="skip attempt")
        self.assertEqual(r["status"], "refused")
        self.assertEqual(loop.state.snapshot["phase"], "DEFINE")
        # DEFINE -> SHIP is illegal
        r = loop.request_phase("SHIP", reason="skip attempt")
        self.assertEqual(r["status"], "refused")


if __name__ == "__main__":
    unittest.main()
