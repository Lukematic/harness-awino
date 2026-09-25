"""Regression: the repair loop must survive the elevator.

Two defects found by the v0.6 /health acceptance gate (autonomous run through
the real recursive loop with an injected sandbox_dir):

1. The BUILD -> VERIFY elevator predicate (_has_write_effects) was monotonic
   over the mission revision: ANY past write re-fired it. An operator repair
   route (VERIFY -> BUILD after a test failure) bounced straight back to
   VERIFY at the next round's elevator check — before the model could patch —
   so the repair loop could never run. The predicate now only counts writes
   journaled since the most recent entry into BUILD.

2. _auto_verify() hardcoded the default sandbox (state.dir / "sandbox") for
   its pre-check search dirs and the verifier worker's project_root. With an
   injected sandbox_dir the pre-check found no artifacts, journaled
   verify_gate_waiting, and VERIFY -> REVIEW stalled forever. It now uses
   _search_dirs() / sandbox.root.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loop import Loop  # noqa: E402
from backends import ScriptedBackend, ScriptedJudge  # noqa: E402


def _loop_with_sandbox():
    home = tempfile.mkdtemp(prefix="awino-repair-")
    sb = Path(tempfile.mkdtemp(prefix="awino-repair-sb-"))
    (sb / "app.py").write_text("x = 1\n")
    loop = Loop(home, "repair", ScriptedBackend([]), ScriptedJudge(),
                sandbox_dir=str(sb))
    return loop, sb


def _journal_write(loop, tool="write_file"):
    rev = loop.state.snapshot["mission_revision"]
    loop.state.record("tool_result", {
        "call_id": "t.0", "tool": tool, "args": {"path": "app.py"},
        "result": {"path": "app.py", "digest": "abc", "bytes": 6},
        "mission_rev": rev})


def _to_build(loop):
    loop.set_mission("Add /health", ["artifact:app.py"])
    loop.approve_contract()
    loop.approve_contract(["app.py"])
    assert loop.state.snapshot["phase"] == "BUILD"


class RepairElevatorTest(unittest.TestCase):
    def test_repair_route_stays_in_build_until_new_write(self):
        loop, _sb = _loop_with_sandbox()
        _to_build(loop)
        # Normal flow: a write on the BUILD floor fires the elevator.
        _journal_write(loop)
        loop._check_elevator_gates("t-elevator")
        self.assertEqual(loop.state.snapshot["phase"], "VERIFY")
        # Operator repair route: VERIFY -> BUILD after a test failure.
        r = loop.request_phase("BUILD", reason="test failure needs repair")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        # The elevator must NOT bounce on the stale write from before.
        loop._check_elevator_gates("t-elevator")
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        # A fresh write on the BUILD floor fires the elevator again.
        _journal_write(loop)
        loop._check_elevator_gates("t-elevator")
        self.assertEqual(loop.state.snapshot["phase"], "VERIFY")

    def test_auto_verify_uses_injected_sandbox(self):
        loop, sb = _loop_with_sandbox()
        loop.set_mission("Add /health",
                         ["artifact:app.py", "event:tool_result:run_command"])
        loop.approve_contract()
        loop.approve_contract(["app.py"])
        rev = loop.state.snapshot["mission_revision"]
        loop.state.record("tool_result", {
            "call_id": "t.1", "tool": "run_command",
            "args": {"cmd": "python -m pytest -q"},
            "result": {"cmd": "python -m pytest -q", "exit_code": 0,
                       "stdout": "2 passed"},
            "mission_rev": rev})
        loop.request_phase("VERIFY", reason="test")
        self.assertTrue(loop._has_exit_zero())
        # With the old hardcoded default-sandbox path this returned False
        # (verify_gate_waiting): app.py lives in the INJECTED sandbox.
        self.assertTrue((sb / "app.py").exists())
        self.assertFalse((loop.state.dir / "sandbox" / "app.py").exists())
        self.assertTrue(loop._auto_verify())
        self.assertTrue(loop.state.snapshot.get("verify_pass"))


class ObservationIdempotencyTest(unittest.TestCase):
    """run_command must re-execute when re-issued: the old (tool, args)
    idempotency key reused the pre-repair pytest failure for the
    post-repair run, so the loop could never observe the green result."""

    def _exec(self, loop, call_id, tool, args):
        return loop._execute_single(call_id, tool, args,
                                    loop._idem({"name": tool, "args": args}))

    def test_run_command_reexecutes_after_world_change(self):
        loop, sb = _loop_with_sandbox()
        (sb / "probe.txt").write_text("v1\n")
        r1 = self._exec(loop, "t1.0", "run_command", {"cmd": "cat probe.txt"})
        self.assertIn("v1", r1["result"]["stdout"])
        (sb / "probe.txt").write_text("v2\n")
        r2 = self._exec(loop, "t1.1", "run_command", {"cmd": "cat probe.txt"})
        # Must be a fresh execution observing v2, not the stale v1.
        self.assertFalse(r2.get("reused"), "run_command result was reused")
        self.assertIn("v2", r2["result"]["stdout"])
        called = [e for e in loop.state.events
                  if e["type"] == "tool_called"
                  and e["data"].get("tool") == "run_command"]
        self.assertEqual(len(called), 2)

    def test_write_file_stays_idempotent(self):
        loop, sb = _loop_with_sandbox()
        args = {"path": "note.txt", "content": "same\n"}
        r1 = self._exec(loop, "t1.0", "write_file", args)
        self.assertNotIn("error", r1["result"])
        r2 = self._exec(loop, "t1.1", "write_file", args)
        # Effect tools still reuse: the write must not double-execute.
        self.assertTrue(r2.get("reused"), "write_file lost idempotency")


if __name__ == "__main__":
    unittest.main()
