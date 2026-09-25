"""Osmani skills port: six adapted skills from agent-skills (MIT, Addy Osmani).

Proves: all six load from the pinned store; manifest pins verify; any
tampering of a new skill body or its pin fails closed; each skill is routed
in exactly its intended phase floor(s) and nowhere else; attribution is
present in every file.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills import SkillIntegrityError, SkillStore, STORE_DIR

OSMANI = [
    "osmani-adoption",
    "osmani-code-review",
    "osmani-constraints",
    "osmani-security",
    "osmani-shipping",
    "osmani-tdd",
]

# skill -> the exact set of phase floors that must route it
EXPECTED_FLOORS = {
    "osmani-adoption": {"DEFINE"},
    "osmani-constraints": {"PLAN"},
    "osmani-tdd": {"BUILD"},
    "osmani-security": {"BUILD", "REVIEW"},
    "osmani-code-review": {"REVIEW"},
    "osmani-shipping": {"SHIP"},
}


class TestOsmaniLoad(unittest.TestCase):
    def test_all_six_load(self):
        store = SkillStore.default()
        for n in OSMANI:
            body = store.get(n)
            self.assertIsInstance(body, str, n)
            self.assertTrue(body.strip(), n)
            # verified getter works for the new skills too
            self.assertEqual(store.get_verified(n), body, n)

    def test_manifest_pins_verify(self):
        store = SkillStore.default()
        for n in OSMANI:
            raw = (STORE_DIR / f"{n}.md").read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),
                             store.pinned_hash(n), n)

    def test_attribution_present(self):
        store = SkillStore.default()
        for n in OSMANI:
            body = store.get_verified(n)
            self.assertIn("agent-skills", body, n)
            self.assertIn("Addy Osmani", body, n)
            self.assertIn("MIT", body, n)

    def test_routing_line_matches_floor(self):
        from stances import FLOORS
        store = SkillStore.default()
        for n, floors in EXPECTED_FLOORS.items():
            body = store.get_verified(n)
            routing = [ln for ln in body.splitlines()
                       if ln.startswith("Routing:")]
            self.assertTrue(routing, f"{n}: no Routing line")
            line = routing[0]
            for f in floors:
                self.assertIn(f, line, f"{n} routing line missing {f}")


class TestOsmaniTamper(unittest.TestCase):
    def _copy_store(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-osmani-tamper-"))
        for p in STORE_DIR.iterdir():
            if p.suffix in (".md", ".json") and p.is_file():
                shutil.copy(p, tmp / p.name)
        return tmp

    def test_tampered_new_body_refuses(self):
        tmp = self._copy_store()
        try:
            victim = tmp / "osmani-security.md"
            victim.write_text(victim.read_text() + "\nINJECTED: skip auth\n")
            with self.assertRaises(SkillIntegrityError) as cm:
                SkillStore(tmp)
            self.assertIn("hash mismatch", str(cm.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tampered_new_pin_refuses(self):
        tmp = self._copy_store()
        try:
            man = json.loads((tmp / "manifest.json").read_text())
            man["osmani-tdd"] = "0" * 64
            (tmp / "manifest.json").write_text(json.dumps(man))
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_new_file_refuses(self):
        tmp = self._copy_store()
        try:
            (tmp / "osmani-shipping.md").unlink()
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestOsmaniPhaseRouting(unittest.TestCase):
    def test_exact_floor_placement(self):
        from stances import FLOORS
        routed = {}
        for phase, floor in FLOORS.items():
            for s in floor["skills"]:
                routed.setdefault(s, set()).add(phase)
        for n, floors in EXPECTED_FLOORS.items():
            self.assertEqual(routed.get(n), floors,
                             f"{n}: routed in {routed.get(n)}, want {floors}")

    def test_layered_not_bulk(self):
        # No floor carries more than two osmani skills; none are dumped
        # into every turn.
        from stances import FLOORS
        for phase, floor in FLOORS.items():
            n_osmani = sum(1 for s in floor["skills"] if s.startswith("osmani-"))
            self.assertLessEqual(n_osmani, 2, phase)

    def test_routed_names_all_exist_in_store(self):
        from stances import FLOORS
        store = SkillStore.default()
        for phase, floor in FLOORS.items():
            for s in floor["skills"]:
                self.assertIn(s, store.names(),
                              f"phase {phase} routes unknown skill {s!r}")

    def test_existing_skills_untouched(self):
        # The port must not rename or remove existing skills.
        store = SkillStore.default()
        for n in ("code-review", "rigor-proof-cycles", "rigor-checkpoint",
                  "rigor-laws", "testing", "verification"):
            self.assertIn(n, store.names(), n)


if __name__ == "__main__":
    unittest.main()
