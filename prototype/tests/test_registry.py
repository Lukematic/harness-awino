"""Track B: memory registry — milestones, breadcrumbs, task tracker, audit."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from registry import Registry


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-registry-"))
        self.awino = self.tmp / ".awino"
        self.reg = Registry(self.awino)
        self.reg.ensure()

    def test_auto_created_on_first_mission(self):
        fresh = Registry(Path(tempfile.mkdtemp(prefix="awino-reg-fresh-"))
                         / ".awino")
        self.assertFalse(fresh.exists)
        res = fresh.ensure()
        self.assertTrue(res["created"])
        self.assertTrue(fresh.exists)
        self.assertTrue(
            (fresh.awino_dir / "registry" / "meta.json").is_file())
        # idempotent
        res2 = fresh.ensure()
        self.assertFalse(res2["created"])

    def test_tasks_transition_correctly(self):
        t = self.reg.add_task("write the spec")
        self.assertEqual(t["state"], "open")
        self.reg.set_task_state(t["id"], "doing")
        self.reg.set_task_state(t["id"], "blocked")
        self.reg.set_task_state(t["id"], "doing")
        self.reg.set_task_state(t["id"], "done")
        tasks = self.reg.tasks()
        self.assertEqual(tasks[0]["state"], "done")
        self.assertEqual([h["state"] for h in tasks[0]["history"]],
                         ["open", "doing", "blocked", "doing", "done"])

    def test_task_state_filters(self):
        a = self.reg.add_task("a")
        b = self.reg.add_task("b")
        self.reg.set_task_state(b["id"], "done")
        self.assertEqual([t["id"] for t in self.reg.tasks(state="open")],
                         [a["id"]])
        self.assertEqual([t["id"] for t in self.reg.tasks(state="done")],
                         [b["id"]])

    def test_invalid_task_state_raises(self):
        t = self.reg.add_task("x")
        with self.assertRaises(ValueError):
            self.reg.add_task("y", state="vapor")
        with self.assertRaises(ValueError):
            self.reg.set_task_state(t["id"], "vapor")
        with self.assertRaises(KeyError):
            self.reg.set_task_state("t-nope", "done")

    def test_close_reopen_resumes_from_breadcrumbs(self):
        self.reg.add_breadcrumb("m-1", "hit a wall on the auth bug",
                                stop_point="BUILD floor, auth.py line 42")
        self.reg.add_breadcrumb("m-1", "parked: ask operator about API keys")
        # "close": a fresh Registry instance on the same project dir
        reopened = Registry(self.awino)
        self.assertTrue(reopened.exists)
        self.assertEqual(reopened.last_stop_point("m-1"),
                         "BUILD floor, auth.py line 42")
        notes = [b["note"] for b in reopened.breadcrumbs("m-1")]
        self.assertIn("parked: ask operator about API keys", notes)

    def test_milestones_recorded(self):
        m = self.reg.add_milestone("decision", "chose sqlite over postgres")
        self.assertEqual(m["kind"], "decision")
        self.reg.add_milestone("completion", "shipped v1")
        kinds = [x["kind"] for x in self.reg.milestones()]
        self.assertEqual(kinds, ["decision", "completion"])
        with self.assertRaises(ValueError):
            self.reg.add_milestone("vibe", "not a kind")

    def test_import_seed_tasks_and_dedupe(self):
        seeds = [{"text": "task one", "done": False, "source": "seed:a"},
                 {"text": "task two", "done": True, "source": "seed:a"}]
        self.assertEqual(self.reg.import_seed_tasks(seeds), 2)
        self.assertEqual(self.reg.import_seed_tasks(seeds), 0)  # deduped
        states = {t["text"]: t["state"] for t in self.reg.tasks()}
        self.assertEqual(states, {"task one": "open", "task two": "done"})

    def test_audit_flags_outdated_and_assumptions(self):
        import time
        old = time.time() - 60 * 86400  # 60 days ago
        self.reg.add_milestone("decision", "assumed the API was stable",
                               assumption=True)
        self.reg.add_task("ancient open task", ts=old)
        done_old = self.reg.add_task("long-done task", ts=old)
        self.reg.set_task_state(done_old["id"], "done")
        audit = self.reg.audit(outdated_days=30)
        by_text = {i["text"]: i["status"] for i in audit["items"]}
        self.assertEqual(by_text["assumed the API was stable"], "assumption")
        self.assertEqual(by_text["ancient open task"], "outdated")
        # done is terminal: never flagged outdated
        self.assertEqual(by_text["long-done task"], "fact")
        self.assertEqual(audit["counts"]["assumption"], 1)
        self.assertEqual(audit["counts"]["outdated"], 1)
        self.assertTrue(isinstance(audit["summary"], str)
                        and len(audit["summary"]) > 20)

    def test_contract_section_loads_state(self):
        self.reg.add_milestone("decision", "chose the design")
        t = self.reg.add_task("implement it")
        self.reg.add_breadcrumb("m-9", "working on it",
                                stop_point="BUILD floor")
        section = self.reg.contract_section()
        self.assertIn("REGISTRY", section)
        self.assertIn("chose the design", section)
        self.assertIn("implement it", section)
        self.assertIn(t["id"], section)
        self.assertIn("BUILD floor", section)

    def test_contract_section_empty_when_no_state(self):
        fresh = Registry(Path(tempfile.mkdtemp(prefix="awino-reg-empty-"))
                         / ".awino")
        fresh.ensure()
        self.assertEqual(fresh.contract_section(), "")
        self.assertEqual(Registry(self.tmp / "elsewhere").contract_section(),
                         "")


if __name__ == "__main__":
    unittest.main()
