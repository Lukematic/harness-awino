"""Deterministic triple routing + stance rubric evaluation tests.

Routing is code (route_triple); firing loads the procedure into the contract
block and rubric-evaluates the output. Keyword-only fake opposition fails.
"""
import unittest

from tests.common import make_loop, T
from stances import route_triple, evaluate_stance, evaluate_chain
from backends import ScriptedBackend


def snap(mission=None, phase="DEFINE"):
    return {"mission": mission, "phase": phase}


class TestTripleRouting(unittest.TestCase):
    def test_proposal_routes_steel_man(self):
        for text in ("I think we should rewrite auth in Rust",
                     "We should switch to Postgres"):
            intent, mode, chain, skills, _ = route_triple(snap(), text, "info")
            self.assertEqual(intent, "opinion")
            self.assertEqual(mode, "plan")
            self.assertEqual(chain, ["steel-man"])

    def test_learning_routes_feynman(self):
        for text in ("How does the judge work?",
                     "Teach me event sourcing.",
                     "I want to learn about retries."):
            intent, mode, chain, skills, _ = route_triple(snap(), text, "info")
            self.assertEqual(intent, "teach")
            self.assertEqual(mode, "observe")
            self.assertEqual(chain, ["feynman"])
            self.assertIn("explainer", skills)

    def test_new_objective_routes_planning_grill(self):
        intent, mode, chain, skills, _ = route_triple(
            snap(), "Rebuild the sync engine", "new_objective")
        self.assertEqual(intent, "new-task")
        self.assertEqual(chain, ["planning-grill"])
        # The interview procedure must be injected on the new-task path,
        # plus the rigor layer: distillation (spec discipline) and the Five
        # Laws (Layer 1 guardrails).
        self.assertEqual(skills, ["mission-definition", "discovery",
                                  "rigor-distillation", "rigor-laws",
                                  "durable-memory"])

    def test_raw_idea_routes_planning_grill(self):
        intent, mode, chain, skills, _ = route_triple(
            snap(), "I have this raw idea for a distributed cache with write-through invalidation",
            "info")
        self.assertEqual(intent, "new-task")
        self.assertEqual(chain, ["planning-grill"])

    def test_advise_routes_plan_mode_steel_man_chain_decision_skill(self):
        # Required: "advise me" -> plan mode (no write tools), steel-man
        # stance fires, decision-analysis skill loads.
        intent, mode, chain, skills, _ = route_triple(
            snap({"id": "m-1"}, "BUILD"), "Advise me on the launch", "info")
        self.assertEqual(intent, "advise")
        self.assertEqual(mode, "plan")
        self.assertEqual(chain[0], "steel-man")
        self.assertIn("decision-analysis", skills)
        from contract import MODES
        self.assertNotIn("write_file", MODES[mode]["tools"])

    def test_fix_routes_build_scoped_first_principles(self):
        intent, mode, chain, skills, _ = route_triple(
            snap(), "Fix this bug in the login form", "info")
        self.assertEqual(intent, "fix")
        self.assertEqual(mode, "build")
        self.assertEqual(chain, ["first-principles"])
        # Rigor layer on the BUILD floor: convergent iteration + TDD proof
        # cycles. (three-strike is injected by the circuit breaker, never
        # floor-routed.)
        self.assertEqual(skills, ["repo", "code", "debug", "rpi",
                                  "rigor-iteration", "rigor-proof-cycles"])

    def test_ship_routes_ship_mode_premortem(self):
        intent, mode, chain, skills, _ = route_triple(
            snap({"id": "m-1"}, "REVIEW"), "Ship it", "info")
        self.assertEqual(intent, "ship")
        self.assertEqual(mode, "ship")
        self.assertEqual(chain, ["premortem"])
        self.assertEqual(skills, ["verification"])

    def test_floor_default_when_no_intent(self):
        intent, mode, chain, skills, trigger = route_triple(
            snap({"id": "m-1"}, "DEFINE"), "go", "info")
        self.assertIsNone(intent)
        self.assertEqual(mode, "plan")
        self.assertEqual(chain, ["planning-grill"])
        self.assertIn("floor default", trigger)

    def test_default_is_advisor_without_mission_or_floor(self):
        intent, mode, chain, skills, _ = route_triple(
            {"mission": None, "phase": "IDLE"}, "go", "info")
        self.assertEqual(chain, ["advisor"])
        self.assertEqual(mode, "observe")


