"""Judge panel: quorum mechanics + adversarial suite.

Covers the fail-closed rules from judges.py:
  - quorum of PASS votes required; ties and shortfalls FAIL
  - a judge that raises / times out / returns garbage counts as FAIL
  - an always-PASS judge cannot single-handedly pass a hostile proposal
  - the panel is pluggable into Loop as a drop-in JudgeBackend
"""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend, ScriptedJudge
from judges import (JudgePanel, DeterministicJudge, OllamaJudge,
                    JudgeBackendError, build_judge_panel)


class _AlwaysPass:
    name = "always-pass"

    def judge(self, turn, contract_block, summary):
        return {"verdict": "PASS", "reason": "looks fine to me"}


class _AlwaysFail:
    name = "always-fail"

    def judge(self, turn, contract_block, summary):
        return {"verdict": "FAIL", "reason": "guilty until proven innocent"}


class _Exploding:
    name = "exploding"

    def judge(self, turn, contract_block, summary):
        raise RuntimeError("judge backend crashed")


class _Malformed:
    name = "malformed"

    def judge(self, turn, contract_block, summary):
        return {"verdict": "MAYBE", "reason": "undecided"}


def _forge_done():
    return T(done_claim=True, progress_delta="All fixed and verified. Done.")


_UNMET = ("## MISSION\nDo things\n### DONE CRITERIA (live)\n"
          "[ ] artifact_exists:fix.py\n[x] manual: operator /done required\n")


class TestQuorumMechanics(unittest.TestCase):
    def test_majority_default(self):
        panel = JudgePanel([_AlwaysPass(), _AlwaysPass(), _AlwaysFail()])
        self.assertEqual(panel.quorum, 2)
        v = panel.judge(T(progress_delta="Working."), "## MISSION\nnone", {})
        self.assertEqual(v["verdict"], "PASS")
        self.assertEqual(len(v["votes"]), 3)

    def test_shortfall_fails(self):
        panel = JudgePanel([_AlwaysPass(), _AlwaysFail(), _AlwaysFail()])
        v = panel.judge(T(progress_delta="Working."), "## MISSION\nnone", {})
        self.assertEqual(v["verdict"], "FAIL")
        self.assertIn("quorum", v["reason"])

    def test_tie_fails_closed(self):
        panel = JudgePanel([_AlwaysPass(), _AlwaysFail()], quorum=2)
        v = panel.judge(T(progress_delta="Working."), "## MISSION\nnone", {})
        self.assertEqual(v["verdict"], "FAIL")

    def test_bad_quorum_rejected(self):
        with self.assertRaises(ValueError):
            JudgePanel([_AlwaysPass()], quorum=2)
        with self.assertRaises(ValueError):
            JudgePanel([_AlwaysPass()], quorum=0)
        with self.assertRaises(ValueError):
            JudgePanel([])

    def test_votes_are_recorded(self):
        panel = JudgePanel([_AlwaysPass(), _AlwaysFail()], quorum=1)
        v = panel.judge(T(progress_delta="x"), "## MISSION\nnone", {})
        by = {x["judge"]: x["verdict"] for x in v["votes"]}
        self.assertEqual(by, {"always-pass": "PASS", "always-fail": "FAIL"})
        self.assertEqual(len(panel.calls), 1)
        self.assertEqual(panel.calls[0]["verdict"], "PASS")


