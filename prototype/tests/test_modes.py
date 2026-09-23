"""Track D: intelligent role modes — router, journaling, override, lens proof."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import modes
from modes import ROLE_IDS, propose_role, role_contract_section
from skills import SkillStore

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from common import make_loop


class RouterTest(unittest.TestCase):
    def test_experiments_mission_selects_researcher(self):
        p = propose_role("Run experiments to evaluate the ranking model on the benchmark")
        self.assertEqual(p["role"], "ai-researcher")

    def test_secrets_content_triggers_cybersecurity(self):
        p = propose_role("Rotate the leaked API key and audit where the secret was stored")
        self.assertEqual(p["role"], "cybersecurity-engineer")

    def test_mid_mission_secret_trigger_overrides(self):
        p = propose_role("FYI the token is in the config", current_role="software-engineer")
        self.assertEqual(p["source"], "trigger")
        self.assertEqual(p["role"], "cybersecurity-engineer")

    def test_mid_mission_experiment_trigger_overrides(self):
        p = propose_role("the results are in from the benchmark", current_role="software-engineer")
        self.assertEqual(p["source"], "trigger")
        self.assertEqual(p["role"], "ai-researcher")

    def test_ambiguous_text_falls_back_to_configured_profile(self):
        p = propose_role("do the thing", configured_profile="ai-architect")
        self.assertEqual(p["role"], "ai-architect")
        self.assertEqual(p["source"], "project_yaml")

    def test_five_roles_all_defined(self):
        self.assertEqual(set(ROLE_IDS), set(modes.ROLES.keys()))
        for rid, r in modes.ROLES.items():
            for key in ("perspective", "use_during", "red_flags",
                        "required_evidence", "skills_to_load",
                        "decision_rule", "close_out", "phase_affinities",
                        "decomposition_playbook", "toolchain"):
                self.assertIn(key, r, f"{rid} missing {key}")
            self.assertTrue(r["decomposition_playbook"], f"{rid} empty playbook")

    def test_role_skill_files_pinned(self):
        store = SkillStore.default()
        for rid in ROLE_IDS:
            name = f"mode-{rid}"
            self.assertIn(name, store.names())
            body = store.get_verified(name)
            self.assertIn("## Perspective", body)
            self.assertIn("[Certain]", body)  # claim labeling convention

    def test_role_skills_load_list(self):
        got = modes.role_skill_names("software-engineer")
        self.assertEqual(got[0], "mode-software-engineer")
        self.assertIn("code", got)


class LoopRoleTest(unittest.TestCase):
    def setUp(self):
        self.loop, self.home = make_loop("role-p1")
        self.awino = Path(tempfile.mkdtemp(prefix="awino-role-")) / ".awino"

    def _attach_registry(self):
        from registry import Registry
        reg = Registry(self.awino)
        reg.ensure()
        self.loop.registry = reg
        return reg

    def test_set_role_mode_journals_and_mirrors(self):
        self._attach_registry()
        res = self.loop.set_role_mode("ai-researcher", "test reason", "router")
        self.assertEqual(res["status"], "ok")
        snap = self.loop.state.snapshot
        self.assertEqual(snap["role_mode"]["role"], "ai-researcher")
        self.assertEqual(snap["role_mode"]["reason"], "test reason")
        # journal has the event
        kinds = [e["type"] for e in self.loop.state.events]
        self.assertIn("role_mode", kinds)
        # .awino/role.json mirror written for awino status/plan
        mirror = json.loads((self.awino / "role.json").read_text())
        self.assertEqual(mirror["role"], "ai-researcher")
        self.assertEqual(mirror["reason"], "test reason")

    def test_unknown_role_is_plain_language_error(self):
        res = self.loop.set_role_mode("ninja", "x", "override")
        self.assertEqual(res["status"], "error")
        self.assertIn("Next action", res["said"])
        self.assertNotIn("Traceback", res["said"])
        self.assertIsNone(self.loop.state.snapshot.get("role_mode"))

    def test_route_role_applies_router_proposal(self):
        self.loop.set_mission("Run experiments to evaluate the model", ["manual"])
        res = self.loop.route_role(mission_text="Run experiments to evaluate the model",
                                   configured_profile="software-engineer", force=True)
        self.assertEqual(res["role"], "ai-researcher")

    def test_mid_mission_trigger_reroutes_lens(self):
        self.loop.set_role_mode("software-engineer", "start", "router")
        self.loop.set_mission("Build the billing page", ["manual"])
        self.loop.run_user_turn("wait, the api key is hardcoded in config.py")
        cur = (self.loop.state.snapshot.get("role_mode") or {}).get("role")
        self.assertEqual(cur, "cybersecurity-engineer")
        kinds = [e["type"] for e in self.loop.state.events]
        self.assertIn("role_mode", kinds)

    def test_role_lens_appends_skill_but_tools_unchanged(self):
        from contract import MODES, compile_contract
        self.loop.set_mission("Build the billing page", ["manual"])
        self.loop.route_role(mission_text="Build the billing page",
                             configured_profile="software-engineer", force=True)
        s = self.loop.state.snapshot
        # simulate what _sensor_route does: lens skill appended to skills
        lens = modes.ROLE_SKILL_NAMES["software-engineer"]
        s2 = dict(s)
        block = compile_contract(self.loop.state, turn_no=1)
        self.assertIn("## ROLE MODE", block)
        self.assertIn("software-engineer", block)
        self.assertIn("permissions: UNCHANGED", block)
        # the proof: offered tools line is identical with/without the lens
        self.loop.state.snapshot["role_mode"] = None
        plain = compile_contract(self.loop.state, turn_no=1)
        self.assertNotIn("## ROLE MODE", plain)
        offered = [l for l in block.splitlines() if l.startswith("offered tools:")]
        offered_plain = [l for l in plain.splitlines() if l.startswith("offered tools:")]
        self.assertEqual(offered, offered_plain)
        self.assertEqual(s2["mode"], self.loop.state.snapshot["mode"])

    def test_role_contract_section_states_no_permission_change(self):
        sec = role_contract_section("ai-architect", "why", ["read_file"], ["read_file"])
        self.assertIn("lens", sec)
        self.assertIn("UNCHANGED", sec)

    def test_phase_boundary_reevaluates_role(self):
        self.loop.set_mission("Run experiments to evaluate the model", ["manual"])
        self.loop.route_role(mission_text="Run experiments to evaluate the model",
                             force=True)
        self.assertEqual(self.loop.active_role()["role"], "ai-researcher")
        # phase boundary: request DEFINE->PLAN keeps a fitting role
        self.loop.state.record("contract_approved", {"scope": []})
        self.loop.request_phase("PLAN", reason="test")
        self.assertEqual(self.loop.state.snapshot["phase"], "PLAN")
        self.assertEqual(self.loop.active_role()["role"], "ai-researcher")


if __name__ == "__main__":
    unittest.main()
