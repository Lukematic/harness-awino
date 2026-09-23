"""Track A: project bootstrap — startup checklist, venv, scaffolding, seeds."""
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bootstrap
from bootstrap import (CHECK_FAIL, CHECK_OK, collect_seed_tasks,
                       parse_seed_checklist, read_project_yaml,
                       run_startup_checklist, write_project_yaml)
from tools import Sandbox


def _no_ruff(*a, **k):
    return {"name": "ruff", "status": "warn", "detail": "stubbed", "fixed": False}


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-bootstrap-"))
        # keep tests hermetic: no real ruff download, no real just install
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

    # -- empty project ------------------------------------------------------
    def test_empty_project_creates_everything(self):
        report = run_startup_checklist(self.tmp, mission_text="do things",
                                       criteria=["manual"])
        self.assertTrue(report["ok"])
        self.assertFalse([c for c in report["checks"]
                          if c["status"] == CHECK_FAIL])
        self.assertTrue((self.tmp / ".venv" / "pyvenv.cfg").is_file())
        self.assertIsNotNone(report["venv_bin"])
        self.assertTrue((self.tmp / "justfile").is_file())
        self.assertTrue((self.tmp / ".awino" / "project.yaml").is_file())
        self.assertTrue((self.tmp / "spec").is_dir())
        self.assertTrue((self.tmp / "docs" / "research").is_dir())
        self.assertTrue((self.tmp / "docs" / "archive").is_dir())
        self.assertTrue((self.tmp / "lessons.md").is_file())
        self.assertTrue((self.tmp / "README.md").is_file())
        d = read_project_yaml(self.tmp / ".awino" / "project.yaml")
        self.assertEqual(d["mission"], "do things")
        self.assertEqual(d["done_criteria"], ["manual"])
        self.assertEqual(d["principles"],
                         ["spec-before-code", "contract-first",
                          "evidence-for-every-change"])

    def test_empty_project_registers_bootstrap_event_shape(self):
        # the report carries everything the sidecar journals as evidence
        report = run_startup_checklist(self.tmp)
        self.assertIn("checks", report)
        self.assertIn("venv_bin", report)
        self.assertIn("seed_tasks", report)
        self.assertIn("breadcrumbs", report)
        self.assertIn("ok", report)
        for c in report["checks"]:
            self.assertIn(c["status"], ("ok", "warn", "fail"))

    # -- idempotency / no clobber -------------------------------------------
    def test_existing_venv_and_makefile_not_clobbered(self):
        venv = self.tmp / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        (self.tmp / "Makefile").write_text("test:\n\t@echo hi\n")
        (self.tmp / ".awino").mkdir()
        (self.tmp / ".awino" / "project.yaml").write_text(
            "project: mine\nmission: custom\n")
        (self.tmp / "README.md").write_text("# mine\n")
        (self.tmp / "lessons.md").write_text("# Lessons\n- old\n")
        report = run_startup_checklist(self.tmp)
        self.assertTrue(report["ok"])
        venv_check = next(c for c in report["checks"] if c["name"] == "venv")
        self.assertEqual(venv_check["status"], CHECK_OK)
        self.assertIn("existing", venv_check["detail"])
        runner = next(c for c in report["checks"]
                      if c["name"] == "task_runner")
        self.assertIn("Makefile", runner["detail"])
        self.assertFalse((self.tmp / "justfile").exists())
        self.assertEqual((self.tmp / "Makefile").read_text(),
                         "test:\n\t@echo hi\n")
        self.assertIn("mission: custom",
                      (self.tmp / ".awino" / "project.yaml").read_text())
        self.assertEqual((self.tmp / "README.md").read_text(), "# mine\n")
        self.assertIn("- old\n", (self.tmp / "lessons.md").read_text())

    # -- venv wiring ----------------------------------------------------------
    def test_run_command_uses_project_venv(self):
        report = run_startup_checklist(self.tmp)
        venv_bin = Path(report["venv_bin"])
        sb = Sandbox(self.tmp, venv_bin=venv_bin)
        res = sb.run_command(
            "python3 -c \"import sys; print(sys.prefix)\"", timeout=30)
        self.assertEqual(res["exit_code"], 0, res.get("stderr"))
        # PROOF: the venv is active through sys.prefix
        self.assertEqual(res["stdout"].strip(), str(self.tmp / ".venv"))

    # -- degradation, never failure -------------------------------------------
    def test_just_install_failure_degrades_to_breadcrumb(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            report = run_startup_checklist(self.tmp)
        runner = next(c for c in report["checks"]
                      if c["name"] == "task_runner")
        self.assertEqual(runner["status"], "warn")
        self.assertTrue(report["breadcrumbs"])
        self.assertTrue(report["ok"])  # bootstrap does not fail

    def test_venv_creation_failure_is_fail_not_raise(self):
        with mock.patch.object(bootstrap, "_run",
                               return_value=(1, "disk full (stubbed)")):
            report = run_startup_checklist(self.tmp)  # must not raise
        venv_check = next(c for c in report["checks"] if c["name"] == "venv")
        self.assertEqual(venv_check["status"], CHECK_FAIL)
        self.assertFalse(report["ok"])
        self.assertIsNone(report["venv_bin"])

    def test_unexpected_error_never_raises(self):
        with mock.patch.object(bootstrap, "check_python",
                               side_effect=RuntimeError("boom")):
            report = run_startup_checklist(self.tmp)
        self.assertTrue(any(c["name"] == "bootstrap"
                            and c["status"] == CHECK_FAIL
                            for c in report["checks"]))

    # -- seeds -----------------------------------------------------------------
    def test_parse_seed_checklist(self):
        body = ("# Seed\n\n- [ ] first task\n- [x] finished task\n"
                "- not a task\n- [ ] second\n")
        tasks = parse_seed_checklist(body)
        self.assertEqual([(t["text"], t["done"]) for t in tasks],
                         [("first task", False), ("finished task", True),
                          ("second", False)])

    def test_collect_seed_tasks_from_files(self):
        seeds = self.tmp / ".awino" / "seeds"
        seeds.mkdir(parents=True)
        (seeds / "a.md").write_text("---\nname: A\n---\n\n- [ ] task one\n")
        (seeds / "b.md").write_text("- [x] task two\n")
        check, tasks = collect_seed_tasks(seeds)
        self.assertEqual(check["status"], CHECK_OK)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["source"], "seed:a")

    def test_no_seeds_starts_empty(self):
        check, tasks = collect_seed_tasks(self.tmp / ".awino" / "seeds")
        self.assertEqual(tasks, [])

    # -- yaml ------------------------------------------------------------------
    def test_yaml_round_trip(self):
        d = self.tmp / ".awino"
        d.mkdir(parents=True)
        write_project_yaml(d, "demo", "do the thing",
                           ["artifact:x.py", "manual"])
        parsed = read_project_yaml(d / "project.yaml")
        self.assertEqual(parsed["project"], "demo")
        self.assertEqual(parsed["mission"], "do the thing")
        self.assertEqual(parsed["done_criteria"], ["artifact:x.py", "manual"])
        self.assertEqual(parsed["env"]["linters"], ["ruff"])
        self.assertEqual(parsed["env"]["venv"], ".venv")
        self.assertEqual(parsed["docs"]["spec"], "spec/")
        self.assertEqual(parsed["principles"],
                         ["spec-before-code", "contract-first",
                          "evidence-for-every-change"])

    def test_read_project_yaml_missing_file(self):
        self.assertEqual(read_project_yaml(self.tmp / "nope.yaml"), {})


if __name__ == "__main__":
    unittest.main()
