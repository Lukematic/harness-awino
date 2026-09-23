"""Track A/H: auto-init on chat session start.

The user never has to type `awino init` by hand. When a session starts in a
directory without `.awino/project.yaml`, the harness runs the full init
flow automatically and reports what it set up in one brief plain-language
summary. `awino init` remains as the explicit manual command/override.
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bootstrap
import chat
import cli
from bootstrap import session_start_auto_init, venv_bin_dir, venv_python
from registry import Registry


def _no_ruff(*a, **k):
    return {"name": "ruff", "status": "warn", "detail": "stubbed", "fixed": False}


class AutoInitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-autoinit-"))
        self._p1 = mock.patch.object(bootstrap, "check_ruff", _no_ruff)
        self._p1.start()
        self._p2 = mock.patch.object(
            bootstrap, "_best_effort_install_just",
            lambda timeout=90: (False, "stubbed: no installer in tests"))
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- the required test: fresh dir + session start -----------------------
    def test_session_start_fresh_dir_creates_everything(self):
        # No explicit `awino init` anywhere in this test — the session-start
        # entry point alone must produce a ready project.
        result = session_start_auto_init(self.tmp)
        self.assertIsNotNone(result, "fresh dir should trigger auto-init")
        self.assertTrue(result["ok"], result["summary"])
        self.assertTrue((self.tmp / ".awino" / "project.yaml").is_file(),
                        "project.yaml missing")
        self.assertTrue(venv_python(venv_bin_dir(self.tmp / ".venv")).exists(),
                        "venv missing")
        self.assertTrue((self.tmp / "justfile").is_file(), "justfile missing")
        self.assertTrue((self.tmp / ".awino" / "registry").is_dir(),
                        "registry missing")
        # one brief plain-language summary: no tracebacks, no jargon dumps
        summary = "\n".join(result["summary"])
        self.assertNotIn("Traceback", summary)
        self.assertNotIn("pyvenv.cfg", summary)
        self.assertIn("Set up this project", summary)
        self.assertIn(".venv", summary)
        self.assertIn("project.yaml", summary)
        self.assertLessEqual(len(result["summary"]), 4, "summary must stay brief")

    def test_session_start_existing_project_is_silent(self):
        first = session_start_auto_init(self.tmp)
        self.assertIsNotNone(first)
        yaml_path = self.tmp / ".awino" / "project.yaml"
        before = yaml_path.read_text()
        # second session start: already a project -> None, nothing re-done
        second = session_start_auto_init(self.tmp)
        self.assertIsNone(second, "existing project must not re-init")
        self.assertEqual(yaml_path.read_text(), before)

    def test_session_start_adopts_without_clobbering(self):
        (self.tmp / "README.md").write_text("MINE — do not touch")
        (self.tmp / "justfile").write_text("test:\n\techo custom\n")
        result = session_start_auto_init(self.tmp)
        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual((self.tmp / "README.md").read_text(),
                         "MINE — do not touch")
        self.assertIn("echo custom", (self.tmp / "justfile").read_text())
        self.assertTrue((self.tmp / ".awino" / "project.yaml").is_file())

    def test_zero_byte_project_yaml_is_repaired_not_trusted(self):
        # A 0-byte project.yaml is a stub from a failed init (e.g. full
        # disk) — it must not count as "already a project".
        awino = self.tmp / ".awino"
        awino.mkdir(parents=True)
        (awino / "project.yaml").write_text("")
        with mock.patch.object(
                bootstrap, "full_init_flow",
                return_value={"ok": True, "summary": ["ok"],
                              "checks": [], "breadcrumbs": {},
                              "seeds_imported": 0}) as m:
            result = session_start_auto_init(self.tmp)
        self.assertIsNotNone(result, "0-byte stub must trigger init")
        m.assert_called_once_with(self.tmp)
        self.assertFalse((awino / "project.yaml").exists(),
                         "0-byte stub should be removed before re-init")

    def test_nonempty_project_yaml_is_left_alone(self):
        awino = self.tmp / ".awino"
        awino.mkdir(parents=True)
        (awino / "project.yaml").write_text("project: mine\n")
        with mock.patch.object(bootstrap, "full_init_flow") as m:
            result = session_start_auto_init(self.tmp)
        self.assertIsNone(result)
        m.assert_not_called()
        self.assertEqual((awino / "project.yaml").read_text(),
                         "project: mine\n")
        # Root ignores permission bits, so simulate the kernel's refusal.
        (self.tmp / "README.md").write_text("existing work")
        real_mkdir = Path.mkdir

    def test_session_start_never_raises_on_unwritable(self):
        # Root ignores permission bits, so simulate the kernel's refusal.
        (self.tmp / "README.md").write_text("existing work")
        real_mkdir = Path.mkdir

        def _no_mkdir(self, *a, **k):
            if str(self).startswith(str(self.tmp)):
                raise PermissionError(13, "Permission denied", str(self))
            return real_mkdir(self, *a, **k)

        with mock.patch.object(Path, "mkdir", _no_mkdir):
            result = session_start_auto_init(self.tmp)
        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])
        summary = "\n".join(result["summary"])
        self.assertIn("Next action", summary)
        self.assertNotIn("Traceback", summary)
        self.assertEqual((self.tmp / "README.md").read_text(), "existing work")

    # -- the REPL session-start wiring --------------------------------------
    def test_chat_session_start_prints_brief_summary(self):
        out = io.StringIO()
        with redirect_stdout(out):
            result = chat.session_start(self.tmp)
        self.assertIsNotNone(result)
        self.assertIn("Set up this project", out.getvalue())
        self.assertTrue((self.tmp / ".awino" / "project.yaml").is_file())
        # second start in the same dir: silent
        out2 = io.StringIO()
        with redirect_stdout(out2):
            result2 = chat.session_start(self.tmp)
        self.assertIsNone(result2)
        self.assertEqual(out2.getvalue(), "")

    # -- `awino init` remains as the explicit manual command -----------------
    def test_init_manual_command_still_works_and_imports_seeds(self):
        seeds = self.tmp / ".awino" / "seeds"
        seeds.mkdir(parents=True)
        (seeds / "kickoff.md").write_text(
            "---\nname: kickoff\n---\n\n- [ ] first seed task\n- [x] done seed\n")
        out, err = io.StringIO(), io.StringIO()
        from contextlib import redirect_stderr
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.cmd_init([str(self.tmp)])
        self.assertEqual(code, 0, err.getvalue())
        reg = Registry(self.tmp / ".awino")
        texts = [t["text"] for t in reg.tasks()]
        self.assertIn("first seed task", texts)
        done = [t for t in reg.tasks() if t["text"] == "done seed"][0]
        self.assertEqual(done["state"], "done")
        self.assertIn("imported 2 seed tasks", out.getvalue())


if __name__ == "__main__":
    unittest.main()
