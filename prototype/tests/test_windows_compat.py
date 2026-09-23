"""Windows compatibility: venv paths, portable recipes, PATH routing.

The harness is developed on Linux but the user runs Windows. These tests
simulate Windows via mock.patch on os.name / os.pathsep so the suite stays
green on Linux CI while proving the Windows code paths.

Proven here (by mock): venv_bin_dir/venv_python/venv_exe selection,
ensure_venv returning Scripts/ on nt, scaffolded justfile recipes containing
no Unix-only shell syntax, and run_command prepending the venv Scripts dir
to PATH with the Windows separator.

NOT proven here: a real Windows run. See WINDOWS_TEST.md for the one-command
check the user runs on their own machine.
"""
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bootstrap
from bootstrap import (JUSTFILE_TEMPLATE, ensure_venv, venv_bin_dir,
                       venv_exe, venv_python)
from tools import Sandbox


def _nt():
    """Context manager faking a Windows runtime for path-selection code."""
    return mock.patch.object(bootstrap.os, "name", "nt")


def _posix():
    return mock.patch.object(bootstrap.os, "name", "posix")


class VenvPathsTest(unittest.TestCase):
    def test_bin_dir_posix(self):
        with _posix():
            self.assertEqual(venv_bin_dir(Path("/x/.venv")),
                             Path("/x/.venv/bin"))

    def test_bin_dir_windows(self):
        # NOTE: no drive-letter paths — WindowsPath cannot be instantiated
        # on Linux; assert on the name instead. The Path must be built
        # OUTSIDE the os.name patch: pathlib.Path dispatches on os.name
        # at construction time.
        venv = Path("/x/.venv")
        with _nt():
            got = venv_bin_dir(venv)
        self.assertEqual(got.name, "Scripts")
        self.assertEqual(got.parent, venv)

    def test_python_posix(self):
        with _posix():
            self.assertEqual(venv_python(Path("/x/.venv/bin")),
                             Path("/x/.venv/bin/python"))

    def test_python_windows(self):
        scripts = Path("/x/.venv/Scripts")
        with _nt():
            got = venv_python(scripts)
        self.assertEqual(got.name, "python.exe")
        self.assertEqual(got.parent, scripts)

    def test_venv_exe_finds_dot_exe_on_windows(self):
        d = Path(tempfile.mkdtemp(prefix="awino-winexe-"))
        try:
            (d / "ruff.exe").write_text("x")
            with _nt():
                self.assertEqual(venv_exe(d, "ruff"), d / "ruff.exe")
            # plain name still wins when it exists
            (d / "ruff").write_text("x")
            with _nt():
                self.assertEqual(venv_exe(d, "ruff"), d / "ruff")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_venv_exe_posix_ignores_dot_exe(self):
        d = Path(tempfile.mkdtemp(prefix="awino-winexe-"))
        try:
            (d / "ruff").write_text("x")
            with _posix():
                self.assertEqual(venv_exe(d, "ruff"), d / "ruff")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class EnsureVenvWindowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-winvenv-"))
        (self.tmp / ".venv").mkdir()
        (self.tmp / ".venv" / "pyvenv.cfg").write_text("x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_existing_venv_returns_scripts_on_windows(self):
        with _nt():
            check, venv_bin = ensure_venv(self.tmp)
        self.assertEqual(check["status"], "ok")
        self.assertEqual(venv_bin, self.tmp / ".venv" / "Scripts")

    def test_existing_venv_returns_bin_on_posix(self):
        with _posix():
            check, venv_bin = ensure_venv(self.tmp)
        self.assertEqual(check["status"], "ok")
        self.assertEqual(venv_bin, self.tmp / ".venv" / "bin")


class RecipePortabilityTest(unittest.TestCase):
    """Scaffolded justfile recipes must run under sh, cmd.exe, and
    PowerShell (just's default windows-shell). That rules out sh-only
    redirection, || / && chains (PowerShell 5.1 lacks them), `python3`
    (absent on Windows), and Unix builtins like rm/touch/ls."""

    def test_no_unix_only_shell_syntax(self):
        # Only recipe lines count — comments may mention operators freely.
        recipe_lines = [ln for ln in JUSTFILE_TEMPLATE.splitlines()
                        if ln.strip() and not ln.lstrip().startswith("#")]
        body = "\n".join(recipe_lines)
        for bad in ("2>/dev/null", "2> /dev/null", "||", "&&", "`", "$(",
                    "python3", "rm -rf", "rm ", "touch ", "ls "):
            self.assertNotIn(bad, body,
                             f"justfile recipe contains {bad!r}")

    def test_recipes_use_portable_python(self):
        self.assertIn("python -m pytest", JUSTFILE_TEMPLATE)
        self.assertIn("ruff check .", JUSTFILE_TEMPLATE)
        self.assertIn("ruff format .", JUSTFILE_TEMPLATE)


class RunCommandWindowsRoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-winpath-"))
        (self.tmp / "Scripts").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_path_prepended_with_windows_separator(self):
        sb = Sandbox(self.tmp, venv_bin=self.tmp / "Scripts")
        # sys.executable: the test box has no bare `python` on PATH; the
        # point is what PATH string the child process inherits.
        probe = (f"{sys.executable} -c "
                 "\"import os; print(os.environ['PATH'])\"")
        with mock.patch.object(bootstrap.os, "name", "nt"), \
             mock.patch.object(bootstrap.os, "pathsep", ";"):
            res = sb.run_command(probe, timeout=30)
        self.assertEqual(res["exit_code"], 0, res.get("stderr"))
        self.assertTrue(
            res["stdout"].strip().startswith(str(self.tmp / "Scripts") + ";"),
            f"venv Scripts dir not PATH-prepended: {res['stdout'][:120]}")

    def test_virtual_env_points_at_venv_root(self):
        sb = Sandbox(self.tmp, venv_bin=self.tmp / "Scripts")
        probe = (f"{sys.executable} -c "
                 "\"import os; print(os.environ['VIRTUAL_ENV'])\"")
        with mock.patch.object(bootstrap.os, "name", "nt"):
            res = sb.run_command(probe, timeout=30)
        self.assertEqual(res["exit_code"], 0, res.get("stderr"))
        self.assertEqual(res["stdout"].strip(), str(self.tmp))


if __name__ == "__main__":
    unittest.main()


class CwdIndependenceTest(unittest.TestCase):
    """The suite must pass no matter where it is launched from.

    Regression: several verifier tests resolved evidence links against ".",
    so they passed from prototype/ but failed from the repo root — exactly
    how a Windows user runs the suite (see WINDOWS_TEST.md)."""

    def test_verdict_evidence_resolves_without_cwd(self):
        from verify import compute_verdict
        from tests.common import PROTOTYPE_ROOT
        elsewhere = tempfile.mkdtemp(prefix="awino-cwd-")
        old = os.getcwd()
        os.chdir(elsewhere)
        try:
            v = compute_verdict(
                ["x"], evidence_links={"x": "tests/common.py"},
                recipe_result={"runner": "just", "recipe": "test",
                               "exit_code": 0, "output": "ok"},
                project_root=PROTOTYPE_ROOT)
        finally:
            os.chdir(old)
            shutil.rmtree(elsewhere, ignore_errors=True)
        self.assertTrue(v["passed"])
