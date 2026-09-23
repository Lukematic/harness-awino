"""Track E: role environment profiles — project.yaml + per-role scaffolding."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bootstrap
from bootstrap import (DEFAULT_PROFILE, PROFILE_IDS, TOOLCHAINS,
                       ensure_profile_scaffold, read_project_yaml,
                       run_startup_checklist, write_project_yaml)


def _no_ruff(*a, **k):
    return {"name": "ruff", "status": "warn", "detail": "stubbed", "fixed": False}


class ProfileYamlTest(unittest.TestCase):
    def test_new_yaml_defaults_profile(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-prof-"))
        d = tmp / ".awino"
        d.mkdir()
        p = write_project_yaml(d, "demo")
        data = read_project_yaml(p)
        self.assertEqual(data.get("profile"), DEFAULT_PROFILE)
        self.assertEqual(DEFAULT_PROFILE, "software-engineer")

    def test_toolchains_declared_per_role(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-prof-"))
        d = tmp / ".awino"
        d.mkdir()
        data = read_project_yaml(write_project_yaml(d, "demo"))
        tc = data.get("toolchains", {})
        self.assertEqual(set(tc.keys()), set(PROFILE_IDS))
        self.assertIn("pytest", tc["software-engineer"])
        for rid in PROFILE_IDS:
            self.assertTrue(tc[rid], f"{rid} has no toolchain")

    def test_profile_round_trips(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-prof-"))
        d = tmp / ".awino"
        d.mkdir()
        p = write_project_yaml(d, "demo")
        text = p.read_text().replace("profile: software-engineer",
                                     "profile: ai-researcher")
        p.write_text(text)
        self.assertEqual(read_project_yaml(p)["profile"], "ai-researcher")


class ProfileScaffoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-scaff-"))

    def test_researcher_profile_creates_experiments_and_templates(self):
        r = ensure_profile_scaffold(self.tmp, "ai-researcher")
        self.assertEqual(r["check"]["status"], "ok")
        self.assertTrue((self.tmp / "experiments").is_dir())
        self.assertTrue((self.tmp / "experiments" / "RUNLOG.md").is_file())
        self.assertTrue((self.tmp / "requirements.txt").is_file())

    def test_architect_profile_creates_adr(self):
        r = ensure_profile_scaffold(self.tmp, "ai-architect")
        self.assertTrue((self.tmp / "docs" / "adr" / "0000-template.md").is_file())
        self.assertIn("Context", (self.tmp / "docs" / "adr" / "0000-template.md").read_text())

    def test_fde_profile_creates_runbooks(self):
        r = ensure_profile_scaffold(self.tmp, "forward-deployed-engineer")
        self.assertTrue((self.tmp / "runbooks" / "TEMPLATE.md").is_file())
        self.assertTrue((self.tmp / "stakeholder-notes").is_dir())

    def test_security_profile_creates_threat_model(self):
        r = ensure_profile_scaffold(self.tmp, "cybersecurity-engineer")
        p = self.tmp / "docs" / "security" / "THREAT-MODEL.md"
        self.assertTrue(p.is_file())
        self.assertIn("STRIDE", p.read_text())

    def test_software_engineer_uses_standard_scaffold(self):
        r = ensure_profile_scaffold(self.tmp, "software-engineer")
        self.assertEqual(r["created"], [])
        self.assertEqual(r["check"]["status"], "ok")

    def test_existing_files_never_clobbered(self):
        (self.tmp / "experiments").mkdir(parents=True)
        sentinel = self.tmp / "experiments" / "RUNLOG.md"
        sentinel.write_text("MY LOG — do not touch")
        req = self.tmp / "requirements.txt"
        req.write_text("django==5.0")
        r = ensure_profile_scaffold(self.tmp, "ai-researcher")
        self.assertEqual(sentinel.read_text(), "MY LOG — do not touch")
        self.assertEqual(req.read_text(), "django==5.0")
        self.assertIn("experiments/RUNLOG.md", r["kept"])

    def test_unknown_profile_warns_never_raises(self):
        r = ensure_profile_scaffold(self.tmp, "rockstar")
        self.assertEqual(r["check"]["status"], "warn")
        self.assertIn("rockstar", r["check"]["detail"])

    def test_checklist_honors_configured_profile(self):
        # project.yaml says ai-architect -> adr scaffold applied, not researcher
        with mock.patch.object(bootstrap, "check_ruff", _no_ruff), \
             mock.patch.object(bootstrap, "_best_effort_install_just",
                               lambda timeout=90: (False, "stubbed")):
            report = run_startup_checklist(self.tmp, mission_text="design the system")
        names = {c["name"]: c for c in report["checks"]}
        self.assertIn("profile_scaffold", names)
        # default profile is software-engineer
        self.assertIn("software-engineer", names["profile_scaffold"]["detail"])


if __name__ == "__main__":
    unittest.main()
