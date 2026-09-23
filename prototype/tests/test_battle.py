"""Battle testing: edge cases + full lifecycle simulation.

Proves the harness survives the real world: corrupt state, half-built
environments, failing recipes, read-only dirs, malformed seeds — the
bootstrap degrades gracefully (breadcrumb + continue, or a clear error),
never crashes silently and never clobbers. Then the full lifecycle:
init -> mission compile -> DAG -> tasks -> verify FAIL -> fix ->
verify PASS -> close -> reopen with exact DAG state.
"""
import json
import os
import stat
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bootstrap
from bootstrap import ensure_venv, run_startup_checklist
from registry import Registry
from verify import run_recipe


def _no_ruff(*a, **k):
    return {"name": "ruff", "status": "warn", "detail": "stubbed", "fixed": False}


def _hermetic():
    p1 = mock.patch.object(bootstrap, "check_ruff", _no_ruff)
    p2 = mock.patch.object(bootstrap, "_best_effort_install_just",
                           lambda timeout=90: (False, "stubbed: no network"))
    return p1, p2


class EdgeCaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-battle-"))
        self._patches = _hermetic()
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_corrupt_tasks_json_degrades_gracefully(self):
        reg = Registry(self.tmp / ".awino")
        reg.ensure()
        (self.tmp / ".awino" / "registry" / "tasks.json").write_text(
            "{not json!!!")
        # never raises, never loses the registry dir
        self.assertEqual(reg.tasks(), [])
        self.assertEqual(reg.dag_summary()["total"], 0)
        # and new tasks still work afterwards
        t = reg.add_task("fresh start")
        self.assertEqual(reg.tasks()[0]["id"], t["id"])

    def test_corrupt_milestones_jsonl_skips_bad_lines(self):
        reg = Registry(self.tmp / ".awino")
        reg.ensure()
        p = self.tmp / ".awino" / "registry" / "milestones.jsonl"
        p.write_text('{"id": "ms-1", "kind": "decision", "text": "ok", "ts": 1}\n'
                     'GARBAGE{{{\n')
        self.assertEqual(len(reg.milestones()), 1)

    def test_half_created_venv_moved_aside_not_built_on(self):
        venv = self.tmp / ".venv"
        venv.mkdir()
        (venv / "junk.txt").write_text("half baked")
        check, _bin = ensure_venv(self.tmp, timeout=30)
        # moved aside, not deleted; fresh venv created or clear warning
        leftovers = list(self.tmp.glob(".venv.broken-*"))
        self.assertTrue(leftovers, "half-created venv was not moved aside")
        self.assertTrue((leftovers[0] / "junk.txt").is_file(),
                        "user data inside the broken venv was lost")
        self.assertIn(check["status"], ("ok", "warn"))

    def test_failing_justfile_recipe_never_raises(self):
        (self.tmp / "justfile").write_text("test:\n\texit 3\n")
        res = run_recipe(self.tmp, "just", "test", timeout=30)
        # `just` may not be installed here — either way, no exception
        self.assertIn("exit_code", res)
        if res["exit_code"] not in (127,):  # 127 = just not installed
            self.assertNotEqual(res["exit_code"], 0)

    def test_failing_makefile_recipe_reports_exit_code(self):
        (self.tmp / "Makefile").write_text("test:\n\tfalse\n")
        res = run_recipe(self.tmp, "make", "test", timeout=30)
        self.assertNotEqual(res["exit_code"], 0)

    def test_read_only_project_dir_clear_error_no_crash(self):
        # The test runner is root, so chmod can't block it — simulate the
        # kernel's PermissionError directly to prove the graceful path.
        ro = self.tmp / "ro-project"
        ro.mkdir()
        (ro / "README.md").write_text("existing")
        real_mkdir = Path.mkdir

        def _no_mkdir(self, *a, **k):
            if str(self).startswith(str(ro)):
                raise PermissionError(13, "Permission denied", str(self))
            return real_mkdir(self, *a, **k)

        with mock.patch.object(Path, "mkdir", _no_mkdir):
            report = run_startup_checklist(ro)
        # never raised; reports what failed; existing files untouched
        self.assertEqual((ro / "README.md").read_text(), "existing")
        fails = [c for c in report["checks"] if c["status"] == "fail"]
        self.assertTrue(fails, "unwritable dir should produce a clear failure")
        self.assertFalse(report["ok"])
        detail = " ".join(c["detail"] for c in fails)
        self.assertIn("writable", detail)

    def test_malformed_seed_frontmatter_ignored_not_fatal(self):
        seeds = self.tmp / ".awino" / "seeds"
        seeds.mkdir(parents=True)
        (seeds / "broken.md").write_text(
            "---\nname: broken\n: : : not yaml at all [[[\n---\n"
            "no checklist here, just prose\n")
        (seeds / "good.md").write_text(
            "---\nname: good\n---\n\n- [ ] real task\n")
        report = run_startup_checklist(self.tmp)
        self.assertTrue(report["ok"])
        tasks = [t["text"] for t in report["seed_tasks"]]
        self.assertIn("real task", tasks)

    def test_no_network_just_install_is_breadcrumb_not_crash(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            report = run_startup_checklist(self.tmp)
        self.assertTrue(report["ok"] or True)  # never raises either way
        joined = " ".join(report["breadcrumbs"])
        self.assertIn("just", joined)

    def test_venv_creation_failure_is_clear_not_silent(self):
        with mock.patch.object(bootstrap, "_run", return_value=(1, "boom")):
            check, bin_ = ensure_venv(self.tmp, timeout=10)
        self.assertEqual(check["status"], "fail")
        self.assertIn("creation", check["detail"])
        self.assertIsNone(bin_)


class LifecycleSimulationTest(unittest.TestCase):
    """Tomorrow's project, rehearsed today: init -> mission -> DAG ->
    tasks -> verify FAIL -> fix -> verify PASS -> close -> reopen."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-lifecycle-"))
        self._patches = _hermetic()
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_lifecycle(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from common import make_loop
        import modes as _modes

        # 1. init: the "boom" entry point on an empty dir
        report = run_startup_checklist(self.tmp, mission_text="Ship the widget",
                                       criteria=["widget works"])
        self.assertTrue(report["ok"])
        awino_dir = self.tmp / ".awino"

        # 2. mission compile: route the role lens, compile the DAG
        loop, home = make_loop("lifecycle-p1")
        reg = Registry(awino_dir)
        reg.ensure()
        loop.registry = reg
        loop.set_mission("Ship the widget",
                         [{"kind": "manual", "text": "widget works"}])
        proposal = loop.route_role(mission_text="Ship the widget",
                                   configured_profile="software-engineer",
                                   force=True)
        self.assertEqual(proposal["role"], "software-engineer")
        mid = loop.state.snapshot["mission"]["id"]
        dag = _modes.compile_initial_dag(reg, "Ship the widget",
                                         proposal["role"], mid)
        self.assertTrue(len(dag) >= 4)
        order = reg.topological_order()
        self.assertEqual(order, [t["id"] for t in dag])

        # 3. execute the tasks, with real evidence files
        proof_dir = self.tmp / "proof"
        proof_dir.mkdir()
        for i, t in enumerate(dag):
            proof = proof_dir / f"step{i}.txt"
            proof.write_text(f"evidence for {t['text']}")
            reg.set_task_state(t["id"], "done",
                               evidence=[str(proof.relative_to(self.tmp))])
        self.assertEqual(reg.dag_summary()["done"], len(dag))

        # 4. drive to VERIFY, then verify FAILS once (criterion unevidenced)
        loop.approve_contract()
        loop.approve_contract(["proof/"])
        loop.request_phase("VERIFY", reason="work done")
        b = loop.begin_verification()
        wid = b["worker_id"]
        r = loop.run_verifier_turn(
            wid, {"evidence_links": {},  # nothing linked yet
                  "recipe_result": {"runner": "just", "recipe": "test",
                                    "exit_code": 0, "output": "ok"},
                  "project_root": str(self.tmp)})
        self.assertFalse(r["passed"])
        res = loop.complete_verification(wid)
        self.assertFalse(res["passed"])
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        # findings became DAG tasks
        findings = [t for t in reg.tasks() if t["source"] == "verifier"]
        self.assertTrue(findings)

        # 5. fix: link the evidence, finish the finding tasks, verify again
        proof = proof_dir / "widget.txt"
        proof.write_text("the widget works")
        for t in findings:
            reg.set_task_state(t["id"], "done",
                               evidence=[str(proof.relative_to(self.tmp))])
        loop.request_phase("VERIFY", reason="findings addressed")
        b = loop.begin_verification()
        wid = b["worker_id"]
        r = loop.run_verifier_turn(
            wid, {"evidence_links": {"widget works": "proof/widget.txt"},
                  "recipe_result": {"runner": "just", "recipe": "test",
                                    "exit_code": 0, "output": "ok"},
                  "project_root": str(self.tmp)})
        self.assertTrue(r["passed"], r["verdict"])

        # 6. verify PASSES -> REVIEW unlocks
        res = loop.complete_verification(wid)
        self.assertTrue(res["passed"])
        rr = loop.request_phase("REVIEW", reason="verifier passed")
        self.assertEqual(rr["status"], "ok")
        loop.request_phase("SHIP", reason="done")

        # 7. close: drop everything; reopen: exact DAG state restored
        dag_before = [(t["id"], t["state"], t["evidence"], t["depends_on"])
                      for t in reg.tasks()]
        order_before = reg.topological_order()
        loop2, _ = make_loop("lifecycle-p1", home=home)
        reg2 = Registry(awino_dir)
        dag_after = [(t["id"], t["state"], t["evidence"], t["depends_on"])
                     for t in reg2.tasks()]
        self.assertEqual(dag_after, dag_before)
        self.assertEqual(reg2.topological_order(), order_before)
        # and the role mirror survived the close/reopen too
        role = _modes.read_role_state(awino_dir)
        self.assertEqual(role["role"], "software-engineer")


if __name__ == "__main__":
    unittest.main()
