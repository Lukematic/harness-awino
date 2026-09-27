"""Receipt edge cases: honesty of the status label, criterion matching,
markdown safety, encoding and malformed inputs."""
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


class ReceiptEdgeTest(StoryTestBase):
    def setUp(self):
        super().setUp()
        self.awd = Path(self._tmp) / "proj" / ".awino"
        self.awd.mkdir(parents=True)
        Registry(self.awd).ensure()

    def _story(self, criteria=("counts.csv has today's row",)):
        st = story_start(self.awd, "Salmon counter", status="doing",
                         problem="No daily count.",
                         done_criteria=list(criteria))
        store = StoryStore(self.awd)
        data = store._load()
        data[st["id"]]["created_ts"] = time.time() - 1000
        store._save(data)
        return store.get(st["id"])

    # -- status honesty ---------------------------------------------------
    def test_pass_followed_by_a_failure_is_not_proven(self):
        st = self._story()
        t = st["created_ts"]
        events = [
            ev("verify_passed", t + 1, worker_id="w-1", verdict=[
                {"criterion": "counts.csv has today's row",
                 "accomplished": "yes"}]),
            ev("verify_failed", t + 2, source="tests",
               reason="test command failed: pytest (exit 1)"),
        ]
        rc = build_receipt(self.awd, st["id"], events=events)
        self.assertEqual(rc["status"], "UNVERIFIED")
        self.assertEqual(rc["proof"]["criteria"][0]["proof"], "unproven")
        self.assertTrue(any("after the last pass" in n
                            for n in rc["lesson"]["notes"]),
                        rc["lesson"]["notes"])

    # -- criterion matching -----------------------------------------------
    def test_criterion_is_not_matched_across_two_verdict_criteria(self):
        st = self._story(criteria=["passes lint"])
        events = [ev("verify_passed", st["created_ts"] + 1, worker_id="w-1",
                     verdict=[{"criterion": "pytest passes",
                               "accomplished": "yes"},
                              {"criterion": "lint clean",
                               "accomplished": "yes"}])]
        rc = build_receipt(self.awd, st["id"], events=events)
        self.assertEqual(rc["proof"]["criteria"][0]["proof"], "unproven")

    def test_criterion_match_ignores_case_punctuation_and_spacing(self):
        st = self._story(criteria=["counts.csv has today's row."])
        events = [ev("verify_passed", st["created_ts"] + 1, worker_id="w-1",
                     verdict=[{"criterion": "counts.csv  has today's row",
                               "accomplished": "yes"}])]
        rc = build_receipt(self.awd, st["id"], events=events)
        self.assertEqual(rc["proof"]["criteria"][0]["proof"],
                         "named in verifier verdict")
        self.assertEqual(rc["status"], "PROVEN")

    def test_non_ascii_criterion_can_be_proven(self):
        st = self._story(criteria=["计数文件有今天的行"])
        events = [ev("verify_passed", st["created_ts"] + 1, worker_id="w-1",
                     verdict=[{"criterion": "计数文件有今天的行",
                               "accomplished": "yes"}])]
        rc = build_receipt(self.awd, st["id"], events=events)
        self.assertEqual(rc["proof"]["criteria"][0]["proof"],
                         "named in verifier verdict")

    def test_short_criterion_does_not_match_inside_a_word(self):
        st = self._story(criteria=["docs"])
        events = [ev("verify_passed", st["created_ts"] + 1, worker_id="w-1",
                     verdict=[{"criterion": "pydocstyle clean",
                               "accomplished": "yes"}])]
        rc = build_receipt(self.awd, st["id"], events=events)
        self.assertEqual(rc["proof"]["criteria"][0]["proof"], "unproven")

    # -- robustness -------------------------------------------------------
    def test_calibration_skips_malformed_receipts(self):
        from receipt import calibration_history, receipts_dir
        d = receipts_dir(self.awd)
        d.mkdir(parents=True)
        (d / "bad-list.json").write_text("[1, 2]")
        (d / "bad-story.json").write_text(json.dumps({"lesson": {"ratio": 2}}))
        (d / "good.json").write_text(json.dumps({
            "story": {"id": "g", "title": "G", "closed_ts": 5},
            "lesson": {"ratio": 1.5}}))
        self.assertEqual([h["story_id"] for h in calibration_history(self.awd)],
                         ["g"])

    def test_event_with_null_ts_does_not_crash(self):
        st = self._story()
        events = [{"type": "tool_result", "ts": None, "data": {}},
                  ev("verify_passed", st["created_ts"] + 1,
                     worker_id="harness", verdict="pass")]
        rc = build_receipt(self.awd, st["id"], events=events)
        self.assertEqual(rc["status"], "SELF-CHECKED")

    def test_task_history_without_ts_does_not_crash(self):
        st = story_start(self.awd, "Counter", status="doing")
        from story import plan_story
        plan_story(self.awd, st["id"], breakdown="b", surveyed="s",
                   user_guidance="u",
                   proposal="A (Honda): script [Likely]. Pick A.",
                   bugatti_brief="edge model",
                   steps=[{"title": "Script", "success": "prints",
                           "failure": "crashes", "forecast": "30m"}])
        reg = Registry(self.awd)
        data = json.loads((reg.dir / "tasks.json").read_text())
        for t in data.values():
            if isinstance(t, dict) and t.get("story_id") == st["id"]:
                t["history"].append({"state": "doing"})  # hand-edited, no ts
                t["history"].append("garbage")
        (reg.dir / "tasks.json").write_text(json.dumps(data))
        rc = build_receipt(self.awd, st["id"], events=[])
        self.assertEqual(rc["proof"]["steps"][0]["actual_s"], None)

    # -- markdown ---------------------------------------------------------
    def test_markdown_table_survives_pipes_and_newlines(self):
        st = self._story(criteria=["exit | code is 0\nand logged"])
        rc = build_receipt(self.awd, st["id"], events=[])
        md = render_receipt_md(rc)
        row = [ln for ln in md.splitlines() if ln.startswith("| exit")]
        self.assertEqual(len(row), 1, md)
        # Two cells: the escaped pipe does not split the row.
        self.assertEqual(row[0].replace("\\|", "").count("|"), 3, row[0])

    def test_receipt_markdown_is_written_as_utf8_whatever_the_locale(self):
        st = self._story()
        rc = build_receipt(self.awd, st["id"], events=[])
        rc["story"]["title"] = "Lachs → Zähler ✓"
        (self.awd / "rc.json").write_text(json.dumps(rc))
        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {PROTO!r})
            from receipt import write_receipt
            rc = json.loads(open({str(self.awd / 'rc.json')!r}).read())
            print(write_receipt({str(self.awd)!r}, rc))
        """)
        env = dict(os.environ, PYTHONUTF8="0", PYTHONCOERCECLOCALE="0",
                   LC_ALL="C", LANG="C")
        out = subprocess.run([sys.executable, "-c", script], env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        md = Path(out.stdout.strip()).read_text(encoding="utf-8")
        self.assertIn("Lachs → Zähler ✓", md)



if __name__ == "__main__":
    unittest.main()
