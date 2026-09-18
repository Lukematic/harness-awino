"""Approvals: consequential tools need a human; approvals bind to the
mission revision AND the scope epoch; denials execute nothing; stale
approvals are rejected."""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend


def build_ready_loop(**kw):
    """Loop driven to BUILD with SCOPE ["out.txt"], paused on a write."""
    backend = ScriptedBackend([
        T(plan=["Write it"], progress_delta="Planning.",
          assumptions=["Cause: the file is missing."]),
        T(plan=["Write it"],
          tool_calls=[{"name": "write_file",
                       "args": {"path": "out.txt", "content": "hello"}}],
          progress_delta="Writing out.txt (needs approval).",
          assumptions=["Cause: the file does not exist yet."]),
    ])
    loop, _ = make_loop(backend=backend, **kw)
    loop.set_mission("Write the file", ["manual"])
    loop.run_user_turn("draft the plan")
    loop.approve_contract()
    loop.approve_contract(["out.txt"])
    return loop


class TestApprovals(unittest.TestCase):
    def test_consequential_needs_approval(self):
        loop = build_ready_loop()
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "awaiting_approval")
        # not executed yet
        self.assertFalse((loop.sandbox.root / "out.txt").exists())
        ap = r["approvals"][0]
        r = loop.approve(ap)
        self.assertEqual(r["status"], "ok")
        self.assertEqual((loop.sandbox.root / "out.txt").read_text(), "hello")
        granted = [a for a in loop.state.snapshot["approvals"] if a["status"] == "granted"]
        self.assertEqual(len(granted), 1)

    def test_nonconsequential_runs_without_approval(self):
        backend = ScriptedBackend([
            T(plan=["Look around"],
              tool_calls=[{"name": "list_dir", "args": {}}],
              progress_delta="Listing."),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Explore", ["manual"])
        r = loop.run_user_turn("go")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any(e["type"] == "tool_result" for e in loop.state.events))

    def test_deny_executes_nothing(self):
        loop = build_ready_loop()
        r = loop.run_user_turn("fix it now")
        ap = r["approvals"][0]
        r = loop.deny(ap)
        self.assertEqual(r["status"], "ok")
        self.assertFalse((loop.sandbox.root / "out.txt").exists())
        denied = [a for a in loop.state.snapshot["approvals"] if a["status"] == "denied"]
        self.assertEqual(len(denied), 1)
        self.assertFalse(any(e["type"] == "tool_result" and not e["data"].get("reused")
                             for e in loop.state.events))

    def test_stale_approval_rejected_after_revision_change(self):
        loop = build_ready_loop()
        r = loop.run_user_turn("fix it now")
        ap = r["approvals"][0]
        # mission revision changes before the operator approves
        loop.set_mission("Write a different file", ["manual"])
        r = loop.approve(ap)
        self.assertEqual(r["status"], "stale")
        self.assertFalse((loop.sandbox.root / "out.txt").exists())
        stale = [a for a in loop.state.snapshot["approvals"] if a["status"] == "stale"]
        self.assertEqual(len(stale), 1)

    def test_stale_approval_rejected_after_scope_change(self):
        loop = build_ready_loop()
        r = loop.run_user_turn("fix it now")
        ap = r["approvals"][0]
        # a scope change invalidates the pending approval even for the
        # same mission revision
        loop.approve_contract(["out.txt", "extra.txt"])
        r = loop.approve(ap)
        self.assertEqual(r["status"], "stale")
        self.assertFalse((loop.sandbox.root / "out.txt").exists())

    def test_immediate_calls_run_while_consequential_waits(self):
        backend = ScriptedBackend([
            T(plan=["Write it"], progress_delta="Planning.",
              assumptions=["Cause: the file is missing."]),
            T(plan=["List then write"],
              tool_calls=[{"name": "list_dir", "args": {}},
                          {"name": "write_file",
                           "args": {"path": "out.txt", "content": "hello"}}],
              progress_delta="Listing now, writing after approval.",
              assumptions=["Cause: the file does not exist yet."]),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Write the file", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(["out.txt"])
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "awaiting_approval")
        # read-only call already ran; write did not
        self.assertTrue(any(e["type"] == "tool_result" and e["data"]["tool"] == "list_dir"
                            for e in loop.state.events))
        self.assertFalse((loop.sandbox.root / "out.txt").exists())
        loop.approve()
        self.assertEqual((loop.sandbox.root / "out.txt").read_text(), "hello")


if __name__ == "__main__":
    unittest.main()
