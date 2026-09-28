"""Budgets are per mission, and an ended mission is recoverable.

Field report (0.7.0): "Terminal state: max_seconds=3600. Start a new project
to continue." and then every message came back "Closed". Reproduced on
main: the turn, token and worker budgets were project-lifetime counters and
the time budget counted idle wall-clock time, so a project that ever
reached a budget was dead for every later mission.
"""
import time
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend


def scripted(n=40):
    return ScriptedBackend([T(progress_delta=f"step {i}") for i in range(n)])


class TestNewMissionIsUsableAfterABudgetEnds(unittest.TestCase):
    def test_turn_budget_is_per_mission(self):
        loop, _ = make_loop(backend=scripted(), config={"max_turns": 3})
        loop.set_mission("first", ["manual"])
        for _ in range(3):
            self.assertEqual(loop.run_user_turn("the export must be CSV")["status"], "ok")
        r = loop.run_user_turn("the export must be CSV")
        self.assertEqual(r["status"], "budget_exhausted")
        loop.set_mission("second", ["manual"])
        r = loop.run_user_turn("the export must be CSV")
        self.assertEqual(r["status"], "ok", r.get("said"))

    def test_token_budget_is_per_mission(self):
        loop, _ = make_loop(backend=scripted())
        loop.set_mission("first", ["manual"])
        loop.run_user_turn("the export must be CSV")
        per_turn = loop.state.snapshot["tokens_used"]
        self.assertGreater(per_turn, 0)
        # Room for one turn per mission, not two.
        loop.config["token_budget"] = int(per_turn * 1.5)
        r = loop.run_user_turn("the export must be CSV")
        self.assertEqual(r["status"], "budget_exhausted")
        loop.set_mission("second", ["manual"])
        r = loop.run_user_turn("the export must be CSV")
        self.assertEqual(r["status"], "ok", r.get("said", "")[:120])

    def test_turn_number_in_header_keeps_counting(self):
        # Mission budgets must not reset the header's turn counter: the
        # position sensor and the journal depend on it being monotonic.
        loop, _ = make_loop(backend=scripted())
        loop.set_mission("first", ["manual"])
        loop.run_user_turn("the export must be CSV")
        loop.set_mission("second", ["manual"])
        loop.run_user_turn("the export must be CSV")
        self.assertEqual(loop.state.snapshot["turn_count"], 2)

    def test_idle_time_does_not_end_a_mission(self):
        loop, _ = make_loop(backend=scripted(), config={"max_seconds": 3600})
        loop.set_mission("testing", ["manual"])
        # Set yesterday, never touched since.
        loop.state.snapshot["mission_start_ts"] = time.time() - 86400
        r = loop.run_user_turn("the export must be CSV")
        self.assertEqual(r["status"], "ok", r.get("said"))

    def test_steady_activity_still_ends_on_time(self):
        loop, _ = make_loop(backend=scripted(), config={"max_seconds": 3600})
        loop.set_mission("M", ["manual"])
        now = time.time()
        loop.state.snapshot["mission_start_ts"] = now - 7200
        for k in range(1, 24):  # an event every 5 minutes for two hours
            loop.state.events.append({"type": "tick", "data": {},
                                      "ts": now - 7200 + k * 300})
        r = loop.run_user_turn("the export must be CSV")
        self.assertEqual(r["status"], "budget_exhausted")

    def test_project_locked_by_old_rules_recovers(self):
        # A journal written by 0.7.0: lifetime turns over the limit and a
        # max_seconds terminal after idle time. Reloading under the fix must
        # leave a usable project, journaled, without a manual step.
        loop, home = make_loop(backend=scripted(), config={"max_turns": 3})
        loop.set_mission("testing", ["manual"])
        for _ in range(3):
            loop.run_user_turn("the export must be CSV")
        loop.state.record("budget_exhausted", {"reason": "max_seconds=3600"})
        loop2, _ = make_loop(home=home, backend=scripted(), config={"max_turns": 3})
        # The idle-clock terminal is not spent under active time: reopened
        # on load (the turn budget of THIS mission is spent, though).
        self.assertFalse(loop2.state.snapshot["terminal"])
        self.assertEqual(loop2.run_user_turn("the export must be CSV")["status"],
                         "budget_exhausted")
        loop2.set_mission("next", ["manual"])
        r = loop2.run_user_turn("the export must be CSV")
        self.assertEqual(r["status"], "ok", r.get("said"))

    def test_stale_terminal_lifts_on_load(self):
        # The session resume is computed right after load; it must not show
        # "terminal" for a mission the new accounting reopens.
        loop, home = make_loop(backend=scripted(), config={"max_seconds": 3600})
        loop.set_mission("testing", ["manual"])
        loop.state.record("budget_exhausted", {"reason": "max_seconds=3600"})
        loop2, _ = make_loop(home=home, backend=scripted(), config={"max_seconds": 3600})
        self.assertFalse(loop2.state.snapshot["terminal"])
        self.assertNotIn("terminal", loop2.status()["next_action"])

    def test_really_spent_budget_stays_ended_on_load(self):
        loop, home = make_loop(backend=scripted(), config={"max_turns": 1})
        loop.set_mission("testing", ["manual"])
        loop.run_user_turn("the export must be CSV")
        loop.run_user_turn("the export must be CSV")  # exhausts
        loop2, _ = make_loop(home=home, backend=scripted(), config={"max_turns": 1})
        self.assertTrue(loop2.state.snapshot["terminal"])

    def test_stale_idle_terminal_lifts_without_new_mission(self):
        loop, _ = make_loop(backend=scripted(), config={"max_seconds": 3600})
        loop.set_mission("testing", ["manual"])
        loop.state.record("budget_exhausted", {"reason": "max_seconds=3600"})
        r = loop.run_user_turn("the export must be CSV")
        self.assertEqual(r["status"], "ok", r.get("said"))
        self.assertTrue(any(e["type"] == "budget_recomputed"
                            for e in loop.state.events))

    def test_real_terminal_message_points_to_new_mission(self):
        loop, _ = make_loop(backend=scripted(), config={"max_turns": 1})
        loop.set_mission("testing", ["manual"])
        loop.run_user_turn("the export must be CSV")
        loop.run_user_turn("the export must be CSV")  # exhausts
        r = loop.run_user_turn("yes new mission we are about to start")
        self.assertEqual(r["status"], "closed")
        self.assertIn('Mission "testing" has ended', r["said"])
        self.assertIn("Start a new mission", r["said"])
        self.assertNotIn("new project", r["said"])

    def test_only_mission_endings_are_flagged(self):
        # The chat's Start a new mission button keys off mission_ended; a
        # per-turn pause (also status budget_exhausted) must not carry it.
        loop, _ = make_loop(backend=scripted(), config={"max_turns": 1})
        loop.set_mission("testing", ["manual"])
        r = loop.run_user_turn("the export must be CSV")
        self.assertNotIn("mission_ended", r)
        r = loop.run_user_turn("the export must be CSV")
        self.assertEqual((r["status"], r.get("mission_ended")), ("budget_exhausted", True))
        r = loop.run_user_turn("more")
        self.assertEqual((r["status"], r.get("mission_ended")), ("closed", True))
        # Per-turn pauses: same status, mission continues, no flag.
        loop2, _ = make_loop(backend=scripted())
        loop2.set_mission("m", ["manual"])
        loop2.backend.last_usage = {"input": 1000, "output": 50, "cached": 0}
        loop2._charge_tokens("contract", {}, "t1")
        loop2.config["turn_token_budget"] = 1000
        for reason in ("token_budget", "round_budget"):
            r = loop2._halt_turn(reason, "t1", 1, 3)
            self.assertEqual(r["status"], "budget_exhausted")
            self.assertNotIn("mission_ended", r)
        self.assertFalse(loop2.state.snapshot["terminal"])

    def test_worker_budget_is_per_mission(self):
        loop, _ = make_loop(backend=scripted(), config={"max_turns": 4})
        loop.set_mission("first", ["manual"])
        loop.spawn_worker("step a", ["a.txt"], {"max_turns": 4})
        with self.assertRaises(RuntimeError):
            loop.spawn_worker("step b", ["b.txt"], {"max_turns": 1})
        loop.set_mission("second", ["manual"])
        loop.spawn_worker("step c", ["c.txt"], {"max_turns": 2})  # must not raise


if __name__ == "__main__":
    unittest.main()
