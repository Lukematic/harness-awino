"""Hooks lifecycle: register/fire/enable/disable/error-isolation/removal,
plus Loop integration at every fire point."""
import unittest

from hooks import HOOK_EVENTS, HookRegistry
from tests.common import make_loop, T
from backends import ScriptedBackend


class TestHookRegistry(unittest.TestCase):
    def test_register_and_fire(self):
        reg = HookRegistry()
        seen = []
        reg.register("a", "turn_start", lambda e, p: seen.append((e, p)))
        reg.register("b", "turn_start",
                     lambda e, p: seen.append((e, p["turn_no"])))
        results = reg.fire("turn_start", {"turn_no": 3})
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(seen[0], ("turn_start", {"turn_no": 3}))
        self.assertEqual(seen[1], ("turn_start", 3))

    def test_fire_unknown_event_is_noop(self):
        reg = HookRegistry()
        self.assertEqual(reg.fire("nope", {}), [])

    def test_register_unknown_event_raises(self):
        reg = HookRegistry()
        with self.assertRaises(ValueError):
            reg.register("x", "bogus_event", lambda e, p: None)

    def test_register_duplicate_name_raises(self):
        reg = HookRegistry()
        reg.register("x", "turn_start", lambda e, p: None)
        with self.assertRaises(ValueError):
            reg.register("x", "turn_end", lambda e, p: None)

    def test_register_non_callable_raises(self):
        reg = HookRegistry()
        with self.assertRaises(ValueError):
            reg.register("x", "turn_start", "not-a-function")

    def test_unregister(self):
        reg = HookRegistry()
        reg.register("x", "turn_start", lambda e, p: None)
        self.assertTrue(reg.unregister("x"))
        self.assertFalse(reg.unregister("x"))
        self.assertEqual(reg.fire("turn_start", {}), [])

    def test_enable_disable(self):
        reg = HookRegistry()
        seen = []
        reg.register("x", "turn_start", lambda e, p: seen.append(1))
        self.assertTrue(reg.is_enabled("x"))
        reg.disable("x")
        self.assertFalse(reg.is_enabled("x"))
        reg.fire("turn_start", {})
        self.assertEqual(seen, [])
        reg.enable("x")
        reg.fire("turn_start", {})
        self.assertEqual(seen, [1])
        self.assertFalse(reg.enable("missing"))
        self.assertFalse(reg.disable("missing"))

    def test_error_isolation(self):
        # A failing hook is recorded and never breaks the loop: other
        # hooks still fire, fire() returns normally.
        reg = HookRegistry()
        seen = []
        reg.register("bad", "tool_result", lambda e, p: 1 / 0)
        reg.register("good", "tool_result", lambda e, p: seen.append(e))
        results = reg.fire("tool_result", {"tool": "read_file"})
        self.assertEqual(len(results), 2)
        bad = next(r for r in results if r["name"] == "bad")
        good = next(r for r in results if r["name"] == "good")
        self.assertFalse(bad["ok"])
        self.assertIn("ZeroDivisionError", bad["error"])
        self.assertTrue(good["ok"])
        self.assertEqual(seen, ["tool_result"])
        self.assertEqual(len(reg.errors), 1)
        self.assertEqual(reg.errors[0]["name"], "bad")
        self.assertEqual(reg.errors[0]["event"], "tool_result")
        # Errors accumulate; clear_errors resets.
        self.assertEqual(reg.clear_errors(), 1)
        self.assertEqual(reg.errors, [])

    def test_listeners(self):
        reg = HookRegistry()
        reg.register("a", "turn_start", lambda e, p: None)
        reg.register("b", "tool_result", lambda e, p: None,
                     enabled=False)
        self.assertEqual(len(reg.listeners()), 2)
        only = reg.listeners("tool_result")
        self.assertEqual(len(only), 1)
        self.assertFalse(only[0]["enabled"])

    def test_all_events_known(self):
        for ev in ("turn_start", "turn_end", "tool_result",
                   "approval_requested", "approval_decided",
                   "mission_set", "mission_complete",
                   "context_compacted"):
            self.assertIn(ev, HOOK_EVENTS)