class TestStanceFiring(unittest.TestCase):
    def _mission_loop(self, script):
        backend = ScriptedBackend(script)
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Decide on the auth rewrite", ["manual"])
        return loop, backend

    def test_steel_man_fires_procedure_and_passes_rubric(self):
        loop, backend = self._mission_loop([
            T(objective="Evaluate rewrite proposal", plan=["Assess the proposal"],
              assumptions=[
                  "Rewriting in Rust discards three years of battle-tested auth edge cases and forces a full re-audit.",
                  "The current module has no known safety bugs, so the rewrite buys little."],
              progress_delta="Restating: you propose rewriting auth in Rust for safety. Counter-case is in assumptions."),
        ])
        r = loop.run_user_turn("I think we should rewrite the auth module in Rust")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["stance"], "steel-man")
        contract = backend.calls[0]["contract"]
        self.assertIn("PROCEDURE steel-man", contract)
        self.assertIn("stance: steel-man", contract.splitlines()[0])

    def test_keyword_only_opposition_fails_rubric(self):
        loop, _ = self._mission_loop([
            T(objective="Evaluate rewrite proposal", plan=["Assess the proposal"],
              assumptions=["However."],
              progress_delta="Restating: you propose rewriting auth in Rust. However."),
        ])
        r = loop.run_user_turn("I think we should rewrite the auth module in Rust")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("keyword-only", r["said"])
        self.assertEqual(loop.state.snapshot["turn_count"], 0)

    def test_feynman_enforced_four_steps(self):
        loop, backend = self._mission_loop([
            T(objective="Learn about the judge", plan=["Explain the judge"],
              questions=["Which part is unclear: scheduling or verdicts?"],
              progress_delta=("Analogy: the judge is a code reviewer who never sleeps. "
                              "Example: it blocked an unverified claim last turn. "
                              "Snapshot: the judge is the harness's always-on verifier.")),
        ])
        r = loop.run_user_turn("How does the judge work?")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["stance"], "feynman")
        self.assertIn("PROCEDURE feynman", backend.calls[0]["contract"])

    def test_feynman_missing_snapshot_fails(self):
        loop, _ = self._mission_loop([
            T(objective="Learn about the judge", plan=["Explain the judge"],
              questions=["Which part is unclear?"],
              progress_delta=("Analogy: the judge is a code reviewer who never sleeps. "
                              "Example: it blocked an unverified claim last turn.")),
        ])
        r = loop.run_user_turn("Teach me about the judge")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("snapshot", r["said"])

    def test_feynman_missing_question_fails(self):
        turn = T(objective="Learn about the judge", plan=["Explain the judge"],
              progress_delta=("Analogy: the judge is a code reviewer. "
                              "Example: it blocked a claim. "
                              "Snapshot: the judge verifies."))
        # Queue enough copies to fail every bounded retry on the gap question.
        loop, _ = self._mission_loop([turn] * 4)
        r = loop.run_user_turn("Teach me about the judge")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("gap question", r["said"])

    def test_planning_grill_fires_and_must_ask_or_plan(self):
        backend = ScriptedBackend([
            T(plan=[], questions=["What is the sync engine's source of truth?"],
              progress_delta="Grilling scope before planning."),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Rebuild sync", ["manual"])
        r = loop.run_user_turn("new objective: Rebuild the sync engine")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["stance"], "planning-grill")
        self.assertIn("PROCEDURE planning-grill", backend.calls[0]["contract"])

    def test_planning_grill_must_not_act_while_asking(self):
        bad = T(plan=["Grill"],
                tool_calls=[{"name": "read_file", "args": {"path": "app.py"}}],
                questions=["What is the source of truth?"],
                progress_delta="Grilling while reading.")
        # Queue enough copies to fail every bounded retry.
        loop, _ = make_loop(backend=ScriptedBackend([bad] * 5))
        loop.set_mission("Rebuild sync", ["manual"])
        r = loop.run_user_turn("new objective: Rebuild the sync engine")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("must not act", r["said"])

    def test_first_principles_requires_stated_cause(self):
        loop, _ = self._mission_loop([
            T(plan=["Patch it"], progress_delta="Patching without a cause."),
        ])
        r = loop.run_user_turn("fix the login bug")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("hypothesized cause", r["said"])

    def test_premortem_requires_failure_modes(self):
        loop, _ = self._mission_loop([
            T(plan=["Ship"], progress_delta="Shipping, all good."),
        ])
        r = loop.run_user_turn("ship it")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("failure modes", r["said"])


class TestRubricUnit(unittest.TestCase):
    def test_steel_man_substance_direct(self):
        good = {"objective": "Evaluate rewrite in Rust", "plan": ["p"],
                "assumptions": ["Rewriting discards years of edge cases and forces a full re-audit."],
                "progress_delta": "x"}
        ok, _ = evaluate_stance("steel-man", good,
                                "I think we should rewrite in Rust")
        self.assertTrue(ok)
        fake = dict(good, assumptions=["On the other hand."])
        ok, failures = evaluate_stance("steel-man", fake,
                                       "I think we should rewrite in Rust")
        self.assertFalse(ok)
        self.assertTrue(any("keyword-only" in f for f in failures))

    def test_chain_evaluates_all(self):
        turn = {"objective": "x", "plan": ["p"],
                "assumptions": ["Rewriting discards years of edge cases and forces a full re-audit."],
                "progress_delta": "Restating the rewrite you asked me to advise on. Counter-case in assumptions."}
        ok, _ = evaluate_chain(["steel-man", "premortem"], turn,
                               "Advise me on the rewrite")
        self.assertTrue(ok)
        ok, failures = evaluate_chain(["steel-man", "premortem"],
                                       dict(turn, assumptions=["However."]),
                                       "Advise me on the rewrite")
        self.assertFalse(ok)

    def test_advisor_rubric_requires_progress(self):
        ok, failures = evaluate_stance(
            "advisor", {"progress_delta": "Did the thing."}, "")
        self.assertTrue(ok)
        self.assertEqual(failures, [])
        ok, failures = evaluate_stance("advisor", {"progress_delta": ""}, "")
        self.assertFalse(ok)
        self.assertTrue(failures)


class TestStanceToolDiscipline(unittest.TestCase):
    """Stances never grant tools; modes grant them, stances only add restraint.

    Steel/advisor (plan mode): writes never offered. Teacher (Feynman):
    rubric rejects every tool call. Researcher (planning-grill): no acting
    in a turn that asks questions.
    """

    def _mission_loop(self, script):
        backend = ScriptedBackend(script)
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Decide on the auth rewrite", ["manual"])
        return loop, backend

    def test_steel_cannot_act(self):
        turn = T(objective="Evaluate rewrite proposal", plan=["Assess it"],
                 assumptions=["Rewriting in Rust discards three years of "
                              "battle-tested auth edge cases and forces a re-audit."],
                 progress_delta="Restating: you propose rewriting auth in Rust. "
                                "Counter-case in assumptions.",
                 tool_calls=[{"name": "write_file",
                              "args": {"path": "x.py", "content": "y"}}])
        loop, _ = self._mission_loop([turn] * 4)
        r = loop.run_user_turn("I think we should rewrite the auth module in Rust")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("not offered in mode 'plan'", r["said"])
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))

    def test_teacher_cannot_call_tools(self):
        turn = T(objective="Learn about the judge", plan=["Explain the judge"],
                 questions=["Which part is unclear: scheduling or verdicts?"],
                 progress_delta=("Analogy: the judge is a code reviewer who never sleeps. "
                                 "Example: it blocked an unverified claim last turn. "
                                 "Snapshot: the judge is the always-on verifier."),
                 tool_calls=[{"name": "read_file", "args": {"path": "notes.txt"}}])
        loop, _ = self._mission_loop([turn] * 4)
        r = loop.run_user_turn("Teach me about the judge")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("no tool calls while teaching", r["said"])
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))

    def test_researcher_cannot_act_while_asking(self):
        turn = T(objective="Rebuild the sync engine", plan=["Shape the mission"],
                 questions=["What exactly is broken in the sync engine?"],
                 progress_delta="Need to know what is broken before scoping.",
                 tool_calls=[{"name": "read_file", "args": {"path": "notes.txt"}}])
        loop, _ = self._mission_loop([turn] * 4)
        r = loop.run_user_turn("Rebuild the sync engine")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("must not act in a turn that asks questions", r["said"])
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))

    def test_advisor_cannot_act(self):
        turn = T(objective="Advise on the rewrite", plan=["Assess the proposal"],
                 assumptions=["Rewriting in Rust discards three years of "
                              "battle-tested auth edge cases and forces a re-audit."],
                 progress_delta=("Restating the rewrite you asked me to advise on. "
                                 "Counter-case in assumptions. "
                                 "Failure rehearsal: a rushed rewrite ships a regression."),
                 tool_calls=[{"name": "write_file",
                              "args": {"path": "x.py", "content": "y"}}])
        loop, _ = self._mission_loop([turn] * 4)
        r = loop.run_user_turn("Advise me on the rewrite")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("not offered in mode 'plan'", r["said"])
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))


