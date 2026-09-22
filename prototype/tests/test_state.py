"""State: event sourcing, snapshot derivation, crash recovery of the log."""
import json
import unittest

from tests.common import make_loop
from state import ProjectState


class TestEventSourcing(unittest.TestCase):
    def test_append_and_reload_roundtrip(self):
        loop, home = make_loop()
        loop.set_mission("Fix bug", ["manual"])
        loop.state.record("progress_recorded", {"turn_id": "t1", "delta": "did x"})
        n_events = len(loop.state.events)

        reloaded = ProjectState(home, "p1")
        self.assertEqual(len(reloaded.events), n_events)
        self.assertEqual(reloaded.snapshot["mission"]["text"], "Fix bug")
        self.assertEqual(reloaded.snapshot["phase"], "DEFINE")
        self.assertEqual(reloaded.snapshot["progress"][-1]["delta"], "did x")

    def test_snapshot_persists_atomically(self):
        loop, home = make_loop()
        loop.set_mission("Fix bug", ["manual"])
        loop.state.persist_snapshot()
        snap = json.loads((loop.state.snapshot_path).read_text())
        self.assertEqual(snap["mission"]["text"], "Fix bug")

    def test_torn_tail_line_is_repaired_not_crash(self):
        loop, home = make_loop()
        loop.set_mission("Fix bug", ["manual"])
        # Simulate a torn final write from a crash.
        with open(loop.state.events_path, "ab") as f:
            f.write(b'{"seq": 99, "id": "abc", "ts": 1.0, "type": "user_mess')
        reloaded = ProjectState(home, "p1")
        self.assertTrue(reloaded.repaired_tail)
        # Earlier events intact.
        self.assertEqual(reloaded.snapshot["mission"]["text"], "Fix bug")
        # Log is still appendable.
        reloaded.record("progress_recorded", {"turn_id": "t9", "delta": "ok"})
        self.assertEqual(reloaded.snapshot["progress"][-1]["delta"], "ok")

    def test_revision_history_append_only(self):
        """Phase B: mission revisions are immutable history."""
        loop, home = make_loop()
        loop.set_mission("First mission", ["manual"])
        loop.set_mission("Second mission", ["manual"])
        hist = loop.state.snapshot["revision_history"]
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["revision"], 1)
        self.assertEqual(hist[0]["text"], "First mission")
        self.assertEqual(hist[1]["revision"], 2)
        self.assertEqual(hist[1]["text"], "Second mission")
        # past revision text is unchanged by the new mission
        self.assertNotEqual(hist[0]["text"], hist[1]["text"])
        # history survives reload from disk
        reloaded = ProjectState(home, "p1")
        self.assertEqual(len(reloaded.snapshot["revision_history"]), 2)
        self.assertEqual(reloaded.snapshot["revision_history"][0]["text"],
                         "First mission")


if __name__ == "__main__":
    unittest.main()
