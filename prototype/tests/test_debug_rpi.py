"""debug + rpi skills (honest-gaps fix program): routing, admission, integrity.

debug: dedicated debugging procedure skill
       (reproduce -> diagnose -> fix -> verify), with teeth.
rpi:   repeatable multi-file implementation workflow skill, in loop-owner
       form (works through the contract/mission machinery, not around it).
"""
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skills import STORE_DIR, SkillIntegrityError, SkillStore
from stances import FLOORS, route_triple

NEW_SKILLS = ("debug", "rpi")


def _fresh_store() -> SkillStore:
    # Fresh load (not the default() singleton) so each test verifies bytes
    # straight from disk, like startup does.
    return SkillStore(STORE_DIR)


class TestDebugRpiRouting(unittest.TestCase):
    """Both skills are routed through the existing machinery."""

    def test_fix_intent_routes_debug_and_rpi(self):
        intent, mode, chain, skills, _ = route_triple(
            {"phase": "PLAN"}, "debug this bug in the login form", "info")
        self.assertEqual(intent, "fix")
        self.assertEqual(mode, "build")
        self.assertIn("debug", skills)
        self.assertIn("rpi", skills)

    def test_build_floor_carries_debug_and_rpi(self):
        skills = FLOORS["BUILD"]["skills"]
        self.assertIn("debug", skills)
        self.assertIn("rpi", skills)

    def test_bugfix_mission_kind_requires_debug(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from contract import skills_for_kind
        self.assertIn("debug", skills_for_kind("bugfix"))
        self.assertIn("rpi", skills_for_kind("build"))


class TestDebugRpiAdmission(unittest.TestCase):
    """Admission: pinned, verified at load, network-declared, injection-clean."""

    def test_names_in_store(self):
        store = _fresh_store()
        for name in NEW_SKILLS:
            self.assertIn(name, store.names(), name)

    def test_bodies_match_manifest_pins(self):
        manifest = json.loads((STORE_DIR / "manifest.json").read_text())
        store = _fresh_store()
        for name in NEW_SKILLS:
            raw = (STORE_DIR / f"{name}.md").read_bytes().replace(b"\r\n", b"\n")
            pinned = hashlib.sha256(raw).hexdigest()
            self.assertEqual(manifest[name], pinned, name)
            self.assertEqual(store.pinned_hash(name), pinned, name)
            self.assertTrue(store.get_verified(name).startswith("PROCEDURE"), name)

    def test_store_loads_cleanly_with_new_skills(self):
        store = _fresh_store()
        for name in NEW_SKILLS:
            body = store.get_verified(name)
            self.assertTrue(body.strip(), name)

    def test_network_declared_none(self):
        store = _fresh_store()
        for name in NEW_SKILLS:
            decl = store.network_declaration(name)
            self.assertEqual(decl["network"], "none", name)
            self.assertEqual(decl["destinations"], [], name)

    def test_debug_has_teeth(self):
        body = _fresh_store().get_verified("debug").upper()
        # required reproduction evidence before diagnosis
        self.assertIn("REPRODUCTION", body)
        self.assertIn("ROOT CAUSE", body)
        # root-cause statement format
        self.assertIn("MECHANISM", body)
        # verification criteria
        self.assertIn("VERIFY", body)
        # checklist gates: diagnosis needs repro, fix needs cause,
        # completion needs passing repro
        self.assertIn("NO DIAGNOSIS WITHOUT", body)
        self.assertIn("NO FIX WITHOUT", body)
        self.assertIn("NO COMPLETION WITHOUT", body)

    def test_rpi_has_teeth(self):
        body = _fresh_store().get_verified("rpi").upper()
        # plan the file set / sequence the changes / verify each file /
        # integrate — loop-owner form
        for token in ("FILE SET", "SEQUENCE", "VERIFY EACH FILE",
                      "INTEGRATE", "SCOPE", "MACHINERY"):
            self.assertIn(token, body, token)

    def test_bodies_grant_nothing(self):
        # Skills are a lens/procedure, never a permission expansion.
        for name in NEW_SKILLS:
            body = _fresh_store().get_verified(name).lower()
            self.assertNotIn("approve yourself", body, name)
            self.assertNotIn("grant yourself", body, name)
            self.assertNotIn("bypass", body, name)


if __name__ == "__main__":
    unittest.main()