class TestTriageStance(unittest.TestCase):
    """Worked example for AUTHORING_TEMPLATE.md: vague agent complaints get a
    named failure mode + falsifier instead of falling through to the fixer."""

    def _mission_loop(self, script):
        backend = ScriptedBackend(script)
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Keep the loop healthy", ["manual"])
        return loop, backend

    def _good_turn(self, **kw):
        base = dict(
            objective="Diagnose the complaint", plan=["Name the mode", "Probe it"],
            assumptions=[
                "Failure mode: silent misroute — the opinion pattern fired on "
                "a status question, so steel-man argued against a proposal "
                "you never made.",
                "Falsifier: if the stance trigger line shows the teach pattern "
                "fired instead, this diagnosis is wrong."],
            progress_delta=("Restating: you said I'm not working because the "
                            "header was wrong. Diagnosis in assumptions."))
        base.update(kw)
        return T(**base)

    def test_vague_complaint_routes_triage(self):
        for text in ("You're not working — the header is wrong every turn",
                     "The agent is misbehaving again",
                     "It keeps acting weird on simple questions"):
            intent, mode, chain, skills, _ = route_triple(snap(), text, "info")
            self.assertEqual(intent, "triage")
            self.assertEqual(mode, "plan")
            self.assertEqual(chain, ["triage"])
            self.assertIn("triage", skills)

    def test_triage_passes_rubric(self):
        loop, backend = self._mission_loop([self._good_turn()])
        r = loop.run_user_turn("You're not working — the header is wrong")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["stance"], "triage")
        self.assertIn("PROCEDURE triage", backend.calls[0]["contract"])
        self.assertIn("silent misroute", backend.calls[0]["contract"])

    def test_triage_unnamed_mode_fails(self):
        turn = self._good_turn(assumptions=[
            "Something is off with the header rendering in a way I cannot explain yet.",
            "Falsifier: if a fresh session shows a correct header, this diagnosis is wrong."])
        loop, _ = self._mission_loop([turn] * 4)
        r = loop.run_user_turn("You're not working")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("name the failure mode", r["said"])

    def test_triage_missing_falsifier_fails(self):
        turn = self._good_turn(assumptions=[
            "Failure mode: rubric false-positive — the last turn was valid but "
            "the grill rubric rejected it for asking a clarifying question."])
        loop, _ = self._mission_loop([turn] * 4)
        r = loop.run_user_turn("The agent is misbehaving")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("falsifier", r["said"])

    def test_triage_cannot_repair(self):
        turn = self._good_turn(tool_calls=[{"name": "write_file",
                                            "args": {"path": "x.py", "content": "y"}}])
        loop, _ = self._mission_loop([turn] * 4)
        r = loop.run_user_turn("You're not working")
        self.assertEqual(r["status"], "escalated")
        self.assertIn("not offered in mode 'plan'", r["said"])
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))


class TestStanceInvariants(unittest.TestCase):
    """Phase C: every stance ships with a procedure AND a rubric.

    A stance without both cannot be added without breaking this test.
    """

    def test_all_stances_have_procedure_and_rubric(self):
        from stances import STANCES, RUBRICS
        for name, stance in STANCES.items():
            self.assertTrue(stance.get("procedure"),
                            f"stance {name!r} has no procedure")
            self.assertIn(name, RUBRICS,
                          f"stance {name!r} has no rubric")
            self.assertTrue(RUBRICS[name],
                            f"stance {name!r} has empty rubric")


if __name__ == "__main__":
    unittest.main()
