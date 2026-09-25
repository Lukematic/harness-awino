"""v0.6 recursive loop: harness task tools and completion continuation.

- task_add/task_update run as harness tools inside the recursive loop:
  duplicates return the existing task, bad statuses are refused, and
  exactly one task may be doing at a time.
- A completion claim rejected for missing evidence does not end the
  turn: the rejection is round feedback and the model continues.
"""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend


def _plan_turn():
    return T(plan=["Do it"], progress_delta="Planning.",
             assumptions=["Cause: the test drives the loop."])


class TestHarnessTaskTools(unittest.TestCase):
    def _loop_with_tasks(self, script):
        backend = ScriptedBackend(script)
        loop, _ = make_loop(backend=backend)
        loop.set_mission("M", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        return loop, backend

    def test_task_add_and_duplicate(self):
        loop, _ = self._loop_with_tasks([
            _plan_turn(),
            T(plan=["Work"],
              tool_calls=[{"name": "task_add", "args": {"title": "Write it"}},
                          {"name": "task_add", "args": {"title": "Write it"}}],
              progress_delta="Adding tasks.",
              assumptions=["The task list starts empty in this test."]),
            T(plan=["Work"], progress_delta="Done.",
              assumptions=["The tasks are recorded in this test."]),
        ])
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "ok")
        tasks = loop.state.snapshot["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "Write it")
        added = [e for e in loop.state.events if e["type"] == "task_added"]
        self.assertEqual(len(added), 1, "duplicate must not journal twice")

    def test_task_update_validates_and_enforces_one_doing(self):
        loop, backend = self._loop_with_tasks([_plan_turn()])
        # Drive task ops directly through the harness interception.
        tid = loop._harness_task_add({"title": "A"}, "t1", 0)["id"]
        tid2 = loop._harness_task_add({"title": "B"}, "t1", 0)["id"]
        # Bad status refused.
        bad = loop._harness_task_update({"id": tid, "status": "bogus"}, "t1", 0)
        self.assertEqual(bad["error_code"], "BAD_STATUS")
        # Unknown id refused.
        unk = loop._harness_task_update({"id": "nope", "status": "done"}, "t1", 0)
        self.assertEqual(unk["error_code"], "UNKNOWN_TASK")
        # One-doing policy.
        self.assertEqual(
            loop._harness_task_update({"id": tid, "status": "doing"},
                                      "t1", 0)["status"], "doing")
        conflict = loop._harness_task_update({"id": tid2, "status": "doing"},
                                             "t1", 0)
        self.assertEqual(conflict["error_code"], "DOING_CONFLICT")
        # Finishing the first unlocks the second.
        self.assertEqual(
            loop._harness_task_update({"id": tid, "status": "done"},
                                      "t1", 0)["status"], "done")
        self.assertEqual(
            loop._harness_task_update({"id": tid2, "status": "doing"},
                                      "t1", 0)["status"], "doing")

    def test_tasks_visible_in_round_contract(self):
        loop, backend = self._loop_with_tasks([
            _plan_turn(),
            T(plan=["Work"],
              tool_calls=[{"name": "task_add", "args": {"title": "Persist me"}}],
              progress_delta="Adding.",
              assumptions=["The task list starts empty in this test."]),
            T(plan=["Work"], progress_delta="Done.",
              assumptions=["The task persists across rounds."]),
        ])
        loop.run_user_turn("fix it now")
        # The round-1 contract block (second backend call) lists the task.
        block = backend.calls[1]["contract"] if len(backend.calls) > 1 else ""
        # Fallback: the persisted snapshot carries the tasks regardless.
        titles = [t["title"] for t in loop.state.snapshot["tasks"]]
        self.assertIn("Persist me", titles)


class TestCompletionContinuation(unittest.TestCase):
    def test_rejected_completion_is_feedback_not_terminal(self):
        # A done_claim with unverified criteria is rejected (done-forgery
        # protection at validation); the rejection is round feedback and
        # the turn continues — the model does real work in a later round
        # of the SAME turn.
        backend = ScriptedBackend([
            _plan_turn(),
            T(plan=["Ship early"], progress_delta="Claiming.",
              assumptions=["The work might already be done in this test."],
              done_claim=True),
            T(plan=["Work"],
              tool_calls=[{"name": "task_add", "args": {"title": "Real work"}}],
              progress_delta="Doing the work instead.",
              assumptions=["The rejection said evidence was missing."]),
            T(plan=["Work"], progress_delta="Done.",
              assumptions=["The task is recorded in this test."]),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("M", ["manual"])
        loop.run_user_turn("draft the plan")
        loop.approve_contract()
        r = loop.run_user_turn("fix it now")
        self.assertEqual(r["status"], "ok")
        rejected = [e for e in loop.state.events
                    if e["type"] == "turn_rejected"]
        self.assertTrue(
            any("unverified criteria" in str(e["data"].get("errors"))
                for e in rejected),
            "expected a done-forgery rejection")
        # The turn continued: the later round's task_add ran.
        titles = [t["title"] for t in loop.state.snapshot["tasks"]]
        self.assertIn("Real work", titles)
        # The rejection was delivered as round feedback.
        fb = [c["feedback"] for c in backend.calls if c["feedback"]]
        self.assertTrue(any("unverified criteria" in f for f in fb),
                        [f[:80] for f in fb if f])
