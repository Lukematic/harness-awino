"""Crash recovery: kill mid-effect -> new process reconciles.
No duplicate writes, no blind replay, no lost approvals."""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend, ScriptedJudge
from loop import Loop


class TestRecovery(unittest.TestCase):
    def _crashed_loop(self):
        backend = ScriptedBackend([
            T(plan=["Write y"], progress_delta="Planning.",
              assumptions=["Cause: the file is missing."]),
            T(plan=["Write y"],
              tool_calls=[{"name": "write_file",
                           "args": {"path": "y.txt", "content": "y-content"}}],
              progress_delta="Writing y.txt (needs approval).",
              assumptions=["Cause: the file does not exist yet."]),
        ])
        loop, home = make_loop(backend=backend, project="crash")
        loop.set_mission("Write things", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(["y.txt"])
        r = loop.run_user_turn("fix it now")
        assert r["status"] == "awaiting_approval", r
        ap = r["approvals"][0]
        # crash AFTER the tool started but BEFORE its result was recorded:
        # tool_called is in the log, tool_result is not.
        loop.state.record("tool_called",
                          {"call_id": "t0.9", "tool": "write_file",
                           "args": {"path": "x.txt", "content": "x-content"},
                           "idem_key": "deadbeef0001"})
        # (the y.txt approval from the paused turn is left pending)
        del loop
        loop2 = Loop(home, "crash", ScriptedBackend([]), ScriptedJudge())
        return loop2, ap

    def test_unknown_effect_pauses_no_blind_replay(self):
        loop2, _ = self._crashed_loop()
        s = loop2.state.snapshot
        self.assertEqual(s["awaiting_inspection"], "t0.9")
        unknowns = [e for e in loop2.state.events if e["type"] == "effect_unknown"]
        self.assertEqual(len(unknowns), 1)
        # recovery did NOT execute the write
        self.assertFalse((loop2.sandbox.root / "x.txt").exists())
        # user turns are blocked until inspection resolves
        r = loop2.run_user_turn("hello")
        self.assertEqual(r["status"], "awaiting_inspection")

    def test_approvals_survive_crash(self):
        loop2, ap = self._crashed_loop()
        pend = [a["id"] for a in loop2.state.snapshot["approvals"]
                if a["status"] == "pending"]
        self.assertIn(ap, pend)

    def test_paused_turn_routing_and_header_survive_restart(self):
        loop2, _ = self._crashed_loop()
        active = loop2.state.snapshot["active_turn"]
        self.assertIsNotNone(active)
        self.assertEqual(active["routing"]["mode"], "build")
        self.assertEqual(active["routing"]["chain"], ["first-principles"])
        self.assertEqual(active["routing"]["skills"],
                         ["repo", "code", "debug", "rpi", "rigor-iteration", "rigor-proof-cycles",
                                      "mode-software-engineer"])
        self.assertIn("[A.W.I.N.O.", active["header"])

    def test_resolve_not_applied_executes_exactly_once(self):
        loop2, _ = self._crashed_loop()
        r = loop2.resolve_inspection("t0.9", "not_applied")
        self.assertEqual(r["status"], "ok")
        self.assertEqual((loop2.sandbox.root / "x.txt").read_text(), "x-content")
        results = [e for e in loop2.state.events
                   if e["type"] == "tool_result" and e["data"]["call_id"] == "t0.9"]
        self.assertEqual(len(results), 1)
        self.assertIsNone(loop2.state.snapshot["awaiting_inspection"])

    def test_resolve_already_applied_verifies_no_duplicate(self):
        loop2, _ = self._crashed_loop()
        # the effect actually happened despite the missing result record
        (loop2.sandbox.root / "x.txt").write_text("x-content")
        r = loop2.resolve_inspection("t0.9", "already_applied")
        self.assertEqual(r["status"], "ok")
        results = [e for e in loop2.state.events
                   if e["type"] == "tool_result" and e["data"]["call_id"] == "t0.9"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["data"]["result"].get("verified"))
        # content untouched, still exactly one write
        self.assertEqual((loop2.sandbox.root / "x.txt").read_text(), "x-content")

    def test_completed_effects_not_replayed(self):
        loop2, _ = self._crashed_loop()
        n_before = len(loop2.state.events)
        # a clean reload with no orphans reconciles nothing
        loop2.resolve_inspection("t0.9", "not_applied")
        from loop import Loop as L
        loop3 = L(loop2.home, "crash", ScriptedBackend([]), ScriptedJudge())
        unknowns = [e for e in loop3.state.events if e["type"] == "effect_unknown"]
        self.assertEqual(len(unknowns), 1)  # only the original, already resolved
        self.assertIsNone(loop3.state.snapshot["awaiting_inspection"])
        self.assertEqual((loop3.sandbox.root / "x.txt").read_text(), "x-content")
        self.assertGreaterEqual(len(loop3.state.events), n_before)


if __name__ == "__main__":
    unittest.main()
