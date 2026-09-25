"""End to end: one mission through the real loop with every concept firing.

Scripted model, real everything else: router, stances + rubrics, modes,
phase machine + gates, approvals, sandboxed writes and test runs, verifier
worker, story ledger, brag board, hash-chained journal.

Found by building this test (all fixed):
- a failing test run on VERIFY never routed back to BUILD (the repair loop
  stalled on a floor with no write tools);
- task_update could not close the plan's DAG tasks (two task lists), so the
  verifier always failed with "open blockers";
- task_update had no way to pass evidence, which the verifier requires;
- harness tool results had no tool_called, and a call paused for approval
  reused the next round's call id — both broke verify_journal.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loop import Loop  # noqa: E402
from backends import ScriptedBackend, ScriptedJudge  # noqa: E402
from registry import Registry  # noqa: E402
from story import StoryStore, story_close  # noqa: E402
from tests.common import T  # noqa: E402

TEST_FILE = ("import unittest\nfrom health import health\n\n"
             "class T(unittest.TestCase):\n"
             "    def test_health(self):\n"
             "        self.assertEqual(health(), {'ok': True})\n")
RUN_TESTS = {"name": "run_command",
             "args": {"cmd": f"{sys.executable} -m unittest -q test_health"}}
CAUSE = ["Cause: no health() exists yet, so the test import fails."]
DOUBT = ["The evidence could mislead if the test was edited to pass."]
PLAN = {
    "title": "Health endpoint", "problem": "No liveness check exists.",
    "done_criteria": "test_health passes",
    "breakdown": "A pure function plus one test is the whole need.",
    "surveyed": "Framework routes are overkill here.",
    "user_guidance": "User wants {'ok': True} and a passing test.",
    "proposal": ("A (Honda): plain health() function [Certain]. "
                 "B: Flask route [Likely overkill]. Pick A."),
    "steps": ("Write health.py | test imports it | ImportError\n"
              "Run the tests | exit 0 | assertion fails"),
    "bugatti_brief": "Deep health: dependency probes and latency budgets.",
}


def write(content):
    return {"name": "write_file",
            "args": {"path": "health.py", "content": content}}


def task(tid, status, evidence=""):
    return {"name": "task_update",
            "args": {"id": tid, "status": status, "evidence": evidence}}


class EndToEndMissionTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="awino-e2e-")
        self.sb = Path(tempfile.mkdtemp(prefix="awino-e2e-sb-"))
        (self.sb / "test_health.py").write_text(TEST_FILE)
        self.backend = ScriptedBackend([])
        self.loop = Loop(self.home, "e2e", self.backend, ScriptedJudge(),
                         sandbox_dir=str(self.sb))
        self.awd = Path(self.home) / ".awino"
        Registry(self.awd).ensure()
        self.loop.registry = Registry(self.awd)

    def say(self, *turns):
        self.backend.script.extend(turns)

    def snap(self):
        return self.loop.state.snapshot

    def events(self, kind):
        return [e["data"] for e in self.loop.state.events if e["type"] == kind]

    def approve_latest(self):
        ap = self.events("approval_requested")[-1]
        return self.loop.approve(ap.get("id") or ap.get("approval_id"))

    def test_mission_runs_end_to_end_with_every_concept_firing(self):
        loop = self.loop

        # IDLE: an idea opens the planning-grill interview (one question).
        self.say(T(plan=[], questions=["What should /health return?"],
                   progress_delta="Asking what health returns."))
        loop.run_user_turn("I want to build a /health endpoint")
        self.assertEqual((self.snap()["mode"], self.snap()["stance"]),
                         ("plan", "planning-grill"))

        # The model's set_mission call moves the phase: IDLE -> DEFINE.
        self.say(T(plan=["Record mission"], tool_calls=[
                   {"name": "set_mission", "args": {
                       "text": "Add health() returning {'ok': True} with a "
                               "passing test",
                       "criteria": "artifact:health.py"}}],
                   progress_delta="Mission crisp; recording it."),
                 T(plan=["Plan it with the user"],
                   progress_delta="Mission recorded."))
        loop.run_user_turn("It returns {'ok': True}; done when the test passes")
        self.assertEqual(self.snap()["phase"], "DEFINE")

        self.assertEqual(loop.approve_contract()["status"], "ok")  # -> PLAN

        # Challenge: a hollow "however" fails the steel-man rubric and is
        # retried; a real counter-case passes.
        self.say(T(plan=["Weigh it"], progress_delta="Plain function is fine.",
                   assumptions=["However"]),
                 T(objective="Plain health function versus a framework route",
                   plan=["Weigh it"],
                   progress_delta=("Restating: a plain health function "
                                   "instead of a framework route."),
                   assumptions=["A plain function is never reachable over "
                                "HTTP, so a real load balancer still cannot "
                                "probe it."]))
        loop.run_user_turn("is a plain health function a good idea instead "
                           "of a framework route?")
        self.assertIn("steel-man", [e.get("stance")
                                    for e in self.events("stance_routed")])
        self.assertTrue(any("steel-man" in " ".join(e.get("errors", []))
                            for e in self.events("turn_rejected")))

        # PLAN: story_plan records the Honda-first plan (three stances).
        self.say(T(plan=["Record the agreed plan"],
                   tool_calls=[{"name": "story_plan", "args": PLAN}],
                   progress_delta="Recording the agreed plan.",
                   assumptions=CAUSE),
                 T(plan=["Await scoped approval"],
                   progress_delta="Plan recorded.", assumptions=CAUSE))
        loop.run_user_turn("Plan it: Honda first, keep it small")
        self.assertEqual(len(self.events("story_plan_stance")), 3)
        t1, t2 = [t["id"] for t in Registry(self.awd).tasks()
                  if t.get("story_id")]

        self.assertEqual(loop.approve_contract(["health.py"])["status"], "ok")
        self.assertEqual(self.snap()["phase"], "BUILD")

        # BUILD: a buggy write needs approval; the tests then fail and the
        # elevator routes VERIFY -> BUILD for the repair, all in one go.
        self.say(T(plan=["Write health.py"],
                   tool_calls=[task(t1, "doing"),
                               write("def health():\n    return {'ok': False}\n")],
                   progress_delta="Writing health.py.", assumptions=CAUSE),
                 T(plan=["Run tests"],
                   tool_calls=[task(t1, "done", "health.py"),
                               task(t2, "doing"), RUN_TESTS],
                   progress_delta="Running the tests.", assumptions=DOUBT),
                 T(plan=["Fix"],
                   tool_calls=[write("def health():\n    return {'ok': True}\n")],
                   progress_delta="Fixing the return value.",
                   assumptions=["Cause: health() returned ok False, so the "
                                "equality assertion failed."]))
        r = loop.run_user_turn("go build it")
        self.assertEqual(r["status"], "awaiting_approval")
        r = self.approve_latest()
        self.assertEqual(r["status"], "awaiting_approval")  # the fix
        self.assertTrue(any(e.get("source") == "tests"
                            for e in self.events("verify_failed")))

        # Approve the fix: tests pass, the verifier worker passes, REVIEW.
        self.say(T(plan=["Re-run tests"],
                   tool_calls=[RUN_TESTS, task(t2, "done", "test_health.py")],
                   progress_delta="Re-running tests.", assumptions=DOUBT),
                 T(plan=["Report"], progress_delta="Verified.",
                   assumptions=["It could fail in production if the route "
                                "is never mounted."]))
        self.approve_latest()
        self.assertEqual(self.snap()["phase"], "REVIEW")
        self.assertTrue(self.events("verify_passed"))
        self.assertTrue(self.events("story_ready_to_close"))

        # REVIEW: evidence-gated completion -> SHIP.
        self.say(T(plan=["Complete with evidence"], tool_calls=[
                   {"name": "attempt_completion", "args": {
                       "summary": "health() returns {'ok': True}; tests pass."}}],
                   progress_delta="Claiming completion with evidence.",
                   assumptions=["It could fail if the test never ran against "
                                "the final file."]))
        loop.run_user_turn("ship it")
        self.assertEqual(self.snap()["phase"], "SHIP")
        self.assertTrue(self.events("mission_done"))

        # The user closes the story; it lands on the brag board.
        st = next(s for s in StoryStore(self.awd).list()
                  if s["title"] == "Health endpoint")
        story_close(self.awd, st["id"], "health() live with a passing test")
        self.assertIn("health() live with a passing test",
                      (self.awd.parent / "STORY.md").read_text())

        # Every concept fired, in code.
        phases = [e["phase"] for e in self.events("phase_changed")]
        # IDLE -> DEFINE rides mission_set; REVIEW -> SHIP rides
        # mission_done; everything between is an explicit phase_changed.
        self.assertEqual(phases, ["PLAN", "BUILD", "VERIFY", "BUILD",
                                  "VERIFY", "REVIEW"])
        self.assertTrue({"planning-grill", "steel-man", "first-principles",
                         "devil's-advocate", "premortem"}
                        <= {e.get("stance") for e in self.events("stance_routed")}
                        | {self.snap()["stance"]})
        self.assertTrue({"plan", "build", "verify", "ship"}
                        <= {e.get("mode") for e in self.events("mode_routed")})
        self.assertTrue(self.events("stance_rubric_passed"))
        self.assertTrue(self.events("approval_granted"))
        ok, problems = loop.verify_journal()
        self.assertTrue(ok, problems)

    def test_done_without_evidence_is_refused_at_the_tool(self):
        self.loop.set_mission("M", ["manual"])
        tid = Registry(self.awd).add_task("step", source="story:x")["id"]
        r = self.loop._harness_task_update(
            {"id": tid, "status": "done"}, "t1", 0)
        self.assertEqual(r.get("error_code"), "EVIDENCE_REQUIRED")
        (self.sb / "proof.txt").write_text("ok")
        r = self.loop._harness_task_update(
            {"id": tid, "status": "done", "evidence": "proof.txt"}, "t1", 0)
        self.assertEqual(r, {"id": tid, "status": "done", "dag": True})


if __name__ == "__main__":
    unittest.main()
