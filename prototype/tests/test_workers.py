"""Phase D: worker delegation with file ownership and shared budgets.

Workers are child Loops with:
- own project dir: <home>/projects/<parent>/workers/<wid>/
- fixed SCOPE = owned_files (cannot be changed by the worker)
- budget drawn from the parent's shared pool

Tests:
- two workers, disjoint owned files
- worker A cannot write B's files (refused)
- shared turn budget: parent max_turns=4, two workers x2 turns each
  -> third worker spawn refused (budget exhausted)
- collect merges worker journal + artifacts
"""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend


class TestWorkerDelegation(unittest.TestCase):
    def test_spawn_worker_creates_isolated_project(self):
        loop, home = make_loop()
        w = loop.spawn_worker("Write docs", owned_files=["docs/"],
                              budget_share={"max_turns": 2})
        self.assertTrue(w["worker_id"].startswith("w-"))
        self.assertIn("workers", w["project_dir"])
        # worker has its own Loop
        self.assertIsNotNone(w["loop"])
        # worker scope is fixed to owned_files
        self.assertEqual(w["loop"].state.snapshot["scope"], ["docs/"])

    def test_worker_cannot_write_outside_owned_files(self):
        loop, home = make_loop()
        w = loop.spawn_worker("Write docs", owned_files=["docs/"],
                              budget_share={"max_turns": 5})
        wloop = w["loop"]
        # Direct scope enforcement test (bypasses the full turn pipeline;
        # the pipeline is tested separately)
        result = wloop._execute_single(
            "t1.0", "write_file",
            {"path": "../other/evil.txt", "content": "x"},
            "idem-test-1")
        self.assertIn("ScopeViolation", str(result["result"]))
        # no file was written outside
        from pathlib import Path
        self.assertFalse(Path(home, "other/evil.txt").exists())
        # But writing inside owned_files works
        result2 = wloop._execute_single(
            "t1.1", "write_file",
            {"path": "docs/ok.txt", "content": "x"},
            "idem-test-2")
        self.assertNotIn("ScopeViolation", str(result2["result"]))

    def test_shared_budget_exhaustion(self):
        loop, home = make_loop(config={"max_turns": 4})
        w1 = loop.spawn_worker("Task 1", owned_files=["a/"],
                               budget_share={"max_turns": 2})
        w2 = loop.spawn_worker("Task 2", owned_files=["b/"],
                               budget_share={"max_turns": 2})
        # budget exhausted: 2+2=4 = parent max
        with self.assertRaises(Exception) as ctx:
            loop.spawn_worker("Task 3", owned_files=["c/"],
                              budget_share={"max_turns": 1})
        self.assertIn("budget", str(ctx.exception).lower())

    def test_collect_worker_merges_artifacts(self):
        loop, home = make_loop()
        w = loop.spawn_worker("Write file", owned_files=["out/"],
                              budget_share={"max_turns": 5})
        wloop = w["loop"]
        # Write via the worker's scoped execution
        result = wloop._execute_single(
            "t1.0", "write_file",
            {"path": "out/result.txt", "content": "done"},
            "idem-collect-1")
        self.assertNotIn("error", str(result["result"]).lower())
        # collect
        result = loop.collect_worker(w["worker_id"])
        self.assertEqual(result["status"], "collected")
        self.assertIn("out/result.txt", str(result["artifacts"]))


if __name__ == "__main__":
    unittest.main()
