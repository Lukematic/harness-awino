"""Story ledger over the sidecar protocol (T10): start, focus, list with
time dedicated, close onto the brag board."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SIDECAR = os.path.join(os.path.dirname(__file__), "..", "awino_sidecar.py")


def run(ws, cmds):
    env = dict(os.environ, AWINO_HOME=tempfile.mkdtemp(prefix="awino-home-"),
               GIT_CEILING_DIRECTORIES=str(Path(ws).parent))
    msgs = [{"cmd": "hello", "workspace": str(ws), "provider": "echo"}]
    msgs += [{"cmd": "command", "name": n, "args": a, "id": str(i)}
             for i, (n, a) in enumerate(cmds)]
    msgs.append({"cmd": "bye"})
    out = subprocess.run([sys.executable, SIDECAR],
                         input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, env=env, timeout=60)
    res = [json.loads(l) for l in out.stdout.splitlines() if l.strip()]
    return [r for r in res if r.get("event") == "command_result"], out.stderr


class SidecarStoriesTest(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="awino-stories-")) / "proj"
        self.ws.mkdir()

    def result(self, r):
        return r.get("result", r)

    def test_start_list_close_brag(self):
        results, err = run(self.ws, [
            ("story_start", {"title": "Salmon counter", "problem": "No count."}),
            ("story_start", {"title": "Docs refresh", "type": "chore"}),
            ("stories", {}),
        ])
        self.assertEqual(len(results), 3, err[-1500:])
        listed = self.result(results[2])["stories"]
        by_title = {s["title"]: s for s in listed}
        # One story in progress at a time: starting the second parks the first.
        self.assertEqual(by_title["Docs refresh"]["status"], "doing")
        self.assertEqual(by_title["Salmon counter"]["status"], "open")
        sid = by_title["Salmon counter"]["id"]

        results, err = run(self.ws, [
            ("story_close", {"id": sid, "outcome": ""}),
            ("story_focus", {"id": sid}),
            ("story_close", {"id": sid, "outcome": "Counter live"}),
            ("stories", {}),
        ])
        self.assertEqual(self.result(results[0])["status"], "error")
        self.assertEqual(self.result(results[1])["status"], "ok")
        ledger = self.result(results[3])
        self.assertEqual([b["title"] for b in ledger["brag"]],
                         ["Salmon counter"])
        self.assertEqual(ledger["brag"][0]["outcome"], "Counter live")
        self.assertGreaterEqual(ledger["brag"][0]["time_s"], 0)
        self.assertIn("Counter live", (self.ws / "STORY.md").read_text())
        # The close hands back a receipt; with no checks run it is honest.
        closed = self.result(results[2])
        self.assertEqual(closed["receipt"]["status"], "UNVERIFIED")
        self.assertIn("## Salmon counter", closed["markdown"])
        results, _ = run(self.ws, [("receipt", {"id": sid}),
                                   ("receipt", {"id": "st-nope"})])
        self.assertEqual(self.result(results[0])["receipt"]["story"]["id"], sid)
        self.assertEqual(self.result(results[1])["status"], "error")

    def test_empty_project_reports_unattached(self):
        results, _ = run(self.ws, [("stories", {})])
        r = self.result(results[0])
        self.assertEqual((r["stories"], r["attached"]), ([], False))


if __name__ == "__main__":
    unittest.main()
