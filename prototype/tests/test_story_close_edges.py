"""Story close edge cases: a second close is refused, and receipt failures
are reported as what they are over the sidecar."""
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


class StoryReCloseTest(StoryTestBase):
    def setUp(self):
        super().setUp()
        self.awd = Path(self._tmp) / "proj" / ".awino"
        self.awd.mkdir(parents=True)
        Registry(self.awd).ensure()

    def _story(self):
        st = story_start(self.awd, "Salmon counter", status="doing",
                         done_criteria=["counts.csv has today's row"])
        store = StoryStore(self.awd)
        data = store._load()
        data[st["id"]]["created_ts"] = time.time() - 1000
        store._save(data)
        return store.get(st["id"])

    def test_closing_a_closed_story_is_refused_and_learns_nothing(self):
        st = self._story()
        events = [ev("tool_result", st["created_ts"] + 1, tool="run_command",
                     args={"cmd": "pytest"}, result={"exit_code": 1})]
        first = story_close(self.awd, st["id"], "Counter live",
                            events=events)
        self.assertEqual(L.load(self.awd)["check:pytest"]["seen"], 1)
        closed_ts = first["closed_ts"]
        with self.assertRaisesRegex(ValueError, "already closed"):
            story_close(self.awd, st["id"], "again", events=events)
        self.assertEqual(L.load(self.awd)["check:pytest"]["seen"], 1)
        again = StoryStore(self.awd).get(st["id"])
        self.assertEqual((again["closed_ts"], again["outcome"]),
                         (closed_ts, "Counter live"))


class SidecarReceiptEdgeTest(StoryTestBase):
    def _sidecar(self):
        import awino_sidecar
        sc = awino_sidecar.Sidecar()
        sc.workspace = Path(self._tmp) / "proj"
        (sc.workspace / ".awino").mkdir(parents=True)
        Registry(sc.workspace / ".awino").ensure()
        return sc

    def test_receipt_build_error_is_not_reported_as_unknown_story(self):
        sc = self._sidecar()
        awd = sc.workspace / ".awino"
        st = story_start(awd, "Counter", status="doing")
        import receipt as R
        orig = R.build_receipt

        def boom(*a, **k):
            raise KeyError("ts")
        R.build_receipt = boom
        try:
            out = sc._cmd_receipt({"id": st["id"]})
        finally:
            R.build_receipt = orig
        self.assertEqual(out["status"], "error")
        self.assertNotIn("no story", out["said"])
        self.assertIn("receipt", out["said"])

    def test_close_succeeds_when_the_receipt_cannot_be_built(self):
        sc = self._sidecar()
        awd = sc.workspace / ".awino"
        st = story_start(awd, "Counter", status="doing")
        import receipt as R
        orig = R.build_receipt
        R.build_receipt = lambda *a, **k: 1 / 0
        try:
            out = sc._cmd_story_close({"id": st["id"], "outcome": "done"})
        finally:
            R.build_receipt = orig
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("ZeroDivisionError", out.get("receipt_error", ""))
        self.assertEqual(StoryStore(awd).get(st["id"])["status"], "done")

    def test_reclose_is_an_error_not_a_second_receipt(self):
        sc = self._sidecar()
        awd = sc.workspace / ".awino"
        st = story_start(awd, "Counter", status="doing")
        self.assertEqual(sc._cmd_story_close(
            {"id": st["id"], "outcome": "done"})["status"], "ok")
        out = sc._cmd_story_close({"id": st["id"], "outcome": "again"})
        self.assertEqual(out["status"], "error")
        self.assertIn("already closed", out["said"])

    def test_lessons_command_survives_malformed_entries(self):
        sc = self._sidecar()
        p = sc.workspace / ".awino" / "lessons" / "lessons.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"junk": 3, "ok": {"key": "ok", "text": "x",
                                                   "status": "live",
                                                   "seen": "2"}}))
        out = sc._cmd_lessons({})
        self.assertEqual([l["key"] for l in out["lessons"]], ["ok"])


if __name__ == "__main__":
    unittest.main()