class TestAdversarialJudges(unittest.TestCase):
    def test_always_pass_cannot_singlehandedly_pass_hostile(self):
        # The key threat: one sycophantic judge must not pass forge_done alone.
        panel = JudgePanel([_AlwaysPass(), DeterministicJudge(),
                            DeterministicJudge()])
        v = panel.judge(_forge_done(), _UNMET, {})
        self.assertEqual(v["verdict"], "FAIL")
        by = {x["judge"]: x["verdict"] for x in v["votes"]}
        self.assertEqual(by["always-pass"], "PASS")
        # both deterministic judges catch the R2 backstop
        self.assertTrue(all(by[j] == "FAIL"
                            for j in by if j != "always-pass"))

    def test_exploding_judge_counts_fail_but_panel_decides(self):
        panel = JudgePanel([_Exploding(), _AlwaysPass(), _AlwaysPass()])
        v = panel.judge(T(progress_delta="Working."), "## MISSION\nnone", {})
        self.assertEqual(v["verdict"], "PASS")  # 2 PASS still reach quorum
        by = {x["judge"]: x for x in v["votes"]}
        self.assertEqual(by["exploding"]["verdict"], "FAIL")
        self.assertIsNotNone(by["exploding"]["error"])

    def test_exploding_judge_cannot_save_hostile_proposal(self):
        panel = JudgePanel([_Exploding(), _AlwaysPass(), DeterministicJudge()])
        v = panel.judge(_forge_done(), _UNMET, {})
        self.assertEqual(v["verdict"], "FAIL")  # only 1 PASS < quorum 2

    def test_malformed_verdict_counts_fail(self):
        panel = JudgePanel([_Malformed(), _AlwaysPass()], quorum=1)
        v = panel.judge(T(progress_delta="x"), "## MISSION\nnone", {})
        by = {x["judge"]: x for x in v["votes"]}
        self.assertEqual(by["malformed"]["verdict"], "FAIL")
        self.assertIn("malformed", by["malformed"]["reason"].lower())
        self.assertEqual(v["verdict"], "PASS")  # quorum 1 via always-pass

    def test_all_judges_down_fails_closed(self):
        panel = JudgePanel([_Exploding(), _Exploding()], quorum=1)
        v = panel.judge(T(progress_delta="x"), "## MISSION\nnone", {})
        self.assertEqual(v["verdict"], "FAIL")
        self.assertIn("0/2 PASS", v["reason"])


class TestOllamaJudgeUnit(unittest.TestCase):
    def test_unreachable_backend_raises_and_panel_fails_it(self):
        # Genuine unreachable host: connection refused is fast and real.
        j = OllamaJudge("qwen2.5-1.5b-local", host="http://127.0.0.1:1",
                        timeout=5)
        with self.assertRaises(JudgeBackendError):
            j.judge(T(progress_delta="x"), "## MISSION\nnone", {})
        panel = JudgePanel([j], quorum=1)
        v = panel.judge(T(progress_delta="x"), "## MISSION\nnone", {})
        self.assertEqual(v["verdict"], "FAIL")


class TestBuildJudgePanel(unittest.TestCase):
    def test_default_is_deterministic_single(self):
        panel = build_judge_panel(spec="")
        self.assertEqual(len(panel.judges), 1)
        self.assertIsInstance(panel.judges[0], DeterministicJudge)
        self.assertEqual(panel.quorum, 1)

    def test_spec_parsing(self):
        panel = build_judge_panel(
            spec="deterministic,deterministic,deterministic", quorum=2)
        self.assertEqual(len(panel.judges), 3)
        self.assertEqual(panel.quorum, 2)
        panel = build_judge_panel(spec="ollama:qwen2.5-1.5b-local")
        self.assertIsInstance(panel.judges[0], OllamaJudge)

    def test_unknown_spec_rejected(self):
        with self.assertRaises(ValueError):
            build_judge_panel(spec="gpt-4")

    def test_panel_plugs_into_loop(self):
        # The panel is a drop-in JudgeBackend: the Loop gate uses it as-is,
        # and a FAIL still blocks the turn before any tool executes.
        backend = ScriptedBackend([
            T(objective="Advise on the file layout", plan=["Advise"],
              assumptions=[
                  "Restating your request: you asked me to advise on the file layout. "
                  "The strongest case for the current layout is colocation.",
                  "The strongest case against is hidden domain boundaries.",
                  "If we are wrong, the tripwire is the next onboarding time."],
              tool_calls=[{"name": "read_file", "args": {"path": "app.py"}}],
              progress_delta="Advice with a read for grounding."),
        ])
        panel = JudgePanel([ScriptedJudge(fail_all=True),
                            ScriptedJudge(fail_all=True)], quorum=1)
        loop, _ = make_loop(backend=backend, judge=panel)
        loop.set_mission("Fix the login bug", ["manual"])
        r = loop.run_user_turn("Advise me on the file layout")
        self.assertEqual(r["status"], "escalated")
        self.assertFalse(any(e["type"] == "tool_called"
                             for e in loop.state.events))
        self.assertTrue(any(e["type"] == "judge_failed"
                            for e in loop.state.events))
        # panel voted on every attempt (initial + retries); all FAIL
        self.assertGreaterEqual(len(panel.calls), 1)
        self.assertTrue(all(c["verdict"] == "FAIL" for c in panel.calls))
        self.assertEqual(len(panel.calls[0]["votes"]), 2)


if __name__ == "__main__":
    unittest.main()
