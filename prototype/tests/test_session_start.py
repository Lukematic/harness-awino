"""Session start tells the user what it remembers (team review 09-27):
notices ride on the ready event instead of a `say` nobody was listening
to, and a corrupt journal line no longer stops the sidecar."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SIDECAR = os.path.join(os.path.dirname(__file__), "..", "awino_sidecar.py")


def hello(ws, cmds=()):
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(Path(ws).parent))
    env.pop("AWINO_HOME", None)
    msgs = [{"cmd": "hello", "workspace": str(ws), "provider": "echo"}]
    msgs += [{"cmd": "command", "name": n, "args": a, "id": str(i)}
             for i, (n, a) in enumerate(cmds)]
    msgs.append({"cmd": "bye"})
    out = subprocess.run([sys.executable, SIDECAR],
                         input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, env=env, timeout=60)
    evs = [json.loads(l) for l in out.stdout.splitlines() if l.strip()]
    ready = next(e for e in evs if e.get("event") == "ready")
    return ready, evs, out.stderr


class SessionStartTest(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="awino-start-")) / "proj"
        self.ws.mkdir()

    def test_lessons_and_open_stories_are_in_ready(self):
        import lessons as L
        awd = self.ws / ".awino"
        L.learn_from_receipt(awd, {"story": {"id": "s1"}, "proof": {
            "checks": [{"cmd": "pytest", "runs": 2, "failures": 1}]}})
        hello(self.ws, [("story_start", {"title": "Salmon counter"})])
        ready, _, err = hello(self.ws)
        notices = " ".join(ready.get("notices") or [])
        self.assertIn("1 lesson(s) from past receipts", notices, err[-800:])
        self.assertIn("`pytest` failed", notices)
        self.assertIn("Open stories: Salmon counter (doing)", notices)

    def test_corrupt_journal_line_is_repaired_not_fatal(self):
        hello(self.ws, [("story_start", {"title": "x"})])
        journals = list((self.ws / ".awino").rglob("events.jsonl"))
        self.assertTrue(journals)
        j = journals[0]
        # Add our own events so the test doesn't depend on how many the
        # background session-start work had written yet (CI was slower).
        from state import ProjectState
        st = ProjectState(j.parent.parent.parent, j.parent.name)
        for i in range(3):
            st.record("note", {"i": i})
        lines = j.read_text().splitlines()
        self.assertGreater(len(lines), 2)
        lines[1] = "{not json"
        j.write_text("\n".join(lines) + "\n")
        ready, evs, err = hello(self.ws)
        notices = " ".join(ready.get("notices") or [])
        self.assertIn("corrupt line (2)", notices, err[-800:])
        self.assertTrue(list(j.parent.glob("events.corrupt-*.jsonl")))
        from state import ProjectState
        st = ProjectState(j.parent.parent.parent, j.parent.name)
        self.assertEqual(st.verify_chain()[0], True)


if __name__ == "__main__":
    unittest.main()
