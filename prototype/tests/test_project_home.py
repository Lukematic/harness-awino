"""One memory per project (T4).

Field bug: the VS Code extension kept session state in ~/.awino-loop while
stories and the registry lived in <project>/.awino, and `awino chat` put
every project in one "inbox" session. State now lives in the project; old
state is adopted once (copied, never deleted).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import state  # noqa: E402
from backends import ScriptedBackend, ScriptedJudge  # noqa: E402
from loop import Loop  # noqa: E402

SIDECAR = os.path.join(os.path.dirname(__file__), "..", "awino_sidecar.py")


class HelpersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-home-"))
        self.ws = self.tmp / "My Project"
        self.ws.mkdir()
        self.legacy = self.tmp / "legacy"
        p = mock.patch.object(state, "LEGACY_HOME", self.legacy)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AWINO_HOME", None)

    def test_default_home_is_inside_the_project(self):
        self.assertEqual(state.default_home(self.ws), self.ws / ".awino")
        os.environ["AWINO_HOME"] = str(self.tmp / "override")
        self.assertEqual(state.default_home(self.ws), self.tmp / "override")

    def test_slug(self):
        self.assertEqual(state.project_slug(self.ws), "my-project")

    def test_legacy_state_is_copied_once_never_deleted(self):
        src = self.legacy / "projects" / "my-project"
        src.mkdir(parents=True)
        (src / "events.jsonl").write_text("{}\n")
        home = state.default_home(self.ws)
        self.assertEqual(state.adopt_legacy_state(home, "my-project"), src)
        self.assertTrue((home / "projects" / "my-project" / "events.jsonl")
                        .exists())
        self.assertTrue((src / "events.jsonl").exists())
        self.assertIsNone(state.adopt_legacy_state(home, "my-project"))

    def test_no_legacy_no_copy(self):
        self.assertIsNone(state.adopt_legacy_state(self.ws / ".awino", "x"))

    def test_state_is_gitignored_but_not_overwritten(self):
        home = self.ws / ".awino"
        state.ensure_state_ignored(home)
        self.assertIn("projects/", (home / ".gitignore").read_text())
        (home / ".gitignore").write_text("custom\n")
        state.ensure_state_ignored(home)
        self.assertEqual((home / ".gitignore").read_text(), "custom\n")


class SidecarSharesProjectMemoryTest(unittest.TestCase):
    def test_sidecar_mission_is_visible_to_a_cli_loop(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-t4-"))
        ws = tmp / "salmon-app"
        ws.mkdir()
        env = dict(os.environ, HOME=str(tmp / "userhome"))
        env.pop("AWINO_HOME", None)
        msgs = [{"cmd": "hello", "workspace": str(ws), "provider": "echo"},
                {"cmd": "command", "name": "mission",
                 "args": {"text": "Count the salmon", "criteria": ["manual"]}},
                {"cmd": "bye"}]
        out = subprocess.run(
            [sys.executable, SIDECAR], input="\n".join(json.dumps(m)
                                                       for m in msgs) + "\n",
            capture_output=True, text=True, env=env, timeout=60)
        events = [json.loads(l) for l in out.stdout.splitlines() if l.strip()]
        self.assertTrue(any(e.get("event") == "command_result"
                            for e in events), out.stderr[-2000:])
        home = ws / ".awino"
        self.assertTrue((home / "projects" / "salmon-app" / "snapshot.json")
                        .exists())
        self.assertFalse((tmp / "userhome" / ".awino-loop").exists())
        self.assertIn("projects/", (home / ".gitignore").read_text())
        cli_loop = Loop(str(home), "salmon-app", ScriptedBackend([]),
                        ScriptedJudge())
        self.assertEqual(cli_loop.state.snapshot["mission"]["text"],
                         "Count the salmon")


if __name__ == "__main__":
    unittest.main()
