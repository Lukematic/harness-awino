"""Phase C: learning record — teaching snapshots and resolved Q/A persist
and are re-injected into future contracts."""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend
from contract import compile_contract


class TestLearningRecord(unittest.TestCase):
    def _feynman_turn(self):
        return T(progress_delta=(
                    "Analogy: a leaf is a tiny solar panel. "
                    "Example: a sunflower tracks the sun to make sugar. "
                    "Snapshot: photosynthesis converts light into chemical energy."),
                 questions=["What part of the leaf captures the light?"],
                 assumptions=["Cause: the user asked how it works."])

    def test_feynman_pass_records_learning(self):
        backend = ScriptedBackend([self._feynman_turn()])
        loop, _ = make_loop(backend=backend)
        r = loop.run_user_turn("teach me photosynthesis")
        self.assertEqual(r["status"], "ok")
        learnings = loop.state.snapshot["learnings"]
        self.assertEqual(len(learnings), 1)
        self.assertEqual(learnings[0]["kind"], "feynman")
        self.assertIn("Snapshot:", learnings[0]["text"])

    def test_learnings_injected_in_next_contract(self):
        backend = ScriptedBackend([self._feynman_turn(), T(progress_delta="x")])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("teach me photosynthesis")
        loop.run_user_turn("more")
        block = backend.calls[-1]["contract"]
        self.assertIn("## LEARNINGS", block)
        self.assertIn("Snapshot:", block)

    def test_learnings_persist_across_restart(self):
        backend = ScriptedBackend([self._feynman_turn()])
        loop, home = make_loop(backend=backend)
        loop.run_user_turn("teach me photosynthesis")
        # simulated restart
        from loop import Loop
        from backends import ScriptedJudge
        loop2 = Loop(home, "p1", ScriptedBackend([]), ScriptedJudge())
        learnings = loop2.state.snapshot["learnings"]
        self.assertEqual(len(learnings), 1)
        self.assertIn("Snapshot:", learnings[0]["text"])

    def test_resolved_qa_recorded_as_learning(self):
        backend = ScriptedBackend([
            T(questions=["What is the deadline?"], progress_delta="asking"),
            T(progress_delta="noted"),
        ])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("start")
        # user answers while questions are open
        loop.run_user_turn("Friday")
        kinds = [l["kind"] for l in loop.state.snapshot["learnings"]]
        self.assertIn("qa", kinds)


if __name__ == "__main__":
    unittest.main()
