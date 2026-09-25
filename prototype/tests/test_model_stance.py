"""Model-chosen stances with phase floors (T14).

The model declares "stance" + "stance_why"; the harness accepts only the
stances the phase allows, runs that stance's rubric plus the phase floor,
and falls back to the router when nothing is declared.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backends import ScriptedBackend, ScriptedJudge  # noqa: E402
from loop import Loop  # noqa: E402
from stances import resolve_declared_stance  # noqa: E402
from tests.common import T  # noqa: E402

COUNTER = ("A rewrite in Rust would cost weeks and the Python code is not "
           "the bottleneck here.")


def build_loop(turns):
    sb = Path(tempfile.mkdtemp(prefix="awino-stance-sb-"))
    be = ScriptedBackend(turns)
    loop = Loop(tempfile.mkdtemp(prefix="awino-stance-"), "p", be,
                ScriptedJudge(), sandbox_dir=str(sb))
    loop.set_mission("Speed up the parser", ["manual"])
    loop.approve_contract()
    loop.state.record("plan_updated", {"plan": ["Profile", "Fix hot path"]})
    loop.approve_contract(["parser.py"])
    assert loop.state.snapshot["phase"] == "BUILD"
    return loop, be


def events(loop, kind):
    return [e["data"] for e in loop.state.events if e["type"] == kind]


class ResolveTest(unittest.TestCase):
    def test_floor_is_appended(self):
        chain, err = resolve_declared_stance("VERIFY", "premortem", "why")
        self.assertEqual((chain, err), (["premortem", "devil's-advocate"], None))

    def test_floor_not_duplicated(self):
        chain, _ = resolve_declared_stance("BUILD", "first-principles", "why")
        self.assertEqual(chain, ["first-principles"])

    def test_phase_limits_and_why_required(self):
        self.assertIn("not allowed in SHIP",
                      resolve_declared_stance("SHIP", "feynman", "x")[1])
        self.assertIn("unknown stance",
                      resolve_declared_stance("BUILD", "yolo", "x")[1])
        self.assertIn("stance_why",
                      resolve_declared_stance("BUILD", "steel-man", " ")[1])
        self.assertEqual(resolve_declared_stance("DEFINE", "feynman", "x")[0],
                         ["feynman"])


class ModelChosenStanceTest(unittest.TestCase):
    def test_model_picks_steel_man_without_trigger_words(self):
        loop, be = build_loop([T(
            objective="Weigh a Rust rewrite of the parser",
            plan=["Profile first"], progress_delta="Weighing the rewrite.",
            assumptions=[COUNTER], stance="steel-man",
            stance_why="The user is committing to a rewrite without "
                       "comparing it to profiling.")])
        loop.run_user_turn("rewrite the parser in rust")
        routed = events(loop, "stance_routed")[-1]
        self.assertEqual(routed["stance"], "steel-man")
        self.assertEqual(routed["chain"], ["steel-man", "first-principles"])
        self.assertTrue(routed["trigger"].startswith("model: "))
        self.assertEqual(events(loop, "stance_rubric_passed")[-1]["stance"],
                         "steel-man->first-principles")
        self.assertIn("CHOOSE YOUR STANCE (BUILD)", be.calls[0]["contract"])

    def test_floor_rubric_still_bites(self):
        # steel-man satisfied, but no plan: the BUILD floor rejects it.
        loop, _ = build_loop([
            T(objective="rewrite parser", plan=[], assumptions=[COUNTER],
              progress_delta="Weighing.", stance="steel-man",
              stance_why="Big change proposed."),
            T(objective="rewrite parser", plan=["Profile first"],
              assumptions=[COUNTER], progress_delta="Weighing.",
              stance="steel-man", stance_why="Big change proposed.")])
        loop.run_user_turn("rewrite the parser in rust")
        failed = events(loop, "stance_rubric_failed")
        self.assertTrue(any("first-principles" in " ".join(f["failures"])
                            for f in failed))
        self.assertTrue(events(loop, "stance_rubric_passed"))

    def test_disallowed_stance_is_rejected_then_corrected(self):
        loop, _ = build_loop([
            T(plan=["Explain"], progress_delta="Teaching.",
              assumptions=["Cause: nested regexes backtrack."],
              stance="feynman", stance_why="Explain the slowness."),
            T(plan=["Profile the parser"], progress_delta="Profiling.",
              assumptions=["Cause: nested regexes backtrack."],
              stance="first-principles", stance_why="Find the cause first.")])
        loop.run_user_turn("the parser is slow")
        rejected = events(loop, "turn_rejected")
        self.assertIn("not allowed in BUILD", " ".join(rejected[0]["errors"]))
        self.assertEqual(events(loop, "stance_routed")[-1]["stance"],
                         "first-principles")

    def test_no_declaration_uses_router(self):
        loop, _ = build_loop([T(plan=["Profile"], progress_delta="Profiling.",
                                assumptions=["Cause: backtracking."])])
        loop.run_user_turn("go")
        self.assertFalse(any(e["trigger"].startswith("model:")
                             for e in events(loop, "stance_routed")))
        self.assertEqual(loop.state.snapshot["stance"], "first-principles")


if __name__ == "__main__":
    unittest.main()
