"""Per-turn contract loop: compile -> check -> refuse.

The loop compiles the objective->mission->tools->progress contract from
code-owned state before every turn and refuses broken contracts with NAMED
reasons — never silently. Refusal means the turn does not proceed and no
tool executes (asserted via zero `tool_called` events and an untouched
backend).

Note on hard pre-execute breaks (TOOL_NOT_GRANTED, SCOPE_INVALIDATED,
COMPLETION_WITHOUT_EVIDENCE): in the live pipeline the semantic validators
reject those turns first, so the contract's pre-execute check is defense in
depth there — it is unit-tested directly. The integration-visible refusals
are the pre-turn breaks (backend never called) and WRITE_WITHOUT_APPROVAL
(execution refused, routed to the approval gate).
"""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend
from contract_loop import (
    check_pre_execute, check_pre_turn, compile_turn_contract,
)


def build_mode_loop(**kw):
    """Loop approved into BUILD with SCOPE ["out.txt"], a plan, build mode."""
    loop, _ = make_loop(backend=ScriptedBackend([]), **kw)
    loop.set_mission("Write the file", ["manual"])
    loop.state.record("plan_updated", {"plan": ["Write it"]})
    loop.approve_contract()
    loop.approve_contract(["out.txt"])
    loop.state.record("mode_routed",
                      {"mode": "build",
                       "offered": ["read_file", "list_dir", "write_file"],
                       "reason": "test"})
    assert loop.state.snapshot["phase"] == "BUILD"
    return loop


def pre_execute(loop, turn):
    contract = compile_turn_contract(loop.state)
    return check_pre_execute(
        loop.state, contract, turn,
        has_valid_approval=loop._has_valid_approval,
        search_dirs=loop._search_dirs())


class TestContractCompilation(unittest.TestCase):
    def test_contract_compiles_from_state(self):
        loop = build_mode_loop()
        c = compile_turn_contract(loop.state)
        self.assertEqual(c["mode"], "build")
        self.assertIn("write_file", c["offered_tools"])
        self.assertIn("write_file", c["consequential_tools"])
        self.assertEqual(c["mission"]["revision"], 1)
        self.assertTrue(c["plan"]["present"])
        self.assertEqual(c["scope"], ["out.txt"])
        self.assertTrue(c["contract_approved"])
        self.assertEqual(len(c["criteria"]), 1)
        self.assertFalse(c["criteria_satisfied"])  # manual: unverified
        self.assertEqual(check_pre_turn(loop.state, c), [])


class TestPreTurnRefusal(unittest.TestCase):
    def test_no_plan_refuses_before_backend_acts(self):
        # BUILD floor, approved contract, but no plan in state.
        backend = ScriptedBackend(
            [T(plan=["Write it"], progress_delta=" benign.")])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Write the file", ["manual"])
        loop.approve_contract()
        loop.approve_contract(["out.txt"])
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        self.assertFalse(loop.state.snapshot["plan"])

        before = len(loop.state.events)
        r = loop.run_user_turn("go")

        self.assertEqual(r["status"], "contract_refused")
        self.assertIn("NO_PLAN", r["breaks"])
        # the backend was never called: the turn did not proceed
        self.assertEqual(backend.calls, [])
        # zero tool execution on refusal
        delta = loop.state.events[before:]
        self.assertFalse(any(e["type"] == "tool_called" for e in delta))
        refused = [e for e in delta if e["type"] == "contract_refused"]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["data"]["stage"], "pre_turn")

    def test_unapproved_build_refuses(self):
        loop, _ = make_loop(
            backend=ScriptedBackend([T(progress_delta="benign.")]))
        loop.set_mission("Write the file", ["manual"])
        loop.state.record("plan_updated", {"plan": ["Write it"]})
        loop.state.record("phase_changed", {"phase": "BUILD"})
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "contract_refused")
        self.assertIn("SCOPE_INVALIDATED", r["breaks"])
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))

    def test_stale_contract_refuses(self):
        loop = build_mode_loop()
        # Simulate a stale approval surviving a revision bump: the latest
        # contract_approved event binds revision 0, the mission is at 1.
        loop.state.record("contract_approved",
                          {"revision": 0, "phase": "PLAN",
                           "scope": ["out.txt"]})
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "contract_refused")
        self.assertIn("CONTRACT_STALE", r["breaks"])
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))

    def test_unknown_mode_refuses(self):
        loop, _ = make_loop()
        c = compile_turn_contract(loop.state)
        c = dict(c, mode="warp9")
        breaks = check_pre_turn(loop.state, c)
        self.assertEqual([b.reason for b in breaks], ["MODE_UNKNOWN"])


