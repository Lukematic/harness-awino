"""Packaging (team review 09-27): `pip install` shipped 13 of 38 modules,
so the installed `awino` command died with ModuleNotFoundError; and
`chat.py --help` turned "--help" into a state directory."""
import glob
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.join(os.path.dirname(__file__), "..")


class PackagingTest(unittest.TestCase):
    def test_every_module_is_packaged(self):
        text = open(os.path.join(HERE, "pyproject.toml")).read()
        listed = set(re.findall(r'^\s+"([a-z_0-9]+)",$', text, re.M))
        present = {os.path.basename(f)[:-3]
                   for f in glob.glob(os.path.join(HERE, "*.py"))}
        self.assertEqual(sorted(present - listed), [])

    def test_chat_help_prints_usage_and_writes_nothing(self):
        cwd = tempfile.mkdtemp(prefix="awino-help-")
        r = subprocess.run([sys.executable, os.path.abspath(
            os.path.join(HERE, "chat.py")), "--help"], cwd=cwd,
            capture_output=True, text=True, timeout=30,
            env=dict(os.environ, GIT_CEILING_DIRECTORIES=cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage: python chat.py", r.stdout)
        self.assertEqual(os.listdir(cwd), [])


if __name__ == "__main__":
    unittest.main()
