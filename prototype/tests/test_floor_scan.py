"""The SHIP floor scan looks at the mission's change, not the environment
(team review 09-27): a .venv made the scan 11 MB and SHIP unpassable, and a
repo with no commits skipped the scan silently."""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from common import make_loop  # noqa: E402

STUB = "def f():\n    raise NotImplementedError\n"


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


class FloorScanTest(unittest.TestCase):
    def setUp(self):
        self.loop, _ = make_loop(project="floor")
        self.root = self.loop.sandbox.root
        git(self.root, "init", "-q")

    def _vendor(self):
        for d in (".venv/lib/site-packages/pkg", "node_modules/pkg"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
            (self.root / d / "mod.py").write_text(STUB)

    def test_no_commits_is_scanned_not_skipped(self):
        (self.root / "app.py").write_text(STUB)
        findings = self.loop._floor_gate()
        self.assertEqual([f["file"] for f in findings], ["app.py"])
        ev = [e for e in self.loop.state.events if e["type"] == "floor_checks"]
        self.assertEqual(ev[-1]["data"]["status"], "fail")

    def test_vendor_dirs_are_ignored_untracked(self):
        self._vendor()
        (self.root / "ok.py").write_text("x = 1\n")
        self.assertEqual(self.loop._floor_gate(), [])

    def test_vendor_dirs_are_ignored_even_if_tracked(self):
        (self.root / "ok.py").write_text("x = 1\n")
        git(self.root, "add", "ok.py")
        git(self.root, "commit", "-qm", "base")
        self._vendor()
        git(self.root, "add", "-A")
        (self.root / "new.py").write_text(STUB)
        self.assertEqual([f["file"] for f in self.loop._floor_gate()],
                         ["new.py"])

    def test_not_a_repo_is_skipped_and_journaled(self):
        import shutil
        shutil.rmtree(self.root / ".git")
        os.environ["GIT_CEILING_DIRECTORIES"] = str(self.root.parent)
        try:
            self.assertEqual(self.loop._floor_gate(), [])
        finally:
            os.environ.pop("GIT_CEILING_DIRECTORIES", None)
        ev = [e for e in self.loop.state.events if e["type"] == "floor_checks"]
        self.assertEqual(ev[-1]["data"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
