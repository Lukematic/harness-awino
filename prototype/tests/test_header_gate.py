"""Turn Header Contract + Elevator transition gate tests.

The header is rendered by the harness from code-owned state at the top of
every turn (pipeline stage 4); the validator asserts the turn echoes it back
EXACTLY. A header for a phase the sensor did not select is forgery.

The elevator is code-gated: DEFINE -> PLAN and PLAN -> BUILD happen only in
approve_contract(); BUILD -> VERIFY needs a diff + verified criteria;
VERIFY -> REVIEW needs exit code 0.
"""
import unittest

from tests.common import make_loop, T
from contract import HEADER_RE
from backends import ScriptedBackend, HostileBackend


class TestTurnHeader(unittest.TestCase):
    def test_header_is_first_line_and_agrees_with_state(self):
        backend = ScriptedBackend([
            T(plan=["P"], progress_delta="planning the fix",
              assumptions=["Cause: the empty-password path passes None."]),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Fix the login bug", ["manual"])
        r = loop.run_user_turn("fix the login bug")
        self.assertEqual(r["status"], "ok")
        first = backend.calls[0]["contract"].splitlines()[0]
        m = HEADER_RE.match(first)
        self.assertIsNotNone(m, f"header malformed: {first!r}")
        (phase, mode, stance, skills, loop_no, run, kn, km, mid) = m.groups()
        s = loop.state.snapshot
        self.assertEqual(phase, "DEFINE")
        self.assertEqual(mode, "build")  # fix intent overrides the floor default
        self.assertEqual(stance, "first-principles")
        self.assertEqual(skills, "repo,code")
        self.assertEqual(loop_no, "1")
        self.assertEqual(kn, "0")
        self.assertEqual(km, "1")
        self.assertEqual(mid, s["mission"]["id"])
        self.assertEqual(run, s["conversation_id"])

    def test_header_tracks_phase_and_stance_across_turns(self):
        backend = ScriptedBackend([
            T(plan=["P"], progress_delta="planned",
              assumptions=["Cause: the empty-password path."],
              questions=["Should the fix handle empty string too?"]),
            T(objective="Evaluate rewrite proposal", plan=["P"],
              assumptions=["Rewriting discards years of edge cases and forces a full re-audit.",
                           "No known safety bugs exist, so the rewrite buys little.",
                           "If we are wrong, the tripwire is the canary deploy."],
              progress_delta="Restating: you propose rewriting auth in Rust. Counter-case in assumptions."),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Fix the login bug", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.run_user_turn("I think we should rewrite the auth module in Rust")
        second = backend.calls[1]["contract"].splitlines()[0]
        m = HEADER_RE.match(second)
        self.assertIsNotNone(m)
        (phase, mode, stance, skills, loop_no, run, kn, km, mid) = m.groups()
        self.assertEqual(phase, "PLAN")
        self.assertEqual(mode, "plan")  # opinion intent: read-only
        self.assertEqual(stance, "steel-man")
        self.assertEqual(skills, "domain")

    def test_omitted_header_is_rejected(self):
        loop, _ = make_loop(backend=HostileBackend(["omit_header"]))
        loop.set_mission("Fix the login bug", ["manual"])
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("header", r["said"])
        self.assertEqual(loop.state.snapshot["turn_count"], 0)
        self.assertFalse(any(e["type"] == "turn_completed"
                             for e in loop.state.events))

    def test_falsified_header_is_rejected(self):
        loop, _ = make_loop(backend=HostileBackend(["forge_header"]))
        loop.set_mission("Fix the login bug", ["manual"])
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("falsified", r["said"])
        # The forged phase SHIP must not leak into state.
        self.assertEqual(loop.state.snapshot["phase"], "DEFINE")
        self.assertFalse(loop.state.snapshot["done"])

    def test_header_phase_must_match_sensor_selection(self):
        # Pipeline order test: the sensor selected DEFINE, so a header
        # claiming any other phase is rejected even if well-formed.
        loop, _ = make_loop(backend=HostileBackend(["forge_header"]))
        loop.set_mission("Fix the login bug", ["manual"])
        r = loop.run_user_turn("fix the login bug")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("phase", r["said"])
        self.assertIn("SHIP", r["said"])

    def test_header_format_exact(self):
        line = ("[A.W.I.N.O. | phase: BUILD | mode: build | stance: first-principles | "
                "skills: repo,code | loop: 3 | run: abc123 | knowledge: 1/2 | "
                "mission: m-a1b2c3d4]")
        self.assertIsNotNone(HEADER_RE.match(line))
        self.assertIsNone(HEADER_RE.match(
            "[A.W.I.N.O. | phase: build | mode: build | stance: x | skills:  | "
            "loop: 3 | run: r | knowledge: 0/1 | mission: m]"))


class TestElevatorGate(unittest.TestCase):
    def _loop_at_build(self, scope=("fix.py",)):
        backend = ScriptedBackend([
            T(plan=["Write the fix"], progress_delta="Planning.",
              assumptions=["Cause: the empty-password path."]),
            T(plan=["Write the fix"],
              tool_calls=[{"name": "write_file",
                           "args": {"path": "fix.py", "content": "patched"}}],
              progress_delta="Writing the fix.",
              assumptions=["Cause: the empty-password path passes None."]),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Fix the login bug", ["artifact:fix.py"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(list(scope))
        return loop

    def test_build_unreachable_without_contract_approval(self):
        loop, _ = make_loop(backend=ScriptedBackend([
            T(plan=["Write the fix"],
              tool_calls=[{"name": "write_file",
                           "args": {"path": "fix.py", "content": "x"}}],
              progress_delta="Writing.",
              assumptions=["Cause: the bug is in fix.py."]),
        ]))
        loop.set_mission("Fix the login bug", ["artifact:fix.py"])
        r = loop.run_user_turn("fix it now")
        # No approved contract/SCOPE: the write is rejected; the elevator
        # never moves and no file is created.
        self.assertEqual(r["status"], "escalated")
        self.assertEqual(loop.state.snapshot["phase"], "DEFINE")
        self.assertFalse((loop.sandbox.root / "fix.py").exists())

    def test_scoped_contract_approval_opens_build(self):
        loop = self._loop_at_build()
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        self.assertEqual(loop.state.snapshot["scope"], ["fix.py"])
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "awaiting_approval")
        loop.approve()
        self.assertTrue((loop.sandbox.root / "fix.py").exists())
        # Exit gate: diff produced + artifact criterion verified -> VERIFY.
        self.assertEqual(loop.state.snapshot["phase"], "VERIFY")

    def test_write_outside_scope_is_rejected(self):
        loop = self._loop_at_build(scope=("fix.py",))
        # 4 copies: 1 initial + 3 bounded retries, all rejected the same way.
        loop.backend = ScriptedBackend([
            T(plan=["Write elsewhere"],
              tool_calls=[{"name": "write_file",
                           "args": {"path": "other.py", "content": "x"}}],
              progress_delta="Writing outside scope.",
              assumptions=["Cause: elsewhere."]),
        ] * 4)
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("SCOPE", r["said"])
        self.assertFalse((loop.sandbox.root / "other.py").exists())
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")

    def test_verify_to_review_needs_exit_zero(self):
        loop = self._loop_at_build()
        loop.run_user_turn("fix it now")
        loop.approve()
        self.assertEqual(loop.state.snapshot["phase"], "VERIFY")
        # A VERIFY turn with no passing command: elevator stays.
        loop.backend = ScriptedBackend([
            T(plan=["Verify"], progress_delta="Looking at the diff.",
              assumptions=["The file exists but the guard may not cover all "
                           "empty inputs; the evidence could be stale."]),
        ])
        loop.run_user_turn("verify it")
        self.assertEqual(loop.state.snapshot["phase"], "VERIFY")
        # A VERIFY turn that runs a command with exit 0: elevator moves.
        loop.backend = ScriptedBackend([
            T(plan=["Verify"],
              tool_calls=[{"name": "run_command", "args": {"cmd": "true"}}],
              progress_delta="Running verification: exit 0.",
              assumptions=["The command ran in the sandbox, but it does not "
                           "exercise the empty-password path itself."]),
        ])
        loop.run_user_turn("verify it")
        self.assertEqual(loop.state.snapshot["phase"], "REVIEW")

    def test_contract_approval_resets_on_new_mission(self):
        loop, _ = make_loop()
        loop.set_mission("Task A", ["manual"])
        loop.approve_contract()
        self.assertTrue(loop.state.snapshot["contract_approved"])
        loop.set_mission("Task B", ["manual"])
        self.assertFalse(loop.state.snapshot["contract_approved"])
        self.assertIsNone(loop.state.snapshot["scope"])

    def test_approve_contract_without_mission(self):
        loop, _ = make_loop()
        r = loop.approve_contract()
        self.assertEqual(r["status"], "none")


if __name__ == "__main__":
    unittest.main()
