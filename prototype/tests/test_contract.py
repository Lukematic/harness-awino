"""Contract: compilation completeness, code-routed triple, skill loading."""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend
from contract import (compile_contract, route_mode, coerce_turn_contract,
                      ContractTypeError, TurnContract, ToolCall)


class TestContract(unittest.TestCase):
    def test_compile_contains_all_blocks(self):
        loop, _ = make_loop()
        loop.set_mission("Fix the login bug", ["artifact:fix.py", "manual"])
        block = compile_contract(loop.state)
        for section in ["## GOAL", "## MISSION", "### DONE CRITERIA", "## PHASE",
                        "## FLOOR BINDING", "## MODE", "## CONTEXT",
                        "## REQUIREMENTS", "## CONSTRAINTS",
                        "## AUTONOMY", "## SKILLS", "## STANCE", "## SETUP",
                        "## OPEN QUESTIONS",
                        "## KNOWN ASSUMPTIONS", "## LEARNINGS", "## PROGRESS", "## VERIFICATION",
                        "## STOP CONDITION", "## YOUR OUTPUT"]:
            self.assertIn(section, block, f"missing {section}")
        # instruction priority: untrusted data cannot change policy
        self.assertIn("untrusted", block.lower())

    def test_triple_routed_by_code_not_backend(self):
        loop, _ = make_loop(backend=ScriptedBackend([
            T(mode_hint="admin", phase_hint="SHIP",
              progress_delta="I choose admin mode.",
              assumptions=["Cause: the harness routes the triple from code."]),
        ]))
        loop.set_mission("Fix the login bug", ["manual"])
        r = loop.run_user_turn("fix the login bug")
        self.assertEqual(r["status"], "ok")
        s = loop.state.snapshot
        # backend asked for "admin"; harness routed build/first-principles from intent
        self.assertEqual(s["mode"], "build")
        self.assertEqual(s["stance_chain"], ["first-principles"])
        self.assertEqual(s["skills"], ["repo", "code", "debug", "rpi", "rigor-iteration", "rigor-proof-cycles"])
        hints = [e for e in loop.state.events if e["type"] == "turn_hint_ignored"]
        self.assertEqual(len(hints), 1)

    def test_route_mode_no_mission_is_observe(self):
        loop, _ = make_loop()
        self.assertEqual(route_mode(loop.state.snapshot), "observe")

    def test_skills_routed_and_loaded_by_harness(self):
        loop, _ = make_loop(backend=ScriptedBackend([
            T(progress_delta="Drafting the plan.",
              assumptions=["Cause: the empty-password path passes None."],
              questions=["Should the fix handle empty string too?"]),
        ]))
        loop.set_mission("Fix the login bug", ["manual"])
        r = loop.run_user_turn("fix the login bug")
        self.assertEqual(r["status"], "ok")
        s = loop.state.snapshot
        self.assertEqual(s["skills"], ["repo", "code", "debug", "rpi", "rigor-iteration", "rigor-proof-cycles"])
        block = compile_contract(loop.state)
        self.assertIn("PROCEDURE code", block)
        self.assertIn("PROCEDURE repo", block)

    def test_floor_binding_rendered(self):
        loop, _ = make_loop()
        loop.set_mission("Fix the login bug", ["manual"])
        block = compile_contract(loop.state)
        self.assertIn("autonomy: supervised", block)
        self.assertIn("Exit gate:", block)

    def test_contract_injected_every_turn(self):
        backend = ScriptedBackend([T(progress_delta="one"), T(progress_delta="two")])
        loop, _ = make_loop(backend=backend)
        loop.run_user_turn("hi")
        loop.run_user_turn("hi again")
        self.assertEqual(len(backend.calls), 2)
        for call in backend.calls:
            self.assertIn("TURN CONTRACT", call["contract"])
            # fresh compilation each turn: turn counter advances
        self.assertIn("turn: 1", backend.calls[0]["contract"])
        self.assertIn("turn: 2", backend.calls[1]["contract"])


