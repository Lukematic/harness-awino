"""Track H: awino init/status/plan CLI + plain-language errors."""
import io
import os
import sys
import tempfile
import shutil
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cli
import bootstrap


def _no_ruff(*a, **k):
    return {"name": "ruff", "status": "warn", "detail": "stubbed", "fixed": False}


class CLITest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-cli-"))
        self._p1 = mock.patch.object(bootstrap, "check_ruff", _no_ruff)
        self._p1.start()
        self._p2 = mock.patch.object(
            bootstrap, "_best_effort_install_just",
            lambda timeout=90: (False, "stubbed: no installer in tests"))
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, fn, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fn(args)
        return code, out.getvalue(), err.getvalue()

    def test_init_empty_dir_bootstraps(self):
        code, out, err = self._run(cli.cmd_init, [str(self.tmp)])
        self.assertEqual(code, 0, err)
        self.assertIn("[ok]", out)
        self.assertIn("ready", out)
        self.assertTrue((self.tmp / ".awino" / "project.yaml").is_file())
        self.assertTrue((self.tmp / ".awino" / "registry").is_dir())

    def test_init_adopts_existing_project_without_clobbering(self):
        sentinel = self.tmp / "README.md"
        sentinel.write_text("MY README")
        (self.tmp / "justfile").write_text("test:\n\techo hi\n")
        code, out, err = self._run(cli.cmd_init, [str(self.tmp)])
        self.assertEqual(code, 0, err)
        self.assertEqual(sentinel.read_text(), "MY README")
        self.assertIn("echo hi", (self.tmp / "justfile").read_text())
        self.assertIn("profile: software-engineer",
                      (self.tmp / ".awino" / "project.yaml").read_text())

    def test_init_is_idempotent(self):
        self._run(cli.cmd_init, [str(self.tmp)])
        code, out, err = self._run(cli.cmd_init, [str(self.tmp)])
        self.assertEqual(code, 0, err)

    def test_status_plain_language_dashboard(self):
        self._run(cli.cmd_init, [str(self.tmp)])
        # seed a role + DAG so the dashboard has something to show
        from registry import Registry
        import modes as _modes
        reg = Registry(self.tmp / ".awino")
        reg.ensure()
        _modes.write_role_state(self.tmp / ".awino", "ai-researcher",
                                "experiments everywhere", "router", "PLAN")
        a = reg.add_task("run the experiment")
        b = reg.add_task("write it up", depends_on=[a["id"]])
        reg.add_breadcrumb("m-1", "paused for coffee", stop_point="step 2")
        code, out, err = self._run(cli.cmd_status, [str(self.tmp)])
        self.assertEqual(code, 0, err)
        self.assertIn("role lens: ai-researcher", out)
        self.assertIn("why: experiments everywhere", out)
        self.assertIn("work:", out)
        self.assertIn("up next:", out)
        self.assertIn("last stop:", out)
        # no jargon dumps
        self.assertNotIn("Traceback", out)

    def test_status_without_project_is_plain_error(self):
        plain = Path(tempfile.mkdtemp(prefix="awino-cli-plain-"))
        code, out, err = self._run(cli.cmd_status, [str(plain)])
        self.assertEqual(code, 1)
        self.assertIn("next action", err)
        self.assertIn("awino init", err)

    def test_plan_shows_next_and_blocked(self):
        self._run(cli.cmd_init, [str(self.tmp)])
        from registry import Registry
        reg = Registry(self.tmp / ".awino")
        a = reg.add_task("first")
        b = reg.add_task("second", depends_on=[a["id"]])
        code, out, err = self._run(cli.cmd_plan, [str(self.tmp)])
        self.assertEqual(code, 0, err)
        self.assertIn("what's next", out)
        self.assertIn("first", out)
        self.assertIn("blocked, and by what", out)
        self.assertIn("second", out)
        self.assertIn("full order", out)

    def test_plan_empty_registry(self):
        self._run(cli.cmd_init, [str(self.tmp)])
        code, out, err = self._run(cli.cmd_plan, [str(self.tmp)])
        # init created an empty registry (no tasks) — plan works, empty
        self.assertEqual(code, 0, err)
        self.assertIn("what's next", out)

    def test_unknown_command_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                sys.argv = ["awino", "bogus"]
                cli.main()
        self.assertEqual(ctx.exception.code, 2)

    def test_plain_error_format_has_three_parts(self):
        code = cli._plain_error("init", RuntimeError("disk on fire"))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
