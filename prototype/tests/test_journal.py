"""Phase B: effect journal — ordered effects with integrity verification."""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend


class TestEffectJournal(unittest.TestCase):
    def test_journal_lists_effects_in_order(self):
        backend = ScriptedBackend([
            T(tool_calls=[{"name": "list_dir", "args": {}}],
              progress_delta="listed"),
            T(tool_calls=[{"name": "read_file", "args": {"path": "x"}}],
              progress_delta="read"),
        ])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("go")
        loop.run_user_turn("go again")
        j = loop.effect_journal()
        self.assertEqual(len(j), 2)
        self.assertEqual(j[0]["tool"], "list_dir")
        self.assertEqual(j[1]["tool"], "read_file")
        self.assertLess(j[0]["seq"], j[1]["seq"])
        # every entry carries the integrity fields
        for e in j:
            self.assertIn("call_id", e)
            self.assertIn("idem_key", e)
            self.assertIn("mission_rev", e)

    def test_verify_journal_ok(self):
        backend = ScriptedBackend([
            T(tool_calls=[{"name": "list_dir", "args": {}}],
              progress_delta="listed"),
        ])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("go")
        ok, problems = loop.verify_journal()
        self.assertTrue(ok, problems)
        self.assertEqual(problems, [])

    def test_verify_journal_detects_forged_duplicate(self):
        """A forged duplicate tool_result (same idem_key, not reused) is flagged."""
        backend = ScriptedBackend([
            T(tool_calls=[{"name": "list_dir", "args": {}}],
              progress_delta="listed"),
        ])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("go")
        # forge: append a duplicate tool_result with the same idem_key
        orig = [e for e in loop.state.events if e["type"] == "tool_result"][0]
        dup = {"seq": 999, "id": "forged", "ts": 1.0, "type": "tool_result",
               "data": dict(orig["data"], call_id="t9.9", reused=False)}
        loop.state.events.append(dup)
        ok, problems = loop.verify_journal()
        self.assertFalse(ok)
        self.assertTrue(any("duplicate execution" in p for p in problems))

    def test_verify_journal_detects_orphan_result(self):
        backend = ScriptedBackend([T(progress_delta="x")])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("go")
        orphan = {"seq": 999, "id": "orphan", "ts": 1.0, "type": "tool_result",
                  "data": {"call_id": "t9.9", "tool": "list_dir",
                           "idem_key": "abc", "result": {}}}
        loop.state.events.append(orphan)
        ok, problems = loop.verify_journal()
        self.assertFalse(ok)
        self.assertTrue(any("no tool_called" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
