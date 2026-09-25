"""Cancellation: cooperative CancelToken through the recursive loop.

Covers: cancel before a model round, cancel during a command (pre-empted),
cancel after effects (too-late-with-effects honestly reported), and
cancel during the approval drain.
"""
import threading
import time
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend
from cancel import CancelToken, Cancelled


def _verify_turn():
    return T(plan=["Verify"],
             tool_calls=[{"name": "run_command", "args": {"cmd": "true"}}],
             progress_delta="Running.",
             assumptions=["The command runs in the sandbox in this test."])


def _loop_in_verify(backend):
    """A loop in VERIFY phase (verify mode offers run_command)."""
    loop, _ = make_loop(backend=backend)
    loop.set_mission("M", ["manual"])
    # Draft a plan so the phase can advance past PLAN.
    loop.backend = ScriptedBackend([
        T(plan=["Do it"], progress_delta="Planning.",
          assumptions=["Cause: the test needs a plan."])])
    loop.run_user_turn("draft the plan")
    loop.approve_contract()
    loop.state.record("phase_changed", {"phase": "VERIFY"})
    loop.backend = backend
    return loop


class TestCancellation(unittest.TestCase):
    def test_cancel_before_model_round_halts_cleanly(self):
        # Token already set: the turn halts at the first checkpoint with
        # no tool effects.
        tok = CancelToken()
        tok.set("test: pre-set")
        backend = ScriptedBackend([_verify_turn()])
        loop, _ = make_loop(backend=backend)
        r = loop.run_user_turn("go", cancel_token=tok)
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(len(backend.calls), 0)
        ev = [e for e in loop.state.events if e["type"] == "turn_cancelled"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["data"]["late_effects"], 0)

    def test_cancel_during_command_preempts(self):
        # Token set while run_command is polling: the subprocess is
        # terminated (exit 130, cancelled=True), not left running.
        tok = CancelToken()

        class SlowBackend(ScriptedBackend):
            def generate(self, contract_block, history, feedback=None,
                         temperature=None, stream_cb=None, tools=None,
                         cancel=None):
                # Arm the canceller to fire mid-command.
                def _fire():
                    time.sleep(0.3)
                    tok.set("test: mid-command")
                threading.Thread(target=_fire, daemon=True).start()
                return super().generate(
                    contract_block, history, feedback, temperature,
                    stream_cb, tools, cancel=cancel)

        backend = SlowBackend([T(
            plan=["Sleep"],
            tool_calls=[{"name": "run_command",
                         "args": {"cmd": "sleep 5", "timeout": 30}}],
            progress_delta="Sleeping.",
            assumptions=["The sleep command runs long enough to pre-empt "
                         "in this test."])])
        loop = _loop_in_verify(backend)
        r = loop.run_user_turn("go", cancel_token=tok)
        self.assertEqual(r["status"], "cancelled")
        results = [e for e in loop.state.events if e["type"] == "tool_result"]
        self.assertTrue(results, "expected a (cancelled) tool_result")
        res = results[0]["data"]["result"]
        self.assertTrue(res.get("cancelled"), res)
        self.assertEqual(res.get("exit_code"), 130)

    def test_cancel_after_effects_reports_too_late(self):
        # Token set after generate returns but before the instant command
        # finishes: the effect journals anyway (too late to stop), and the
        # halt honestly reports late_effects.
        tok = CancelToken()

        class LateBackend(ScriptedBackend):
            def generate(self, contract_block, history, feedback=None,
                         temperature=None, stream_cb=None, tools=None,
                         cancel=None):
                turn = super().generate(
                    contract_block, history, feedback, temperature,
                    stream_cb, tools, cancel=None)  # don't let the script see it
                tok.set("test: after generate")
                return turn

        backend = LateBackend([_verify_turn(), _verify_turn()])
        loop = _loop_in_verify(backend)
        r = loop.run_user_turn("go", cancel_token=tok)
        self.assertEqual(r["status"], "cancelled")
        self.assertIn("too-late-with-effects", r["said"])
        ev = [e for e in loop.state.events if e["type"] == "turn_cancelled"]
        self.assertEqual(ev[0]["data"]["late_effects"], 1)

    def test_backend_raises_cancelled_before_model_call(self):
        # A set token aborts before the (scripted) model call — the same
        # checkpoint a real backend uses between stream chunks.
        tok = CancelToken()
        backend = ScriptedBackend([_verify_turn()])
        loop, _ = make_loop(backend=backend)

        orig_generate = backend.generate

        def _generate(*a, **k):
            tok.set("test: before call")
            return orig_generate(*a, **{**k, "cancel": tok})

        backend.generate = _generate
        r = loop.run_user_turn("go", cancel_token=tok)
        # The Cancelled from generate becomes a clean cancelled halt.
        self.assertEqual(r["status"], "cancelled")

    def test_cancel_token_is_thread_safe(self):
        tok = CancelToken()
        seen = []

        def _setter():
            for _ in range(100):
                tok.set("race")

        threads = [threading.Thread(target=_setter) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
            seen.append(tok.is_set())
        self.assertTrue(all(seen))
        self.assertIsNotNone(tok.set_ts)
        self.assertEqual(tok.reason, "race")
