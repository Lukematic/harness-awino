"""set_mission as a model-callable tool (0.5.3).

The discovery interview previously dead-ended: the model could ask questions
forever but had no way to record the mission — only the command palette
could. Now the model calls set_mission when the objective and done criteria
are crisp, and the harness executes it like any other tool (validated,
journaled, permission-gated).
"""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend
from contract import MODES


def _set_mission_turn(text="Write the file", criteria="manual"):
    return T(tool_calls=[{"name": "set_mission",
                          "args": {"text": text, "criteria": criteria}}],
             progress_delta="Mission is crisp; recording it.")


class SetMissionToolTest(unittest.TestCase):
    def test_model_call_sets_mission_end_to_end(self):
        backend = ScriptedBackend([_set_mission_turn()])
        loop, _ = make_loop(backend=backend)
        self.assertIsNone(loop.state.snapshot["mission"])
        r = loop.run_user_turn("lets set mission")
        self.assertEqual(r["status"], "ok")
        mission = loop.state.snapshot["mission"]
        self.assertIsNotNone(mission)
        self.assertEqual(mission["text"], "Write the file")
        self.assertEqual(mission["revision"], 1)
        # the tool result is journaled and reports ok
        results = [e["data"] for e in loop.state.events
                   if e["type"] == "tool_result"
                   and e["data"]["tool"] == "set_mission"]
        self.assertTrue(results)
        self.assertTrue(results[0]["result"].get("ok"))
        self.assertEqual(results[0]["result"]["mission"]["id"], mission["id"])

    def test_second_call_revises_mission(self):
        backend = ScriptedBackend([
            _set_mission_turn("Write the file"),
            _set_mission_turn("Write the file better"),
        ])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("set mission")
        loop.run_user_turn("revise mission")
        mission = loop.state.snapshot["mission"]
        self.assertEqual(mission["text"], "Write the file better")
        self.assertEqual(mission["revision"], 2)

    def test_empty_text_is_tool_error_not_crash(self):
        loop, _ = make_loop()
        result = loop._execute_single("t.0", "set_mission",
                                      {"text": "   ", "criteria": "manual"},
                                      "idem-sm-1")
        self.assertIn("error", result["result"])
        self.assertIn("non-empty", result["result"]["error"])
        self.assertIsNone(loop.state.snapshot["mission"])

    def test_bad_criteria_is_tool_error(self):
        loop, _ = make_loop()
        result = loop._execute_single("t.0", "set_mission",
                                      {"text": "Write the file", "criteria": "  ; "},
                                      "idem-sm-2")
        self.assertIn("error", result["result"])
        self.assertIn("criterion", result["result"]["error"])
        self.assertIsNone(loop.state.snapshot["mission"])

    def test_criteria_string_splits_on_semicolons(self):
        loop, _ = make_loop()
        result = loop._execute_single("t.0", "set_mission",
                                      {"text": "Write the file",
                                       "criteria": "manual; tests pass"},
                                      "idem-sm-4")
        self.assertTrue(result["result"].get("ok"), result["result"])
        mission = loop.state.snapshot["mission"]
        labels = [c.get("label", c.get("kind")) for c in mission["done_criteria"]]
        self.assertEqual(labels, ["manual", "tests pass"])

    def test_worker_mission_is_fixed(self):
        loop, _ = make_loop()
        loop.state.snapshot["worker_id"] = "w-test123"
        result = loop._execute_single("t.0", "set_mission",
                                      {"text": "Write the file", "criteria": ["manual"]},
                                      "idem-sm-3")
        self.assertIn("error", result["result"])
        self.assertIn("worker", result["result"]["error"])
        self.assertIsNone(loop.state.snapshot["mission"])

    def test_offered_in_observe_but_not_build(self):
        loop, _ = make_loop()
        turn = T(tool_calls=[{"name": "set_mission",
                              "args": {"text": "Write the file",
                                       "criteria": "manual"}}])
        turn["header"] = "HDR"
        errs_observe = loop.validate_semantics(turn, "HDR", MODES["observe"]["tools"])
        self.assertFalse([e for e in errs_observe if "not offered" in e],
                         f"set_mission should be offered in observe: {errs_observe}")
        errs_build = loop.validate_semantics(turn, "HDR", MODES["build"]["tools"])
        self.assertTrue(any("not offered" in e and "set_mission" in e
                            for e in errs_build),
                        f"set_mission must not be offered in build: {errs_build}")

    def test_unknown_tool_still_rejected(self):
        loop, _ = make_loop()
        turn = T(tool_calls=[{"name": "no_such_tool", "args": {}}])
        turn["header"] = "HDR"
        errs = loop.validate_semantics(turn, "HDR", MODES["observe"]["tools"])
        self.assertTrue(any("unknown tool" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
