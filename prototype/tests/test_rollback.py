"""Phase E: rollback — truncate to a sequence number, rebuild by replay."""
import unittest
from pathlib import Path

from tests.common import make_loop, T
from backends import ScriptedBackend


class TestRollback(unittest.TestCase):
    def test_rollback_truncates_and_rebuilds(self):
        backend = ScriptedBackend([
            T(progress_delta="first"),
            T(progress_delta="second"),
            T(progress_delta="third"),
        ])
        loop, home = make_loop(backend=backend)
        loop.set_mission("M", ["manual"])
        loop.run_user_turn("one")
        loop.run_user_turn("two")
        loop.run_user_turn("three")
        # find the seq after the first turn_completed
        seqs = [e["seq"] for e in loop.state.events
                if e["type"] == "turn_completed"]
        self.assertEqual(len(seqs), 3)
        target_seq = seqs[0]
        turn_count_before = loop.state.snapshot["turn_count"]
        self.assertEqual(turn_count_before, 3)

        r = loop.rollback(target_seq)
        self.assertEqual(r["status"], "ok")
        # snapshot rebuilt: turn_count back to 1
        self.assertEqual(loop.state.snapshot["turn_count"], 1)
        # later progress is gone
        deltas = [p["delta"] for p in loop.state.snapshot["progress"]]
        self.assertNotIn("third", str(deltas))
        self.assertNotIn("second", str(deltas))
        self.assertIn("first", str(deltas))
        # backup exists
        backups = list(Path(home, "projects", "p1").glob("events.jsonl.bak.*"))
        self.assertTrue(backups, "no backup file")
        # rollback event recorded
        self.assertTrue(any(e["type"] == "rollback"
                            for e in loop.state.events))

    def test_rollback_invalid_seq(self):
        # rollback on a fresh loop (no events file yet) returns error
        loop, _ = make_loop()
        r = loop.rollback(999999)
        self.assertEqual(r["status"], "error")
        # after a turn, rollback to future seq is a no-op (keeps all)
        backend = ScriptedBackend([T(progress_delta="x")])
        loop2, _ = make_loop(backend=backend)
        loop2.set_mission("M", ["manual"])
        loop2.run_user_turn("hi")
        count_before = len(loop2.state.events)
        r = loop2.rollback(999999)
        self.assertEqual(r["status"], "ok")
        # all events kept (plus the rollback event)
        self.assertGreaterEqual(len(loop2.state.events), count_before)


if __name__ == "__main__":
    unittest.main()
