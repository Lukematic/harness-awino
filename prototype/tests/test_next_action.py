"""Next-action line tests: the contract block's final line is compiled from
code-owned state and reflects the actual situation every turn.
"""
import unittest

from tests.common import make_loop, T
from contract import compile_contract, next_action_line
from backends import ScriptedBackend


def line(loop) -> str:
    return next_action_line(loop.state.snapshot)


class TestNextActionLine(unittest.TestCase):
    def test_no_mission(self):
        loop, _ = make_loop()
        l = line(loop)
        self.assertTrue(l.startswith("Floor: IDLE | Next action:"))
        self.assertIn("set a mission", l)
        self.assertIn("Blocked on: mission", l)

    def test_contract_ends_with_the_line(self):
        loop, _ = make_loop()
        contract = compile_contract(loop.state)
        self.assertEqual(contract.splitlines()[-1], line(loop))

    def test_define_wants_plan_then_contract_approval(self):
        loop, _ = make_loop()
        loop.set_mission("Fix the login bug", ["manual"])
        l = line(loop)
        self.assertIn("Floor: DEFINE", l)
        self.assertIn("approve", l)
        self.assertIn("/approve-contract", l)

    def test_plan_wants_scoped_contract_approval(self):
        loop, _ = make_loop()
        loop.set_mission("Fix the login bug", ["manual"])
        loop.approve_contract()
        self.assertEqual(loop.state.snapshot["phase"], "PLAN")
        l = line(loop)
        self.assertIn("Floor: PLAN", l)
        self.assertIn("SCOPE", l)
        self.assertIn("Blocked on: scoped contract approval", l)

    def test_scoped_plan_wants_build_actions(self):
        loop, _ = make_loop()
        loop.set_mission("Fix the login bug", ["manual"])
        loop.approve_contract()
        loop.approve_contract(["fix.py"])
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        l = line(loop)
        self.assertIn("build within SCOPE", l)

    def test_awaiting_approval(self):
        backend = ScriptedBackend([
            T(plan=["Write the fix"], progress_delta="Planning.",
              assumptions=["Cause: the bug."]),
            T(plan=["Write the fix"],
              tool_calls=[{"name": "write_file",
                           "args": {"path": "f.txt", "content": "x"}}],
              progress_delta="Writing (needs approval).",
              assumptions=["Cause: the bug is in f.txt."]),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Fix the login bug", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(["f.txt"])
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "awaiting_approval")
        l = line(loop)
        self.assertIn("decide on approval", l)
        self.assertIn("Blocked on: operator approval", l)

    def test_open_questions_block(self):
        backend = ScriptedBackend([
            T(plan=[], questions=["What exactly is broken?"],
              progress_delta="Need answers first."),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Fix the login bug", ["manual"])
        loop.run_user_turn("go")
        self.assertTrue(loop.state.snapshot["open_questions"])
        l = line(loop)
        self.assertIn("user answers", l)

    def test_verify_wants_exit_zero(self):
        loop, _ = make_loop()
        loop.set_mission("Fix the login bug", ["manual"])
        loop.state.record("phase_changed", {"phase": "VERIFY"})
        l = line(loop)
        self.assertIn("Floor: VERIFY", l)
        self.assertIn("exit code 0", l)

    def test_done_mission(self):
        backend = ScriptedBackend([
            T(plan=["Write the fix"], progress_delta="Planning.",
              assumptions=["Cause: the bug."]),
            T(plan=["Write the fix"],
              tool_calls=[{"name": "write_file",
                           "args": {"path": "fix.py", "content": "patched"}}],
              progress_delta="Writing (needs approval).",
              assumptions=["Cause: the empty-password path."]),
            T(plan=["Verify"],
              tool_calls=[{"name": "run_command", "args": {"cmd": "true"}}],
              progress_delta="Tests pass: exit 0.",
              assumptions=["The command ran green, but it does not itself "
                           "exercise the empty-password path."]),
            T(plan=["Ship"],
              assumptions=["The artifact could be stale if the sandbox was "
                           "reset; the tripwire is re-reading the file.",
                           "The event log could miss a crash-mid-effect; the "
                           "tripwire is the idempotency check."],
              progress_delta="Premortem done; claiming completion on evidence.",
              done_claim=True),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Fix the login bug",
                         ["artifact:fix.py", "event:tool_result:write_file"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(["fix.py"])
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "awaiting_approval")
        # v0.6: approving resumes the recursive loop, which drives the rest
        # autonomously — it runs the tests (exit 0), the elevator
        # auto-verifies (Track G: the verifier pass unlocks REVIEW), and the
        # done claim is verified against the done criteria in code. No
        # operator shepherding between approval and mission_done.
        r = loop.approve()
        self.assertEqual(r["status"], "ok")
        self.assertTrue(loop.state.snapshot["done"])
        self.assertEqual(loop.state.snapshot["phase"], "SHIP")
        l = line(loop)
        self.assertIn("Floor: SHIP", l)
        self.assertIn("Mission complete", l)

    def test_line_reflects_real_state_not_stale_copy(self):
        # The line is compiled from the live snapshot, so a phase change
        # is immediately visible in the next compiled contract.
        loop, _ = make_loop()
        loop.set_mission("Fix the login bug", ["manual"])
        before = compile_contract(loop.state).splitlines()[-1]
        loop.state.record("phase_changed", {"phase": "PLAN"})
        after = compile_contract(loop.state).splitlines()[-1]
        self.assertNotEqual(before, after)
        self.assertIn("Floor: PLAN", after)


if __name__ == "__main__":
    unittest.main()
