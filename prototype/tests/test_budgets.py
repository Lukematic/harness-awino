"""Budgets: max turns is terminal; stall detection escalates."""
import time
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend


class TestBudgets(unittest.TestCase):
    def test_max_turns_is_terminal(self):
        backend = ScriptedBackend([T(progress_delta=f"turn {i}") for i in range(5)])
        loop, _ = make_loop(backend=backend, config={"max_turns": 2})
        self.assertEqual(loop.run_user_turn("a")["status"], "ok")
        self.assertEqual(loop.run_user_turn("b")["status"], "ok")
        r = loop.run_user_turn("c")
        self.assertEqual(r["status"], "budget_exhausted")
        self.assertTrue(loop.state.snapshot["terminal"])
        # further turns stay closed
        r = loop.run_user_turn("d")
        self.assertEqual(r["status"], "closed")

    def test_stall_detection_escalates(self):
        backend = ScriptedBackend([T(plan=["Wait"], progress_delta="same")
                                   for _ in range(10)])
        loop, _ = make_loop(backend=backend, config={"stall_limit": 3})
        statuses = [loop.run_user_turn(f"m{i}")["status"] for i in range(4)]
        self.assertEqual(statuses[:3], ["ok", "ok", "ok"])
        self.assertEqual(statuses[3], "stalled")
        self.assertTrue(loop.state.snapshot["awaiting_operator"])
        self.assertTrue(any(e["type"] == "stalled" for e in loop.state.events))

    def test_token_budget_tracked(self):
        backend = ScriptedBackend([T(progress_delta="x")])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("hi")
        self.assertGreater(loop.state.snapshot["tokens_used"], 0)

    def test_time_budget_is_terminal(self):
        """Phase B: wall-clock budget exhaustion is terminal."""
        backend = ScriptedBackend([T(progress_delta="x")])
        loop, _ = make_loop(backend=backend, config={"max_seconds": 3600})
        loop.set_mission("M", ["manual"])
        # backdate the mission start to force exhaustion
        loop.state.snapshot["mission_start_ts"] = time.time() - 7200
        # persist the backdated ts via a record so reload sees it
        r = loop.run_user_turn("hi")
        self.assertEqual(r["status"], "budget_exhausted")
        self.assertTrue(any(e["type"] == "budget_exhausted"
                            for e in loop.state.events))

    def test_budgets_view(self):
        """Phase B: budgets() reports used/limit/remaining."""
        backend = ScriptedBackend([T(progress_delta="x")])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("M", ["manual"])
        loop.run_user_turn("hi")
        b = loop.budgets()
        self.assertEqual(b["turns"]["used"], 1)
        self.assertEqual(b["turns"]["limit"], 50)
        self.assertEqual(b["turns"]["remaining"], 49)
        self.assertGreater(b["tokens"]["used"], 0)
        self.assertGreaterEqual(b["seconds"]["remaining"], 0)


if __name__ == "__main__":
    unittest.main()