class TestTypedContract(unittest.TestCase):
    """Phase B: the validated turn coerces to an immutable typed contract."""

    def good_raw(self):
        return {
            "header": "h", "objective": "o", "plan": ["p1"],
            "tool_calls": [{"name": "list_dir", "args": {"path": "."}}],
            "questions": ["q?"], "assumptions": ["a"],
            "progress_delta": "did a thing", "done_claim": False,
        }

    def test_coerce_ok(self):
        t = coerce_turn_contract(self.good_raw())
        self.assertIsInstance(t, TurnContract)
        self.assertEqual(t.header, "h")
        self.assertEqual(t.plan, ("p1",))
        self.assertEqual(len(t.tool_calls), 1)
        self.assertIsInstance(t.tool_calls[0], ToolCall)
        self.assertEqual(t.tool_calls[0].name, "list_dir")
        # args normalized to sorted tuple pairs
        self.assertEqual(t.tool_calls[0].args, (("path", "."),))

    def test_coerce_rejects_wrong_types(self):
        bad = self.good_raw()
        bad["plan"] = "not a list"
        self.assertRaises(ContractTypeError, coerce_turn_contract, bad)
        bad = self.good_raw()
        bad["tool_calls"] = [{"name": "x", "args": ["not", "a", "dict"]}]
        self.assertRaises(ContractTypeError, coerce_turn_contract, bad)
        bad = self.good_raw()
        bad["done_claim"] = "yes"
        self.assertRaises(ContractTypeError, coerce_turn_contract, bad)
        bad = self.good_raw()
        bad["tool_calls"] = [{"name": "x", "args": {"k": 123}}]
        self.assertRaises(ContractTypeError, coerce_turn_contract, bad)

    def test_immutable(self):
        t = coerce_turn_contract(self.good_raw())
        self.assertRaises(ContractTypeError, setattr, t, "header", "changed")
        self.assertRaises(ContractTypeError, setattr, t.tool_calls[0], "name", "x")

    def test_as_dict_roundtrip(self):
        raw = self.good_raw()
        d = coerce_turn_contract(raw).as_dict()
        self.assertEqual(d["header"], raw["header"])
        self.assertEqual(d["plan"], raw["plan"])
        self.assertEqual(d["tool_calls"], raw["tool_calls"])

    def test_pipeline_uses_typed_contract(self):
        # a turn that passes validation flows through coercion; the loop
        # still completes the turn with the type-guaranteed dict.
        backend = ScriptedBackend([T(progress_delta="typed ok")])
        loop, _ = make_loop(backend=backend)
        r = loop.run_user_turn("hi")
        self.assertEqual(r["status"], "ok")


class TestNaturalLanguageCriteria(unittest.TestCase):
    """Plain-language done criteria become labeled manual criteria (the
    0.5.0 beta found natural language was refused with
    'unknown criterion spec'). Manual criteria are never auto-verified —
    only an explicit operator /done closes them — so this stays fail-closed
    while accepting plain language."""

    def test_natural_language_maps_to_manual_with_label(self):
        from contract import parse_criteria
        c = parse_criteria("hello.txt exists, contains the greeting line")
        self.assertEqual(c["kind"], "manual")
        self.assertEqual(c["label"],
                         "hello.txt exists, contains the greeting line")

    def test_explicit_forms_unchanged(self):
        from contract import parse_criteria
        self.assertEqual(parse_criteria("manual"), {"kind": "manual"})
        self.assertEqual(parse_criteria("artifact:hello.txt"),
                         {"kind": "artifact_exists", "path": "hello.txt"})
        self.assertEqual(parse_criteria("event:turn"),
                         {"kind": "event", "event_type": "turn"})
        self.assertEqual(parse_criteria("manual:my words"),
                         {"kind": "manual", "label": "my words"})

    def test_manual_label_shown_and_never_auto_ok(self):
        from contract import criterion_status
        ok, label = criterion_status(
            {"kind": "manual", "label": "hello.txt exists"},
            {}, [], [], manual_ok=False)
        self.assertFalse(ok)
        self.assertIn("hello.txt exists", label)
        # even with manual_ok=True the label survives
        ok2, label2 = criterion_status(
            {"kind": "manual", "label": "hello.txt exists"},
            {}, [], [], manual_ok=True)
        self.assertTrue(ok2)
        self.assertIn("hello.txt exists", label2)

    def test_set_mission_accepts_natural_language(self):
        loop, _ = make_loop()
        r = loop.set_mission("Create hello.txt",
                             ["hello.txt exists, contains the greeting line"])
        self.assertNotIn("error", r.get("status", ""))
        crit = loop.state.snapshot["mission"]["done_criteria"]
        self.assertEqual(crit[0]["kind"], "manual")
        self.assertIn("hello.txt exists", crit[0]["label"])


if __name__ == "__main__":
    unittest.main()
