"""Direct asks get answered (not grilled), the contract carries the current
time, and "New chat" starts a fresh conversation over the same project."""
import datetime
import unittest
from unittest import mock

from tests.common import make_loop, T
from stances import is_direct_ask, route_triple, resolve_declared_stance
from backends import ScriptedBackend
import contract


def snap(mission=None, phase="DEFINE"):
    return {"mission": mission, "phase": phase}


class TestDirectAskRouting(unittest.TestCase):
    def test_short_questions_and_small_talk_are_direct(self):
        for text in ("what time is it", "What day is it?", "hi",
                     "thanks!", "who wrote this module?",
                     "can you list the open tasks",
                     "is the sidecar connected?"):
            self.assertTrue(is_direct_ask(text), text)

    def test_statements_and_long_text_are_not_direct(self):
        for text in ("build a CLI that syncs my notes",
                     "the login page redirects twice after sign in",
                     "what I want is a tool that watches the repo, runs the "
                     "tests on every save and tells me what broke",
                     ""):
            self.assertFalse(is_direct_ask(text), text)

    def test_direct_ask_in_define_routes_advisor_not_grill(self):
        # The reported bug: "what time is it" in DEFINE came back as a
        # planning-grill mission question.
        for kind in ("info", "question"):
            intent, mode, chain, skills, trigger = route_triple(
                snap({"id": "m-1"}, "DEFINE"), "what time is it", kind)
            self.assertEqual(intent, "ask")
            self.assertEqual(mode, "observe")
            self.assertEqual(chain, ["advisor"])
            self.assertEqual(skills, [])
            self.assertEqual(trigger, "direct question")

    def test_specific_intents_still_win(self):
        s = snap({"id": "m-1"}, "DEFINE")
        self.assertEqual(route_triple(s, "How does the judge work?", "question")[0],
                         "teach")
        self.assertEqual(route_triple(s, "start new mission", "info")[0],
                         "new-task")
        self.assertEqual(route_triple(s, "should we use postgres?", "question")[0],
                         "decide")

    def test_non_questions_keep_floor_default(self):
        _, _, chain, _, trigger = route_triple(
            snap({"id": "m-1"}, "DEFINE"), "the export must be CSV", "info")
        self.assertEqual(chain, ["planning-grill"])
        self.assertIn("floor default", trigger)

    def test_declared_advisor_skips_floor_rubric_for_asks(self):
        chain, err = resolve_declared_stance("PLAN", "advisor", "quick answer")
        self.assertIsNone(err)
        self.assertEqual(chain, ["advisor", "first-principles"])
        chain, err = resolve_declared_stance("PLAN", "advisor", "quick answer",
                                             apply_floor=False)
        self.assertIsNone(err)
        self.assertEqual(chain, ["advisor"])


class TestDirectAskInLoop(unittest.TestCase):
    def test_aside_is_answered_and_open_question_survives(self):
        backend = ScriptedBackend([
            T(objective="Define the mission", plan=[],
              questions=["What outcome should this mission create?"],
              progress_delta="Asked for the outcome."),
            T(objective="Define the mission", plan=[],
              progress_delta="[Certain] It is 09:15 on Monday."),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("testing", ["manual"])
        r = loop.run_user_turn("let's define this")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(loop.state.snapshot["open_questions"],
                         ["What outcome should this mission create?"])
        r = loop.run_user_turn("what time is it?")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(loop.state.snapshot["stance"], "advisor")
        # The aside did not "answer" the mission question.
        self.assertEqual(loop.state.snapshot["open_questions"],
                         ["What outcome should this mission create?"])
        self.assertIn("## NOW", backend.calls[1]["contract"])


class TestNowInContract(unittest.TestCase):
    def test_now_line_uses_local_time(self):
        fixed = datetime.datetime(2026, 9, 28, 9, 15, tzinfo=datetime.timezone(
            datetime.timedelta(hours=-7), "PDT"))
        with mock.patch.object(contract, "_now", return_value=fixed):
            line = contract.now_line()
        self.assertIn("Monday 2026-09-28 09:15", line)
        self.assertIn("UTC-07:00", line)


class TestNewChat(unittest.TestCase):
    def test_new_session_resets_conversation_keeps_mission(self):
        backend = ScriptedBackend([
            T(objective="Define", plan=[], questions=["What outcome?"],
              progress_delta="Asked about the outcome."),
            T(objective="Define", plan=[], progress_delta="Fresh start."),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("testing", ["manual"])
        loop.run_user_turn("let's define this")
        old_cid = loop.state.snapshot["conversation_id"]
        self.assertTrue(loop.history)

        r = loop.new_session()
        self.assertEqual(r["status"], "ok")
        s = loop.state.snapshot
        self.assertNotEqual(s["conversation_id"], old_cid)
        self.assertEqual(r["conversation_id"], s["conversation_id"])
        self.assertEqual(s["open_questions"], [])
        self.assertEqual(r["mission"]["text"], "testing")
        self.assertEqual(s["mission"]["text"], "testing")
        self.assertEqual(loop.history, [])

        loop.run_user_turn("the export must be CSV")
        c = backend.calls[1]["contract"]
        # The previous chat's progress does not leak into the new one.
        self.assertNotIn("Asked about the outcome.", c)
        self.assertIn(f"run: {s['conversation_id']}", c.splitlines()[0])

    def test_new_session_survives_reload(self):
        loop, home = make_loop()
        loop.set_mission("testing", ["manual"])
        cid = loop.new_session()["conversation_id"]
        loop2, _ = make_loop(home=home)
        self.assertEqual(loop2.state.snapshot["conversation_id"], cid)
        self.assertEqual(loop2.history, [])

    def test_new_session_refused_while_approval_pending(self):
        loop, _ = make_loop()
        loop.state.snapshot["awaiting_approval"] = True
        r = loop.new_session()
        self.assertEqual(r["status"], "refused")
        self.assertIn("approval", r["said"])


class TestSessionNewCommand(unittest.TestCase):
    def test_sidecar_session_new(self):
        from tests.test_sidecar import SidecarClient
        c = SidecarClient()
        try:
            c.hello()
            r = c.cmd("mission", {"text": "testing", "criteria": ["manual"]})
            while r.get("event") != "command_result":
                r = c.recv()
            before = c.cmd("status")
            while before.get("event") != "command_result":
                before = c.recv()
            r = c.cmd("session_new")
            while r.get("event") != "command_result":
                r = c.recv()
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["result"]["status"], "ok")
            self.assertEqual(r["result"]["mission"]["text"], "testing")
            self.assertTrue(r["result"]["conversation_id"])
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
