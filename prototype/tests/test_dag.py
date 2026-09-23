"""Track F: task DAG store — dependencies, ordering, blocking, persistence."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from registry import Registry
import modes


class DAGTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awino-dag-"))
        self.reg = Registry(self.tmp / ".awino")
        self.reg.ensure()

    def _chain(self, n=3):
        tasks = []
        prev = None
        for i in range(n):
            t = self.reg.add_task(f"step {i}", depends_on=[prev] if prev else [],
                                  done_criteria=f"criterion {i}")
            tasks.append(t)
            prev = t["id"]
        return tasks

    def test_topological_order_respects_dependencies(self):
        tasks = self._chain(3)
        order = self.reg.topological_order()
        self.assertEqual(order, [t["id"] for t in tasks])

    def test_downstream_blocked_until_deps_done(self):
        a, b, c = self._chain(3)
        # b and c cannot complete before their deps
        with self.assertRaises(ValueError) as ctx:
            self.reg.set_task_state(c["id"], "done")
        self.assertIn("blocked", str(ctx.exception).lower())
        with self.assertRaises(ValueError):
            self.reg.set_task_state(b["id"], "done")
        # complete in order
        self.reg.set_task_state(a["id"], "done", evidence=["proof-a.md"])
        self.reg.set_task_state(b["id"], "done", evidence=["proof-b.md"])
        self.reg.set_task_state(c["id"], "done", evidence=["proof-c.md"])
        self.assertEqual(self.reg.dag_summary()["done"], 3)

    def test_whats_next_returns_only_unblocked(self):
        a, b, c = self._chain(3)
        nxt = self.reg.whats_next()
        self.assertEqual([t["id"] for t in nxt], [a["id"]])
        self.reg.set_task_state(a["id"], "done", evidence=["x"])
        nxt = self.reg.whats_next()
        self.assertEqual([t["id"] for t in nxt], [b["id"]])

    def test_blocked_by_names_blockers(self):
        a, b = self._chain(2)
        blockers = self.reg.blocked_by(b["id"])
        self.assertEqual([x["id"] for x in blockers], [a["id"]])
        self.reg.set_task_state(a["id"], "done", evidence=["x"])
        self.assertEqual(self.reg.blocked_by(b["id"]), [])

    def test_unknown_dependency_refused_plainly(self):
        with self.assertRaises(ValueError) as ctx:
            self.reg.add_task("orphan", depends_on=["t-nope"])
        self.assertIn("unknown task id", str(ctx.exception))

    def test_cycle_detected_not_silently_ignored(self):
        a = self.reg.add_task("a")
        b = self.reg.add_task("b", depends_on=[a["id"]])
        # hand-edit a cycle into tasks.json (simulates external corruption)
        import json
        p = self.tmp / ".awino" / "registry" / "tasks.json"
        data = json.loads(p.read_text())
        data[a["id"]]["depends_on"] = [b["id"]]
        p.write_text(json.dumps(data))
        with self.assertRaises(ValueError) as ctx:
            self.reg.topological_order()
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_close_reopen_restores_exact_dag_state(self):
        tasks = self._chain(3)
        self.reg.set_task_state(tasks[0]["id"], "done", evidence=["proof.md"])
        before = self.reg.topological_order()
        before_states = {t["id"]: (t["state"], t["evidence"],
                                   t["depends_on"], t["done_criteria"])
                         for t in self.reg.tasks()}
        # "close": drop the object; "reopen": fresh instance on same dir
        reopened = Registry(self.tmp / ".awino")
        self.assertEqual(reopened.topological_order(), before)
        after_states = {t["id"]: (t["state"], t["evidence"],
                                  t["depends_on"], t["done_criteria"])
                        for t in reopened.tasks()}
        self.assertEqual(after_states, before_states)
        # next unblocked work is identical after reopen
        self.assertEqual([t["id"] for t in reopened.whats_next()],
                         [t["id"] for t in self.reg.whats_next()])
        self.assertEqual([t["id"] for t in reopened.whats_next()],
                         [tasks[1]["id"]])

    def test_old_tasks_without_dag_fields_still_work(self):
        t = self.reg.add_task("legacy task")
        self.assertEqual(t.get("depends_on"), [])
        self.assertEqual(t.get("evidence"), [])
        # blocking checks tolerate missing keys
        self.reg.set_task_state(t["id"], "done")
        self.assertEqual(self.reg.dag_summary()["done"], 1)

    def test_dag_summary_counts(self):
        # "blocked" = directly blocked (its own deps aren't all done) — the
        # same definition blocked_by() uses. After the first task in a chain
        # is done, only the tail is still directly blocked.
        tasks = self._chain(3)
        self.reg.set_task_state(tasks[0]["id"], "done", evidence=["x"])
        s = self.reg.dag_summary()
        self.assertEqual(s, {"total": 3, "done": 1, "doing": 0, "open": 2,
                             "blocked": 1, "next": [tasks[1]["id"]]})

    def test_contract_section_shows_dag_progress(self):
        self._chain(2)
        sec = self.reg.contract_section()
        self.assertIn("DAG progress", sec)
        self.assertIn("What's next", sec)

    def test_initial_dag_compiled_from_role_playbook(self):
        created = modes.compile_initial_dag(self.reg, "Ship the billing page",
                                            "software-engineer", "m-1")
        self.assertEqual(len(created),
                         len(modes.ROLES["software-engineer"]["decomposition_playbook"]))
        order = self.reg.topological_order()
        self.assertEqual(order, [t["id"] for t in created])
        # idempotent: second call creates nothing
        again = modes.compile_initial_dag(self.reg, "Ship the billing page",
                                          "software-engineer", "m-1")
        self.assertEqual(again, [])
        self.assertEqual(len(self.reg.tasks()), len(created))

    def test_completion_journaled_with_evidence(self):
        t = self.reg.add_task("write the thing", done_criteria="thing written")
        self.reg.set_task_state(t["id"], "doing")
        self.reg.set_task_state(t["id"], "done", evidence=["out/result.txt"])
        got = [x for x in self.reg.tasks() if x["id"] == t["id"]][0]
        self.assertEqual(got["evidence"], ["out/result.txt"])
        self.assertEqual([h["state"] for h in got["history"]],
                         ["open", "doing", "done"])


if __name__ == "__main__":
    unittest.main()
