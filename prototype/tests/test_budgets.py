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

    def test_round_stall_warns_once_then_halts(self):
        # v0.6: identical in-turn rounds trip the stall breaker — the first
        # threshold hit journals round_stall_warning and grants exactly one
        # more round with the warning as feedback; a repeated identical
        # round after the warning halts the turn as stalled.
        turns = [T(plan=["Check"],
                    tool_calls=[{"name": "list_dir", "args": {"path": "."}}],
                    progress_delta="checking",
                    assumptions=["The directory listing is stable across "
                                 "reads in this test."])
                 for _ in range(10)]
        backend = ScriptedBackend(turns)
        loop, _ = make_loop(backend=backend, config={"round_stall_limit": 3})
        r = loop.run_user_turn("check the dir")
        self.assertEqual(r["status"], "stalled")
        warnings = [e for e in loop.state.events
                    if e["type"] == "round_stall_warning"]
        self.assertEqual(len(warnings), 1)
        halts = [e for e in loop.state.events
                 if e["type"] == "stalled"
                 and e["data"].get("reason") == "round_stall"]
        self.assertEqual(len(halts), 1)
        # Replay alignment: the halt sets the operator-wait state.
        self.assertTrue(loop.state.snapshot["awaiting_operator"])
        stall_fb = [c["feedback"] for c in backend.calls
                    if c["feedback"] and "STALL WARNING" in c["feedback"]]
        self.assertEqual(len(stall_fb), 1)

    def test_round_budget_halts_and_waits_for_operator(self):
        # v0.6: max_rounds_per_turn is a terminal halt for the turn — the
        # journal records round_budget_exhausted and the replayed state
        # waits for the operator (with reason + round).
        turns = [T(plan=["Step"],
                    tool_calls=[{"name": "list_dir",
                                 "args": {"path": f"d{i}"}}],
                    progress_delta=f"step {i}",
                    assumptions=["Each step lists a different path in "
                                 "this test."])
                 for i in range(10)]
        backend = ScriptedBackend(turns)
        loop, _ = make_loop(backend=backend,
                            config={"max_rounds_per_turn": 5,
                                    "round_stall_limit": 99})
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "budget_exhausted")
        ev = [e for e in loop.state.events
              if e["type"] == "round_budget_exhausted"]
        self.assertEqual(len(ev), 1)
        s = loop.state.snapshot
        self.assertTrue(s["awaiting_operator"])
        self.assertEqual(s["awaiting_operator_reason"], "round_budget")
        self.assertEqual(s["awaiting_operator_round"], ev[0]["data"]["round"])

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