class TestHookLoopIntegration(unittest.TestCase):
    def _loop_with_recorder(self, script, events):
        loop, _ = make_loop(backend=ScriptedBackend(script))
        seen = []
        for ev in events:
            loop.hooks.register(
                f"rec-{ev}", ev,
                lambda e, p, _seen=seen: _seen.append((e, p)))
        return loop, seen

    def test_turn_start_and_end(self):
        loop, seen = self._loop_with_recorder(
            [T(progress_delta="hi")], ["turn_start", "turn_end"])
        r = loop.run_user_turn("hello")
        self.assertEqual(r["status"], "ok")
        kinds = [e for e, _ in seen]
        self.assertEqual(kinds, ["turn_start", "turn_end"])
        start = next(p for e, p in seen if e == "turn_start")
        end = next(p for e, p in seen if e == "turn_end")
        self.assertEqual(start["text"], "hello")
        self.assertEqual(start["turn_no"], 1)
        self.assertEqual(end["status"], "ok")

    def test_tool_result_hook(self):
        loop, seen = self._loop_with_recorder(
            [T(tool_calls=[{"name": "read_file",
                            "args": {"path": "x.txt"}}],
               progress_delta="reading")],
            ["tool_result"])
        (loop.sandbox.root / "x.txt").write_text("hello")
        loop.run_user_turn("read it")
        tools = [p for e, p in seen if e == "tool_result"]
        self.assertTrue(any(p["tool"] == "read_file" and p["ok"]
                            for p in tools))

    def test_approval_requested_and_decided(self):
        loop, seen = self._loop_with_recorder(
            [T(plan=["Write it"], progress_delta="Planning.",
               assumptions=["Cause: the file is missing."]),
             T(plan=["Write it"],
               tool_calls=[{"name": "write_file",
                            "args": {"path": "f.txt", "content": "x"}}],
               progress_delta="writing",
               assumptions=["Cause: the file does not exist yet."])],
            ["approval_requested", "approval_decided"])
        loop.set_mission("Write the file", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(["f.txt"])
        r = loop.run_user_turn("write it now")
        self.assertEqual(r["status"], "awaiting_approval")
        req = [p for e, p in seen if e == "approval_requested"]
        self.assertEqual(len(req), 1)
        self.assertEqual(len(req[0]["approval_ids"]), 1)
        loop.approve()
        self.assertEqual((loop.sandbox.root / "f.txt").read_text(), "x")
        dec = [p for e, p in seen if e == "approval_decided"]
        self.assertEqual(len(dec), 1)
        self.assertEqual(dec[0]["decision"], "granted")
        self.assertTrue(dec[0]["approval_id"].startswith("ap-"))

    def test_approval_denied_payload(self):
        loop, seen = self._loop_with_recorder(
            [T(plan=["Write it"], progress_delta="Planning.",
               assumptions=["Cause: the file is missing."]),
             T(plan=["Write it"],
               tool_calls=[{"name": "write_file",
                            "args": {"path": "f.txt", "content": "x"}}],
               progress_delta="writing",
               assumptions=["Cause: the file does not exist yet."])],
            ["approval_decided"])
        loop.set_mission("Write the file", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(["f.txt"])
        r = loop.run_user_turn("write it now")
        self.assertEqual(r["status"], "awaiting_approval")
        ap_id = loop.state.snapshot["approvals"][0]["id"]
        loop.deny(ap_id)
        dec = [p for e, p in seen if e == "approval_decided"]
        self.assertEqual(len(dec), 1)
        self.assertEqual(dec[0]["decision"], "denied")
        self.assertEqual(dec[0]["approval_id"], ap_id)

    def test_approval_decided_fires_with_others_pending(self):
        # Two consequential calls: deciding the first returns early
        # (one still pending) — the decision must still fire the hook.
        loop, seen = self._loop_with_recorder(
            [T(plan=["Write both"], progress_delta="Planning.",
               assumptions=["Cause: both files are missing."]),
             T(plan=["Write both"],
               tool_calls=[{"name": "write_file",
                            "args": {"path": "a.txt", "content": "a"}},
                           {"name": "write_file",
                            "args": {"path": "b.txt", "content": "b"}}],
               progress_delta="writing both",
               assumptions=["Cause: neither file exists yet."])],
            ["approval_decided"])
        loop.set_mission("Write two files", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        loop.approve_contract(["a.txt", "b.txt"])
        r = loop.run_user_turn("write them now")
        self.assertEqual(r["status"], "awaiting_approval")
        ids = [a["id"] for a in loop.state.snapshot["approvals"]
               if a["status"] == "pending"]
        self.assertEqual(len(ids), 2)
        r = loop.approve(ids[0])
        self.assertEqual(r["status"], "awaiting_approval")
        dec = [p for e, p in seen if e == "approval_decided"]
        self.assertEqual(len(dec), 1)
        self.assertEqual(dec[0]["decision"], "granted")
        self.assertEqual(dec[0]["approval_id"], ids[0])

    def test_mission_set_hook(self):
        loop, seen = self._loop_with_recorder([], ["mission_set"])
        m = loop.set_mission("Do the thing", ["manual: eyeball it"])
        fired = [p for e, p in seen if e == "mission_set"]
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["mission_id"], m["id"])
        self.assertEqual(fired[0]["revision"], m["revision"])

    def test_mission_complete_hook(self):
        # Full evidence-gated completion: the hook fires exactly where
        # the mission_done journal event is recorded.
        from tests.test_done_forgery import plan_write_verify, drive_to_review
        from tests.common import drive_verification  # noqa: F401
        script = plan_write_verify() + [
            T(plan=["Ship"],
              progress_delta="Premortem done; claiming completion.",
              assumptions=["The artifact could be stale if the sandbox was "
                           "reset; the tripwire is re-reading the file.",
                           "The event log could miss a crash-mid-effect; the "
                           "tripwire is the idempotency check."],
              done_claim=True),
        ]
        loop, seen = self._loop_with_recorder(script, ["mission_complete"])
        loop.set_mission("Fix the login bug",
                         ["artifact:fix.py", "event:tool_result:write_file"])
        drive_to_review(loop)
        r = loop.run_user_turn("ship it")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any(e["type"] == "mission_done"
                            for e in loop.state.events))
        fired = [p for e, p in seen if e == "mission_complete"]
        self.assertEqual(len(fired), 1)
        self.assertIn(fired[0]["via"], ("attempt_completion",
                                        "legacy_done_claim"))

    def test_mission_complete_via_operator_request_done(self):
        # The second completion path (operator /done) fires the hook too;
        # a refused /done fires nothing.
        from tests.test_done_forgery import plan_write_verify, drive_to_review
        loop, seen = self._loop_with_recorder(plan_write_verify(),
                                              ["mission_complete"])
        loop.set_mission("Fix the login bug",
                         ["artifact:fix.py", "manual"])
        drive_to_review(loop)
        r = loop.request_done()
        self.assertEqual(r["status"], "done")
        fired = [p for e, p in seen if e == "mission_complete"]
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["via"], "operator")
        # Refused /done: no completion, no hook.
        loop2, seen2 = self._loop_with_recorder([], ["mission_complete"])
        seen2.clear()
        loop2.set_mission("Fix the login bug",
                          ["artifact:missing.py", "manual"])
        r = loop2.request_done()
        self.assertEqual(r["status"], "rejected")
        self.assertEqual([e for e, _ in seen2], [])

    def test_failing_hook_never_breaks_turn(self):
        loop, _ = make_loop(backend=ScriptedBackend(
            [T(progress_delta="hi")]))
        loop.hooks.register("boom", "turn_start",
                            lambda e, p: (_ for _ in ()).throw(
                                RuntimeError("hook blew up")))
        r = loop.run_user_turn("hello")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(loop.hooks.errors), 1)


if __name__ == "__main__":
    unittest.main()
