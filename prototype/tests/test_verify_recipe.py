"""VERIFY runs the project's own test/lint recipes (team review 09-27):
the gate used to accept whatever command the model ran last, so a
passing `echo ok` counted as verification."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from common import make_loop  # noqa: E402


class VerifyRecipeTest(unittest.TestCase):
    def _loop_in_verify(self, makefile: str | None):
        loop, _ = make_loop(project="recipe")
        if makefile is not None:
            (loop.sandbox.root / "Makefile").write_text(makefile)
        loop.state.record("phase_changed", {"phase": "VERIFY",
                                            "reason": "test"})
        rev = loop.state.snapshot["mission_revision"]
        # The model "verified" with a command that proves nothing.
        loop.state.record("tool_result", {
            "call_id": "t1.0", "tool": "run_command",
            "args": {"cmd": "echo ok"}, "result": {"exit_code": 0,
                                                   "stdout": "ok"},
            "mission_rev": rev})
        return loop

    def test_failing_project_tests_route_back_to_build(self):
        loop = self._loop_in_verify("test:\n\t@echo 'FAILED test_slug' && false\n")
        loop._check_elevator_gates("t1")
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        runs = [e["data"] for e in loop.state.events
                if e["type"] == "tool_result" and e["data"].get("harness_recipe")]
        self.assertEqual(runs[0]["args"]["cmd"], "make test")
        self.assertIn("FAILED test_slug", runs[0]["result"]["stdout"])

    def test_lint_failure_also_routes_back(self):
        loop = self._loop_in_verify("test:\n\t@true\nlint:\n\t@echo 'E501 x.py' && false\n")
        loop._check_elevator_gates("t1")
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        cmds = [e["data"]["args"]["cmd"] for e in loop.state.events
                if e["type"] == "tool_result" and e["data"].get("harness_recipe")]
        self.assertEqual(cmds, ["make test", "make lint"])

    def test_passing_recipes_run_once_not_every_round(self):
        loop = self._loop_in_verify("test:\n\t@true\n")
        loop._check_elevator_gates("t1")
        loop._check_elevator_gates("t1")
        n = sum(1 for e in loop.state.events
                if e["type"] == "tool_result" and e["data"].get("harness_recipe"))
        self.assertEqual(n, 1)

    def test_no_recipe_keeps_the_old_behaviour(self):
        loop = self._loop_in_verify(None)
        loop._check_elevator_gates("t1")
        self.assertFalse(any(e["data"].get("harness_recipe")
                             for e in loop.state.events
                             if e["type"] == "tool_result"))


if __name__ == "__main__":
    unittest.main()