class TestPreExecuteChecks(unittest.TestCase):
    def test_tool_not_granted(self):
        loop = build_mode_loop()
        turn = T(tool_calls=[{"name": "run_command",
                              "args": {"cmd": "true"}}])
        breaks = pre_execute(loop, turn)
        self.assertEqual([b.reason for b in breaks], ["TOOL_NOT_GRANTED"])

    def test_write_without_approval(self):
        loop = build_mode_loop()
        turn = T(tool_calls=[{"name": "write_file",
                              "args": {"path": "out.txt",
                                       "content": "hi"}}])
        breaks = pre_execute(loop, turn)
        self.assertEqual([b.reason for b in breaks],
                         ["WRITE_WITHOUT_APPROVAL"])

    def test_write_with_matching_approval_passes(self):
        loop = build_mode_loop()
        call = {"name": "write_file",
                "args": {"path": "out.txt", "content": "hi"}}
        ap = {"id": "ap-test1", "call_id": "t9.0", "tool": "write_file",
              "args": dict(call["args"]), "idem_key": loop._idem(call),
              "revision": loop._revision(),
              "scope_epoch": loop.state.snapshot["scope_epoch"],
              "status": "pending"}
        loop.state.record("approval_requested",
                          {"turn_id": "t9", "approvals": [ap],
                           "pending_calls": []})
        loop.state.record("approval_granted", {"id": "ap-test1"})
        turn = T(tool_calls=[call])
        self.assertEqual(pre_execute(loop, turn), [])

    def test_write_outside_scope_invalidated(self):
        loop = build_mode_loop()
        turn = T(tool_calls=[{"name": "write_file",
                              "args": {"path": "other.txt",
                                       "content": "hi"}}])
        reasons = [b.reason for b in pre_execute(loop, turn)]
        self.assertIn("SCOPE_INVALIDATED", reasons)

    def test_completion_without_evidence(self):
        loop = build_mode_loop()
        turn = T(done_claim=True)
        breaks = pre_execute(loop, turn)
        self.assertEqual([b.reason for b in breaks],
                         ["COMPLETION_WITHOUT_EVIDENCE"])


class TestRefusalWiring(unittest.TestCase):
    def test_write_without_approval_pauses_with_named_refusal(self):
        # Integration: the contract refuses execution, names the break, and
        # routes to the approval gate — the write does not run.
        backend = ScriptedBackend([
            T(plan=["Write it"], progress_delta="Planning.",
              assumptions=["Cause: the file is missing."]),
            T(plan=["Write it"],
              tool_calls=[{"name": "write_file",
                           "args": {"path": "out.txt", "content": "hello"}}],
              progress_delta="Writing out.txt (needs approval).",
              assumptions=["Cause: the file does not exist yet."]),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Write the file", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(["out.txt"])
        before = len(loop.state.events)
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "awaiting_approval")
        delta = loop.state.events[before:]
        refusals = [e for e in delta if e["type"] == "contract_refused"]
        self.assertTrue(refusals)
        self.assertEqual(refusals[0]["data"]["stage"], "pre_execute")
        self.assertEqual(refusals[0]["data"]["recourse"], "approval_gate")
        reasons = [b["reason"] for b in refusals[0]["data"]["breaks"]]
        self.assertIn("WRITE_WITHOUT_APPROVAL", reasons)
        # zero execution of the refused write
        self.assertFalse((loop.sandbox.root / "out.txt").exists())
        self.assertFalse(any(e["type"] == "tool_called"
                             and e["data"]["tool"] == "write_file"
                             for e in delta))

    def test_valid_turn_proceeds(self):
        # Positive control: a healthy contract compiles and the turn runs.
        backend = ScriptedBackend([
            T(plan=["Look around"],
              tool_calls=[{"name": "list_dir", "args": {}}],
              progress_delta="Listing."),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Explore", ["manual"])
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any(e["type"] == "tool_result"
                            for e in loop.state.events))
        self.assertFalse(any(e["type"] == "contract_refused"
                             for e in loop.state.events))


class TestPhaseTransitions(unittest.TestCase):
    """Phase B: explicit transition table; illegal jumps are refused."""

    def test_legal_path(self):
        loop, _ = make_loop()
        loop.set_mission("M", ["manual"])
        self.assertEqual(loop.state.snapshot["phase"], "DEFINE")
        r = loop.request_phase("PLAN", reason="test")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["phase"], "PLAN")
        r = loop.request_phase("BUILD", reason="test")
        self.assertEqual(r["status"], "ok")

    def test_illegal_jump_refused(self):
        loop, _ = make_loop()
        loop.set_mission("M", ["manual"])
        # DEFINE -> REVIEW skips PLAN/BUILD: refused
        r = loop.request_phase("REVIEW", reason="test jump")
        self.assertEqual(r["status"], "refused")
        self.assertEqual(loop.state.snapshot["phase"], "DEFINE")
        # refusal is recorded in the log and flagged
        self.assertTrue(any(e["type"] == "transition_refused"
                            for e in loop.state.events))
        self.assertTrue(any("transition refused" in f
                            for f in loop.state.snapshot["flags"]))

    def test_rework_edges_allowed(self):
        loop, _ = make_loop()
        loop.set_mission("M", ["manual"])
        loop.request_phase("PLAN", reason="t")
        loop.request_phase("BUILD", reason="t")
        # BUILD -> PLAN (re-plan) is a legal rework edge
        r = loop.request_phase("PLAN", reason="re-plan")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["phase"], "PLAN")


if __name__ == "__main__":
    unittest.main()
