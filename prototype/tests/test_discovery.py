"""Discovery interview procedure: routing, rubrics, tool discipline.

Ports the legacy awino-discover interview (detect before asking,
one-question-at-a-time across the mission frontier, never rush to a spec)
into the loop-owner: the discovery SKILL carries the procedure body, the
planning-grill STANCE enforces the interview discipline in code.
"""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend
from stances import route_triple


def snap(mission=None, phase="DEFINE"):
    return {"mission": mission, "phase": phase}


def interview_loop(turns):
    loop, _ = make_loop(backend=ScriptedBackend(turns))
    loop.set_mission("Interview the idea", ["manual"])
    return loop


class TestDiscoverySkill(unittest.TestCase):
    def test_new_task_routes_discovery_skill(self):
        for text, kind in (
                ("Rebuild the sync engine", "new_objective"),
                ("I have this raw idea for a distributed cache with "
                 "write-through invalidation", "info")):
            intent, mode, chain, skills, _ = route_triple(snap(), text, kind)
            self.assertEqual(intent, "new-task")
            self.assertEqual(chain, ["planning-grill"])
            # The interview procedure must ride along with the grill.
            self.assertEqual(skills, ["mission-definition", "discovery"])

    def test_discovery_procedure_injected_in_contract(self):
        loop = interview_loop([
            T(plan=[], questions=["What outcome should this create?"],
              progress_delta="Opening the interview at the mission frontier."),
        ])
        r = loop.run_user_turn("new objective: Rebuild the sync engine")
        self.assertEqual(r["status"], "ok")
        block = loop.backend.calls[0]["contract"]
        self.assertIn("PROCEDURE discovery", block)
        self.assertIn("one question at a time", block)
        self.assertIn("success metric", block)
        self.assertIn("PLAN_RUSH", block)

    def test_good_interview_turn_asks_one_question(self):
        loop = interview_loop([
            T(plan=[], questions=["Who exactly is the primary user?"],
              progress_delta="Resolving the frontier: primary user."),
        ])
        r = loop.run_user_turn("new objective: Rebuild the sync engine")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["turn_count"], 1)

    def test_three_questions_at_once_rejected(self):
        bad = T(plan=[],
                questions=["What is the mission?", "Who is the user?",
                           "What is the success metric?"],
                progress_delta="Asking everything at once.")
        loop = interview_loop([bad] * 5)
        r = loop.run_user_turn("new objective: Rebuild the sync engine")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("ONE question", r["said"])
        self.assertIn("QUESTION_DRIP", r["said"])
        self.assertEqual(loop.state.snapshot["turn_count"], 0)

    def test_plan_rush_rejected(self):
        bad = T(plan=["Write the spec", "Implement v1", "Ship it"],
                questions=["What is the mission?"],
                progress_delta="Rushing to a spec mid-interview.")
        loop = interview_loop([bad] * 5)
        r = loop.run_user_turn("new objective: Rebuild the sync engine")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("PLAN_RUSH", r["said"])
        self.assertEqual(loop.state.snapshot["turn_count"], 0)

    def test_tool_discipline_while_interviewing(self):
        # plan is non-empty so the turn reaches the stance rubric (the
        # semantics plan gate would reject it earlier — layered defense).
        bad = T(plan=["Grill"],
                tool_calls=[{"name": "read_file", "args": {"path": "app.py"}}],
                questions=["What is the mission?"],
                progress_delta="Reading while interviewing.")
        loop = interview_loop([bad] * 5)
        r = loop.run_user_turn("new objective: Rebuild the sync engine")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("must not act", r["said"])
        # Rejected before execution: the read never ran.
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))


if __name__ == "__main__":
    unittest.main()
