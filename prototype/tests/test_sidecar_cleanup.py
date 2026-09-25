"""Regression test: sidecar tests must not leak temp dirs.

Every ``SidecarClient`` creates two temp dirs (``awino-sidecar-test-*``
workspace and ``awino-home-test-*`` AWINO_HOME). The historical defect:
``SidecarClient.close()`` stopped the sidecar process but never removed
the dirs, so a full suite run leaked 1,548 of them (tens of GB) and once
filled a 512MB tmpfs, causing mass test failures.

These tests run real sidecar tests in an isolated subprocess with a fresh
TMPDIR and assert zero matching dirs remain afterwards — both when the
subset passes and when a test in it is forced to fail (unittest
tearDown/addCleanup still run on failure, and close() removes owned dirs
on both paths).

Stdlib only.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

PROTOTYPE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEAK_PREFIXES = ("awino-sidecar-test-", "awino-home-test-")

# Representative subset of the real sidecar suite: protocol handshake,
# malformed input, and a workspace-tools round trip (exercises both temp
# dirs the client creates).
PASSING_SUBSET = [
    "tests.test_sidecar.ProtocolTest.test_hello_ready",
    "tests.test_sidecar.ProtocolTest.test_malformed_stdin_survives",
    "tests.test_sidecar.ProtocolTest.test_unknown_cmd_fails_closed",
]

# A test module written into a scratch dir for the forced-failure case.
# Mirrors the real pattern: client created in setUp, closed in tearDown,
# test body raises mid-run.
FORCED_FAILURE_MODULE = textwrap.dedent("""\
    import unittest
    from tests.test_sidecar import SidecarClient

    class ForcedFailureTest(unittest.TestCase):
        def setUp(self):
            self.c = SidecarClient()

        def tearDown(self):
            self.c.close()

        def test_forced_failure(self):
            self.c.hello()
            raise AssertionError("forced failure: temp dirs must still "
                                 "be cleaned up")
    """)


def _leaked_dirs(tmpdir):
    """Dirs in tmpdir matching the historical leak prefixes."""
    found = []
    for name in os.listdir(tmpdir):
        if name.startswith(LEAK_PREFIXES):
            path = os.path.join(tmpdir, name)
            if os.path.isdir(path):
                found.append(path)
    return found


def _fresh_tmpdir(test):
    tmpdir = tempfile.mkdtemp(prefix="awino-cleanup-check-")
    test.addCleanup(shutil.rmtree, tmpdir, True)
    return tmpdir


class SidecarCleanupTest(unittest.TestCase):
    def _run_in_isolated_tmp(self, unittest_args, extra_path=None):
        tmpdir = _fresh_tmpdir(self)
        env = dict(os.environ, TMPDIR=tmpdir)
        if extra_path:
            env["PYTHONPATH"] = extra_path + os.pathsep + env.get(
                "PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest"] + unittest_args,
            cwd=PROTOTYPE_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=900)
        return tmpdir, proc

    def test_passing_sidecar_tests_leave_zero_temp_dirs(self):
        tmpdir, proc = self._run_in_isolated_tmp(PASSING_SUBSET)
        self.assertEqual(proc.returncode, 0,
                         f"subset must stay green:\n{proc.stdout[-3000:]}")
        leaked = _leaked_dirs(tmpdir)
        self.assertEqual(leaked, [],
                         f"leaked temp dirs after passing run: {leaked}")

    def test_failing_sidecar_test_still_cleans_up_temp_dirs(self):
        scratch = _fresh_tmpdir(self)
        mod_path = os.path.join(scratch, "forced_failure_mod.py")
        with open(mod_path, "w") as f:
            f.write(FORCED_FAILURE_MODULE)
        tmpdir, proc = self._run_in_isolated_tmp(
            ["forced_failure_mod.ForcedFailureTest.test_forced_failure"],
            extra_path=scratch)
        self.assertNotEqual(proc.returncode, 0,
                            "the forced-failure test must actually fail")
        self.assertIn("forced failure", proc.stdout)
        leaked = _leaked_dirs(tmpdir)
        self.assertEqual(leaked, [],
                         f"leaked temp dirs after failing run: {leaked}")

    def test_sweeper_removes_stale_stray_dirs(self):
        from tests.temp_sweep import sweep, STALE_AFTER_SECONDS
        tmpdir = _fresh_tmpdir(self)
        strays = []
        for prefix in LEAK_PREFIXES:
            d = tempfile.mkdtemp(prefix=prefix, dir=tmpdir)
            strays.append(d)
        # Backdate: only STALE strays may be swept.
        old = time.time() - STALE_AFTER_SECONDS - 60
        for d in strays:
            os.utime(d, (old, old))
        # a non-matching dir must be left alone
        keep = tempfile.mkdtemp(prefix="awino-cleanup-check-keep-",
                                dir=tmpdir)
        old_tmpdir = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = tmpdir
        try:
            removed = sweep()
        finally:
            if old_tmpdir is None:
                del os.environ["TMPDIR"]
            else:
                os.environ["TMPDIR"] = old_tmpdir
        # Exact count is not asserted: other suite runs may share the
        # TMPDIR and create/remove matching dirs concurrently. What
        # matters is that OUR strays are gone and the keep-dir survived.
        self.assertGreaterEqual(removed, len(strays))
        self.assertTrue(os.path.isdir(keep))
        for d in strays:
            self.assertFalse(os.path.exists(d), d)

    def test_sweeper_never_touches_fresh_dirs(self):
        # Concurrency safety: a matching dir younger than the staleness
        # threshold may belong to a LIVE concurrent suite run sharing this
        # TMPDIR — the sweeper must leave it alone.
        from tests.temp_sweep import sweep
        tmpdir = _fresh_tmpdir(self)
        fresh_dirs = [tempfile.mkdtemp(prefix=p, dir=tmpdir)
                      for p in LEAK_PREFIXES]
        old_tmpdir = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = tmpdir
        try:
            sweep()
        finally:
            if old_tmpdir is None:
                del os.environ["TMPDIR"]
            else:
                os.environ["TMPDIR"] = old_tmpdir
        for d in fresh_dirs:
            self.assertTrue(os.path.isdir(d),
                            f"fresh dir must survive the sweep: {d}")


if __name__ == "__main__":
    unittest.main()
