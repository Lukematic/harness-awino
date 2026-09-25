"""Done-forgery: done_claim=true with unverified criteria must NEVER
terminate the loop. The most important test in the suite."""
import unittest

from tests.common import make_loop, T, drive_verification
from backends import HostileBackend, ScriptedBackend


def plan_write_verify():
    """Script: plan turn, build write turn, verify turn, done-claim turn.

    v0.6: approve() resumes the recursive loop instead of finalizing, so
    the resumed round consumes the verify turn (the phase is VERIFY by
    then — the elevator fires on the drained write effects). The "run the
    tests" turn that follows is a no-call observation round.
    """
    return [
        T(plan=["Write the fix"], progress_delta="Planning.",
          assumptions=["Cause: the empty-password path."]),
        T(plan=["Write the fix"],
          tool_calls=[{"name": "write_file",
                       "args": {"path": "fix.py", "content": "patched"}}],
          progress_delta="Writing the fix (needs approval).",
          assumptions=["Cause: the empty-password path passes None."]),
        # Consumed by the resumed round after approve(): phase is VERIFY,
        # mode is verify, stance is devil's-advocate — the assumptions
        # carry a substantive attack on the result.
        T(plan=["Verify"],
          tool_calls=[{"name": "run_command", "args": {"cmd": "true"}}],
          progress_delta="Tests pass: exit 0.",
          assumptions=["The command ran green, but it does not itself "
                       "exercise the empty-password path."]),
        # The "run the tests" turn: tests already ran in the resume round;
        # a no-call round that observes and exits.
        T(plan=["Verify"],
          progress_delta="Tests already green from the approval resume.",
          assumptions=["The verify round's exit 0 stands untouched; "
                       "re-running the suite would add no new evidence."]),
    ]


def drive_to_review(loop):
    """Approve the contract + scope, run the plan/write script, then
    pass the verifier gate (Track G) to reach REVIEW.

    v0.6: approve() resumes the recursive loop, and the resumed round
    runs the verify turn itself — there is no separate "run the tests"
    user turn anymore. Track G auto-verify may already advance VERIFY ->
    REVIEW when the criteria hold; only drive the manual verifier if the
    phase is still VERIFY.
    """
    loop.run_user_turn("draft the plan")
    loop.approve_contract()
    loop.approve_contract(["fix.py"])
    loop.run_user_turn("fix it now")
    loop.approve()
    assert loop.state.snapshot["phase"] in ("VERIFY", "REVIEW"), \
        loop.state.snapshot["phase"]
    if loop.state.snapshot["phase"] == "VERIFY":
        # Track G: the verifier worker must journal a pass verdict.
        res = drive_verification(
            loop, evidence_links={"artifact exists: fix.py": "tests/common.py",
                                  "manual (operator sign-off)": "tests/common.py",
                                  "event: tool_result": "tests/common.py"})
        assert res["passed"], res.get("said")
        r = loop.request_phase("REVIEW", reason="verifier passed")
        assert r["status"] == "ok", r
    assert loop.state.snapshot["phase"] == "REVIEW", loop.state.snapshot["phase"]


class TestDoneForgery(unittest.TestCase):
    def test_forged_done_never_terminates(self):
        loop, _ = make_loop(backend=HostileBackend(["forge_done"] * 8))
        loop.set_mission("Fix the login bug", ["artifact:fix.py", "manual"])
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("forgery", r["said"])
        s = loop.state.snapshot
        self.assertFalse(s["done"])
        self.assertFalse(any(e["type"] == "mission_done" for e in loop.state.events))
        # operator re-engages and the forger keeps trying: still never done
        r = loop.run_user_turn("continue")
        self.assertEqual(r["status"], "escalated")
        self.assertFalse(loop.state.snapshot["done"])
        self.assertFalse(any(e["type"] == "mission_done" for e in loop.state.events))

    def test_forged_done_rejected_even_when_close(self):
        # artifact exists, but the manual criterion still needs the operator:
        # a model done_claim must still be rejected.
        loop, _ = make_loop(backend=HostileBackend(["forge_done"]))
        loop.set_mission("Fix the login bug", ["artifact:fix.py", "manual"])
        (loop.sandbox.root / "fix.py").write_text("patched")
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("manual", r["said"])
        self.assertFalse(loop.state.snapshot["done"])

    def test_legitimate_completion_computed_not_claimed(self):
        # Positive path: real work -> criteria verify in code -> done.
        # No manual criterion here, so a verified done_claim may complete.
        backend = ScriptedBackend(plan_write_verify() + [
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
        drive_to_review(loop)
        r = loop.run_user_turn("ship it")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(loop.state.snapshot["done"])
        self.assertEqual(loop.state.snapshot["phase"], "SHIP")
        self.assertTrue(any(e["type"] == "mission_done" for e in loop.state.events))

    def test_done_only_via_operator_when_manual_criterion(self):
        backend = ScriptedBackend(plan_write_verify())
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Fix the login bug", ["artifact:fix.py", "manual"])
        drive_to_review(loop)
        # operator /done completes it (manual criterion satisfied by /done itself)
        r = loop.request_done()
        self.assertEqual(r["status"], "done")
        self.assertTrue(loop.state.snapshot["done"])
        # and /done with unmet criteria is refused with gaps
        loop2, _ = make_loop()
        loop2.set_mission("Fix the login bug", ["artifact:missing.py", "manual"])
        r = loop2.request_done()
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(any("missing.py" in g for g in r["gaps"]))
        self.assertFalse(loop2.state.snapshot["done"])

    def test_done_claim_without_current_revision_evidence_is_refused(self):
        # A write result from a previous mission revision cannot satisfy the
        # done criterion of the current one.
        loop, _ = make_loop()
        loop.set_mission("Write the old file",
                         ["artifact:fix.py", "event:tool_result:write_file"])
        # simulate completed old work at mission_rev 0
        loop.state.record("tool_result", {"call_id": "t-old",
                                          "tool": "write_file",
                                          "args": {"path": "fix.py"},
                                          "result": {"ok": True},
                                          "mission_rev": 0})
        (loop.sandbox.root / "fix.py").write_text("patched")
        loop.set_mission("Write the new file",
                         ["artifact:fix.py", "event:tool_result:write_file"])
        loop.state.record("phase_changed", {"phase": "REVIEW"})
        r = loop.request_done()
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(any("write_file" in g for g in r["gaps"]))
        self.assertFalse(loop.state.snapshot["done"])


if __name__ == "__main__":
    unittest.main()
