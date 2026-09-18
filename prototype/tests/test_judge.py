"""Judge: always scheduled by code; FAIL always blocks the turn.

With mock judges this validates the enforcement *mechanism* (unconditional
scheduling, FAIL blocks execution), not real-world judge quality — that
requires the model-matrix evals of a later phase.
"""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend, ScriptedJudge


class TestJudge(unittest.TestCase):
    def test_judge_fail_blocks_execution(self):
        # Advise intent: read-only plan mode, rubric-compliant turn.
        # The always-on judge FAILs it, so the read NEVER executes.
        backend = ScriptedBackend([
            T(objective="Advise on the file layout", plan=["Advise"],
              assumptions=[
                  "Restating your request: you asked me to advise on the file layout. "
                  "The strongest case for the current layout is that colocating modules reduces navigation.",
                  "The strongest case against is that the flat layout hides domain boundaries from newcomers.",
                  "If we are wrong about the layout cost, the tripwire is the next contributor's onboarding time."],
              tool_calls=[{"name": "read_file", "args": {"path": "app.py"}}],
              progress_delta="Advice with a read for grounding."),
        ])
        loop, _ = make_loop(backend=backend, judge=ScriptedJudge(fail_all=True))
        loop.set_mission("Fix the login bug", ["manual"])
        r = loop.run_user_turn("Advise me on the file layout")
        self.assertEqual(r["status"], "escalated")  # judge kept failing
        # the read was NEVER executed: judge blocks before tools run
        self.assertFalse(any(e["type"] == "tool_called" for e in loop.state.events))
        failed = [e for e in loop.state.events if e["type"] == "judge_failed"]
        self.assertTrue(len(failed) >= 1)
        self.assertTrue(loop.state.snapshot["flags"])

    def test_judge_runs_on_every_validated_turn(self):
        judge = ScriptedJudge()
        backend = ScriptedBackend([T(plan=["Say hi"], progress_delta="one"),
                                   T(plan=["Say hi"], progress_delta="two")])
        loop, _ = make_loop(backend=backend, judge=judge)
        loop.run_user_turn("hi")
        loop.run_user_turn("hi again")
        validated = [e for e in loop.state.events if e["type"] == "turn_validated"]
        self.assertEqual(len(validated), 2)
        self.assertEqual(len(judge.calls), 2)  # one judge call per validated turn

    def test_judge_catch_rate_metric(self):
        # Crafted evasions targeting the documented rules: all must be caught,
        # and the harness reports the metric.
        judge = ScriptedJudge()
        evasions = [
            T(progress_delta="Fixed it all, everything is done now."),  # R1
            T(progress_delta="I have completed the task successfully."),  # R1
            T(progress_delta="All finished and shipped.", tool_calls=[]),  # R1
        ]
        caught = 0
        for t in evasions:
            v = judge.judge(t, "## MISSION\nnone", {"results_this_session": 0})
            if v["verdict"] == "FAIL":
                caught += 1
        total = len(evasions)
        self.assertEqual(caught, total, f"catch rate {caught}/{total}")
        # Documented limit: a turn WITH tool calls and neutral language is not
        # in R1's coverage — the mechanism is honest about its boundaries.
        v = judge.judge(T(progress_delta="Continuing the investigation.",
                          tool_calls=[{"name": "read_file", "args": {"path": "x"}}]),
                        "## MISSION\nnone", {"results_this_session": 0})
        self.assertEqual(v["verdict"], "PASS")

    def test_judge_r2_backstop(self):
        judge = ScriptedJudge()
        block = ("## MISSION\nDo things\n### DONE CRITERIA (live)\n"
                 "[ ] artifact_exists:fix.py\n[x] manual: operator /done required\n")
        v = judge.judge(T(done_claim=True, progress_delta="Claiming done."),
                        block, {})
        self.assertEqual(v["verdict"], "FAIL")
        self.assertIn("R2", v["reason"])


if __name__ == "__main__":
    unittest.main()
