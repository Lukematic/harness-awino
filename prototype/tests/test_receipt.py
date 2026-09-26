"""Receipts: every closed story gets promise -> proof -> lesson, built from
the journal and registry, labelled honestly (PROVEN / SELF-CHECKED /
UNVERIFIED), with forecast vs actual per step."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import receipt  # noqa: E402
from receipt import (build_receipt, calibration_history, load_receipt,  # noqa: E402
                     parse_forecast, render_receipt_md)
from story import StoryStore, plan_story, story_close, story_start  # noqa: E402
from registry import Registry  # noqa: E402
from test_story import StoryTestBase  # noqa: E402

PLAN = dict(
    breakdown="Count fish from the camera feed.",
    surveyed="Manual tally sheets; slow.",
    user_guidance="User wants a daily number.",
    proposal="A (Honda): script + CSV [Likely]. B: dashboard. Pick A.",
    bugatti_brief="Live species classifier on the edge device.",
)


def ev(t, ts, **data):
    return {"type": t, "ts": ts, "data": data, "hash": f"h{ts}"}


class ParseForecastTest(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(parse_forecast("45m"), 2700)
        self.assertEqual(parse_forecast("2h"), 7200)
        self.assertEqual(parse_forecast("1h30m"), 5400)
        self.assertEqual(parse_forecast("1.5 hours"), 5400)
        self.assertEqual(parse_forecast("2 days"), 172800)
        self.assertEqual(parse_forecast("90 min"), 5400)

    def test_garbage_is_none(self):
        for s in ("", "soon", "a while", "0m"):
            self.assertIsNone(parse_forecast(s), s)


class ReceiptTest(StoryTestBase):
    def setUp(self):
        super().setUp()
        self.awd = Path(self._tmp) / "proj" / ".awino"
        self.awd.mkdir(parents=True)
        Registry(self.awd).ensure()

    def _story(self, forecasts=("30m", "1h")):
        st = story_start(self.awd, "Salmon counter", status="doing",
                         problem="No daily count.",
                         done_criteria=["counts.csv has today's row"])
        steps = [{"title": "Count script", "success": "prints a number",
                  "failure": "crashes", "forecast": forecasts[0]},
                 {"title": "CSV writer", "success": "row appended",
                  "failure": "file missing", "forecast": forecasts[1]}]
        plan_story(self.awd, st["id"], steps=steps, **PLAN)
        # Opened a while ago, so the journal events below fall inside it.
        import time
        store = StoryStore(self.awd)
        data = store._load()
        data[st["id"]]["created_ts"] = time.time() - 1000
        store._save(data)
        return store.get(st["id"])

    def _finish_tasks(self, sid, durations):
        """Mark the story's DAG tasks doing -> done with fake wall times."""
        reg = Registry(self.awd)
        tasks = sorted((t for t in reg.tasks() if t.get("story_id") == sid),
                       key=lambda t: t["step_index"])
        data = json.loads((reg.dir / "tasks.json").read_text())
        t0 = 1_000_000.0
        for t, dur in zip(tasks, durations):
            data[t["id"]]["state"] = "done"
            data[t["id"]]["evidence"] = [f"proof/{t['step_index']}.txt"]
            data[t["id"]]["history"] += [{"state": "doing", "ts": t0},
                                         {"state": "done", "ts": t0 + dur}]
            t0 += dur
        (reg.dir / "tasks.json").write_text(json.dumps(data))

    def test_forecast_is_captured_on_steps(self):
        st = self._story()
        self.assertEqual([s["forecast_s"] for s in st["steps"]], [1800, 3600])
        self.assertIn("forecast: 30m", st["approach"])

    def test_bad_forecast_is_refused(self):
        with self.assertRaisesRegex(ValueError, "forecast"):
            self._story(forecasts=("soon", "1h"))

    def test_proven_receipt_with_forecast_vs_actual(self):
        st = self._story()
        self._finish_tasks(st["id"], [1800, 3 * 3600])  # step 2 ran 3x
        t = st["created_ts"]
        events = [
            ev("tool_result", t - 100, tool="run_command",
               args={"cmd": "pytest"}, result={"exit_code": 0}),  # before
            ev("tool_result", t + 1, tool="write_file",
               args={"path": "count.py"}, result={"ok": True}),
            ev("approval_requested", t + 2),
            ev("tool_result", t + 3, tool="run_command",
               args={"cmd": "pytest"}, result={"exit_code": 1}),
            ev("verify_failed", t + 4, source="tests", reason="pytest exit 1"),
            ev("tool_result", t + 5, tool="run_command",
               args={"cmd": "pytest"}, result={"exit_code": 0}),
            ev("verify_passed", t + 6, worker_id="w-1", verdict=[
                {"criterion": "counts.csv has today's row",
                 "accomplished": "yes", "proof_link": "counts.csv"}]),
        ]
        closed = story_close(self.awd, st["id"], "Counter live",
                             events=events, chain_ok=True)
        self.assertEqual(closed["receipt_status"], "PROVEN")
        rc = load_receipt(self.awd, st["id"])
        self.assertEqual(rc["proof"]["files"], ["count.py"])
        self.assertEqual(rc["proof"]["approvals"], 1)
        chk = rc["proof"]["checks"][0]
        self.assertEqual((chk["runs"], chk["failures"], chk["last_exit"]),
                         (2, 1, 0))  # the pre-story run is outside the window
        self.assertEqual(rc["proof"]["criteria"][0]["proof"],
                         "named in verifier verdict")
        self.assertEqual(rc["lesson"]["ratio"], round((1800 + 10800) / 5400, 2))
        notes = " ".join(rc["lesson"]["notes"])
        self.assertIn("2.33x the forecast", notes)
        self.assertIn("'CSV writer' ran 3.0x", notes)
        self.assertIn("1 failed verification", notes)
        md = Path(closed["receipt_path"]).read_text()
        for s in ("Receipt: PROVEN", "| 2 | CSV writer | 1h | 3h 00m | done",
                  "`pytest` → exit 0 (pass); 2 run(s), 1 failed",
                  "chain intact", "Bugatti (pitched, not built)"):
            self.assertIn(s, md)

    def test_unverified_close_says_so(self):
        st = self._story()
        closed = story_close(self.awd, st["id"], "gave up", events=[])
        self.assertEqual(closed["receipt_status"], "UNVERIFIED")
        rc = load_receipt(self.awd, st["id"])
        self.assertEqual(rc["proof"]["criteria"][0]["proof"], "unproven")
        md = render_receipt_md(rc)
        self.assertIn("No check commands were run", md)
        self.assertIn("1 done criteria closed without proof", md)

    def test_self_check_is_not_labelled_proven(self):
        st = self._story()
        events = [ev("verify_passed", st["created_ts"] + 1,
                     worker_id="harness", verdict="pass")]
        closed = story_close(self.awd, st["id"], "done", events=events)
        self.assertEqual(closed["receipt_status"], "SELF-CHECKED")

    def test_criterion_the_verdict_skipped_is_unproven(self):
        st = self._story()
        StoryStore(self.awd).update(
            st["id"], done_criteria=["counts.csv has today's row",
                                     "README explains the count"])
        events = [ev("verify_passed", st["created_ts"] + 1, worker_id="w-1",
                     verdict=[{"criterion": "counts.csv has today's row",
                               "accomplished": "yes"}])]
        closed = story_close(self.awd, st["id"], "done", events=events)
        self.assertEqual(closed["receipt_status"], "PARTLY PROVEN")
        rc = load_receipt(self.awd, st["id"])
        self.assertEqual([c["proof"] for c in rc["proof"]["criteria"]],
                         ["named in verifier verdict", "unproven"])

    def test_untracked_time_is_not_shown_as_zero(self):
        st = self._story()
        closed = story_close(self.awd, st["id"], "done", events=[])
        md = Path(closed["receipt_path"]).read_text()
        self.assertIn("time not tracked", md)

    def test_calibration_history_feeds_the_next_plan(self):
        for dur in ([3600, 7200], [3600, 7200]):  # 2x a 30m+1h forecast
            st = self._story()
            self._finish_tasks(st["id"], dur)
            story_close(self.awd, st["id"], "ok", events=[])
        hist = calibration_history(self.awd)
        self.assertEqual([h["ratio"] for h in hist], [2.0, 2.0])
        from loop import Loop
        note = Loop._calibration_note(self.awd)
        self.assertIn("ran 2.0x their forecast", note)

    def test_receipt_failure_never_blocks_close(self):
        st = self._story()
        orig = receipt.build_receipt
        receipt.build_receipt = lambda *a, **k: 1 / 0
        try:
            closed = story_close(self.awd, st["id"], "done")
        finally:
            receipt.build_receipt = orig
        self.assertEqual(closed["status"], "done")
        self.assertIn("ZeroDivisionError", closed["receipt_error"])


class StoryPlanForecastToolTest(StoryTestBase):
    def test_fourth_column_is_the_forecast(self):
        from common import make_loop
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        loop.set_mission("Count salmon", ["manual"])
        r = loop._resolve_tool_fn("story_plan")(
            title="Salmon counter", problem="No count.",
            done_criteria="csv row", **PLAN,
            steps=("Count script | prints n | crashes | 30m\n"
                   "CSV writer | row appended | missing"))
        self.assertTrue(r.get("ok"), r)
        self.assertIn("1 step(s) have no time forecast", r["said"])
        st = StoryStore(awd).get(r["story_id"])
        self.assertEqual(st["steps"][0]["forecast"], "30m")
        self.assertNotIn("forecast", st["steps"][1])


if __name__ == "__main__":
    unittest.main()
