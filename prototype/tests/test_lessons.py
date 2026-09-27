"""Lessons: receipts -> merged lessons -> in the turn contract -> judged by
later receipts (escalate on recurrence, learned after clean closes)."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import lessons as L  # noqa: E402
from registry import Registry  # noqa: E402
from test_story import StoryTestBase  # noqa: E402


def receipt(sid="st-1", title="Salmon counter", overrun=True, failed=True,
            unproven=False, loop=False):
    steps = [{"title": "CSV writer", "forecast": "1h", "forecast_s": 3600,
              "actual_s": 10800 if overrun else 3000}]
    checks = [{"cmd": "pytest", "runs": 2, "failures": 1 if failed else 0,
               "last_exit": 0}]
    criteria = [{"criterion": "README explains it",
                 "proof": "unproven" if unproven else "named in verifier verdict"}]
    notes = ["Three-strike breaker fired 1 time(s)."] if loop else []
    return {"story": {"id": sid, "title": title},
            "proof": {"steps": steps, "checks": checks, "criteria": criteria},
            "lesson": {"notes": notes}}


class LessonTest(StoryTestBase):
    def setUp(self):
        super().setUp()
        self.awd = Path(self._tmp) / "proj" / ".awino"
        self.awd.mkdir(parents=True)

    def test_extracts_only_what_the_receipt_shows(self):
        kinds = sorted(c["kind"] for c in L.extract(
            receipt(unproven=True, loop=True)))
        self.assertEqual(kinds, ["check", "forecast", "loop", "proof"])
        self.assertEqual(L.extract(receipt(overrun=False, failed=False)), [])

    def test_same_scenario_merges_instead_of_duplicating(self):
        r1 = L.learn_from_receipt(self.awd, receipt("st-1"))
        self.assertEqual(sorted(r1["created"]),
                         ["check:pytest", "forecast:csv-writer"])
        r2 = L.learn_from_receipt(self.awd, receipt("st-2"))
        self.assertEqual(sorted(r2["reinforced"]),
                         ["check:pytest", "forecast:csv-writer"])
        store = L.load(self.awd)
        self.assertEqual(len(store), 2)
        self.assertEqual(store["check:pytest"]["seen"], 2)
        self.assertEqual([e["action"] for e in store["check:pytest"]["ledger"]],
                         ["create", "reinforce"])
        self.assertEqual(store["check:pytest"]["evidence"],
                         ["receipt:st-1", "receipt:st-2"])

    def test_recurrence_after_being_shown_escalates(self):
        L.learn_from_receipt(self.awd, receipt("st-1"))
        L.mark_shown(self.awd)
        r = L.learn_from_receipt(self.awd, receipt("st-2", failed=False))
        self.assertEqual(r["escalated"], ["forecast:csv-writer"])
        lines = L.index_lines(self.awd)
        self.assertTrue(lines[0].startswith("[ESCALATED] Steps like 'CSV writer'"))

    def test_clean_closes_graduate_and_recurrence_revives(self):
        L.learn_from_receipt(self.awd, receipt("st-1", overrun=False))
        L.mark_shown(self.awd)
        for i in range(L.LEARNED_AFTER - 1):
            r = L.learn_from_receipt(self.awd, receipt(f"c{i}", overrun=False,
                                                       failed=False))
            self.assertEqual(r["learned"], [])
        r = L.learn_from_receipt(self.awd, receipt("c-last", overrun=False,
                                                   failed=False))
        self.assertEqual(r["learned"], ["check:pytest"])
        self.assertEqual(L.index_lines(self.awd), [])
        r = L.learn_from_receipt(self.awd, receipt("again", overrun=False))
        self.assertEqual(r["revived"], ["check:pytest"])
        self.assertEqual(len(L.index_lines(self.awd)), 1)

    def test_unshown_lessons_never_graduate(self):
        L.learn_from_receipt(self.awd, receipt("st-1", overrun=False))
        for i in range(L.LEARNED_AFTER + 2):
            L.learn_from_receipt(self.awd, receipt(f"c{i}", overrun=False,
                                                   failed=False))
        self.assertEqual(L.load(self.awd)["check:pytest"]["status"], "live")

    def test_index_is_bounded(self):
        for i in range(L.INDEX_LIMIT + 4):
            L.learn_from_receipt(self.awd, {
                "story": {"id": f"s{i}"},
                "proof": {"checks": [{"cmd": f"check-{i}", "runs": 1,
                                      "failures": 1}]}})
        self.assertEqual(len(L.index_lines(self.awd)), L.INDEX_LIMIT)


class LessonsReachTheModelTest(StoryTestBase):
    """Story close -> lesson -> next session's turn contract."""

    def test_closed_story_lesson_is_in_the_next_contract(self):
        from common import make_loop
        from contract import compile_contract
        from story import story_start, story_close
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        st = story_start(awd, "Salmon counter", status="doing")
        t = st["created_ts"]
        events = [{"type": "tool_result", "ts": t + 0.001, "hash": "h",
                   "data": {"tool": "run_command", "args": {"cmd": "pytest"},
                            "result": {"exit_code": 1}}}]
        import time
        time.sleep(0.01)
        closed = story_close(awd, st["id"], "done", events=events)
        self.assertEqual(closed["lessons"]["created"], ["check:pytest"])
        self.assertEqual(loop.offer_lessons(mark_shown=True),
                         ["[x1] `pytest` failed 1 of 1 run(s) before the "
                          "story closed. Run it before claiming a step is "
                          "done."])
        block = compile_contract(loop.state)
        self.assertIn("## LESSONS", block)
        self.assertIn("`pytest` failed 1 of 1 run(s)", block)
        self.assertEqual(L.load(awd)["check:pytest"]["shown"], 1)
        # Journaled, so a restart rebuilds the same contract.
        self.assertTrue(any(e["type"] == "lessons_offered"
                            for e in loop.state.events))


if __name__ == "__main__":
    unittest.main()
