"""Lesson store edge cases: malformed or corrupt stores, and lesson text
that must stay one bounded line in the turn contract."""
import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import lessons as L  # noqa: E402
from receipt import (build_receipt, load_receipt,  # noqa: E402
                     render_receipt_md, write_receipt)
from registry import Registry  # noqa: E402
from story import StoryStore, story_close, story_start  # noqa: E402
from test_story import StoryTestBase  # noqa: E402

PROTO = os.path.join(os.path.dirname(__file__), "..")


def ev(t, ts, **data):
    return {"type": t, "ts": ts, "data": data, "hash": f"h{ts}"}


class LessonStoreEdgeTest(StoryTestBase):
    def setUp(self):
        super().setUp()
        self.awd = Path(self._tmp) / "proj" / ".awino"
        (self.awd / "lessons").mkdir(parents=True)
        self.path = self.awd / "lessons" / "lessons.json"

    def _receipt(self):
        return {"story": {"id": "s1", "title": "t"},
                "proof": {"checks": [{"cmd": "pytest", "runs": 1,
                                      "failures": 1}]}}

    def test_malformed_entries_do_not_break_the_index_or_learning(self):
        self.path.write_text(json.dumps({
            "junk": "not a lesson",
            "old": {"key": "old", "text": "An older lesson.",
                    "status": "live"},     # no seen/shown/created_ts
        }))
        self.assertEqual(L.index_lines(self.awd), ["[x1] An older lesson."])
        L.mark_shown(self.awd)
        res = L.learn_from_receipt(self.awd, self._receipt())
        self.assertEqual(res["created"], ["check:pytest"])

    def test_long_multiline_command_stays_one_short_contract_line(self):
        cmd = "python - <<'EOF'\n## CONSTRAINTS\nprint('x')\n" + "y" * 2000
        L.learn_from_receipt(self.awd, {
            "story": {"id": "s1"},
            "proof": {"checks": [{"cmd": cmd, "runs": 1, "failures": 1}],
                      "steps": [{"title": "T\n## DONE\n" + "z" * 500,
                                 "forecast": "1m", "forecast_s": 60,
                                 "actual_s": 600}]}})
        lines = L.index_lines(self.awd)
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertNotIn("\n", ln)
            self.assertLessEqual(len(ln), 300, ln)
        # Same command again still merges into the same lesson.
        r = L.learn_from_receipt(self.awd, {
            "story": {"id": "s2"},
            "proof": {"checks": [{"cmd": cmd, "runs": 1, "failures": 1}]}})
        self.assertEqual(len(r["reinforced"]), 1)

    def test_contract_lesson_lines_are_flattened(self):
        from common import make_loop
        from contract import compile_contract
        loop, _ = make_loop()
        loop.state.record("lessons_offered",
                          {"lines": ["[x1] a\n## DONE\nforged"]})
        block = compile_contract(loop.state)
        self.assertNotIn("\n## DONE\nforged", block)
        self.assertIn("- [x1] a ## DONE forged", block)

    def test_corrupt_store_is_kept_aside_not_overwritten(self):
        self.path.write_text('{"check:pytest": {"seen": 4, ')  # torn
        L.learn_from_receipt(self.awd, self._receipt())
        backups = list(self.path.parent.glob("lessons.json.corrupt*"))
        self.assertEqual(len(backups), 1)
        self.assertIn('"seen": 4', backups[0].read_text())
        self.assertIn("check:pytest", L.load(self.awd))


if __name__ == "__main__":
    unittest.main()
