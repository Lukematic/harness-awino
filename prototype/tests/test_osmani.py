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
    "osmani-failure-modes",
    "osmani-security",
    "osmani-shipping",
    "osmani-tdd",
]

# skill -> the exact set of phase floors that must route it
EXPECTED_FLOORS = {
    "osmani-adoption": {"DEFINE"},
    "osmani-constraints": {"PLAN"},
    "osmani-failure-modes": {"PLAN"},
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


class TestDefinitionOfDone(unittest.TestCase):
    def test_reference_loads_and_pin_verifies(self):
        store = SkillStore.default()
        body = store.get_verified("definition-of-done")
        self.assertTrue(body.strip())
        raw = (STORE_DIR / "definition-of-done.md").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         store.pinned_hash("definition-of-done"))

    def test_attribution_present(self):
        body = SkillStore.default().get_verified("definition-of-done")
        self.assertIn("agent-skills", body)
        self.assertIn("Addy Osmani", body)
        self.assertIn("MIT", body)

    def test_acceptance_criteria_vs_dod_distinction(self):
        body = SkillStore.default().get_verified("definition-of-done")
        low = body.lower().replace("*", "")
        self.assertIn("acceptance criteria", low)
        self.assertIn("did we build", low)
        self.assertIn("is it ready", low)

    def test_not_floor_routed(self):
        # A supporting reference: pinned for integrity, never bulk-loaded
        # into a turn's context as a standalone skill.
        from stances import FLOORS
        routed = {s for f in FLOORS.values() for s in f["skills"]}
        self.assertNotIn("definition-of-done", routed)

    def test_reachable_from_ship_gate(self):
        # osmani-shipping is SHIP-routed and names the DoD as its final gate.
        from stances import FLOORS
        self.assertIn("osmani-shipping", FLOORS["SHIP"]["skills"])
        body = SkillStore.default().get_verified("osmani-shipping")
        self.assertIn("definition-of-done", body)

    def test_referenced_from_planning_phase(self):
        # osmani-constraints is PLAN-routed and points at the DoD.
        from stances import FLOORS
        self.assertIn("osmani-constraints", FLOORS["PLAN"]["skills"])
        body = SkillStore.default().get_verified("osmani-constraints")
        self.assertIn("definition-of-done", body)

    def test_tampered_reference_refuses(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-dod-tamper-"))
        try:
            for p in STORE_DIR.iterdir():
                if p.suffix in (".md", ".json") and p.is_file():
                    shutil.copy(p, tmp / p.name)
            victim = tmp / "definition-of-done.md"
            victim.write_text(victim.read_text() + "\nINJECTED LINE\n")
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestOsmaniFailureModes(unittest.TestCase):
    def test_ten_modes_present(self):
        body = SkillStore.default().get_verified("osmani-failure-modes")
        low = body.lower()
        for phrase in ("unchecked assumptions", "confused", "inconsistency",
                       "tradeoffs", "sycophancy", "overcomplicat",
                       "orthogonal", "not fully understood", "without a spec",
                       "looks right"):
            self.assertIn(phrase, low, phrase)

    def test_each_mode_names_awino_mechanism(self):
        # The adaptation: every failure mode names the Awino machinery that
        # catches it — not generic advice.
        body = SkillStore.default().get_verified("osmani-failure-modes")
        low = body.lower()
        for mech in ("rigor-laws", "discovery", "judge", "decision-analysis",
                     "devil's-advocate", "osmani-code-review", "rigor-scope",
                     "osmani-adoption", "mission-definition",
                     "rigor-proof-cycles", "rigor-distillation"):
            self.assertIn(mech, low, mech)

    def test_verification_step_present(self):
        body = SkillStore.default().get_verified("osmani-failure-modes")
        self.assertIn("VERIFICATION STEP", body)
        self.assertIn("turn boundary", body.lower())

    def test_cross_phase_guardrail_routing(self):
        from stances import FLOORS
        self.assertIn("osmani-failure-modes", FLOORS["PLAN"]["skills"])
        body = SkillStore.default().get_verified("osmani-failure-modes")
        routing = [ln for ln in body.splitlines() if ln.startswith("Routing:")]
        self.assertTrue(routing)
        self.assertIn("cross-phase guardrail", routing[0].lower())

    def test_tampered_failure_modes_refuses(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-fm-tamper-"))
        try:
            for p in STORE_DIR.iterdir():
                if p.suffix in (".md", ".json") and p.is_file():
                    shutil.copy(p, tmp / p.name)
            victim = tmp / "osmani-failure-modes.md"
            victim.write_text(victim.read_text() + "\nINJECTED LINE\n")
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_code_review_defers_scope_to_rigor_scope(self):
        # No duplicated weaker scope discipline: the review checklist points
        # at rigor-scope for the touch-audit.
        body = SkillStore.default().get_verified("osmani-code-review")
        self.assertIn("rigor-scope", body)


class TestLifecycleSequence(unittest.TestCase):
    def _section(self, body, header):
        lines = body.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.strip() == header)
        out = []
        for ln in lines[start + 1:]:
            if ln.startswith("## ") or ln.startswith("---"):
                break
            out.append(ln)
        return "\n".join(out)

    def test_file_loads_and_pin_verifies(self):
        store = SkillStore.default()
        body = store.get_verified("lifecycle-sequence")
        self.assertTrue(body.strip())
        raw = (STORE_DIR / "lifecycle-sequence.md").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         store.pinned_hash("lifecycle-sequence"))

    def test_attribution_present(self):
        body = SkillStore.default().get_verified("lifecycle-sequence")
        self.assertIn("agent-skills", body)
        self.assertIn("Addy Osmani", body)
        self.assertIn("MIT", body)

    def test_not_floor_routed(self):
        # Reference map: pinned for integrity, never bulk-loaded as a skill.
        from stances import FLOORS
        routed = {s for f in FLOORS.values() for s in f["skills"]}
        self.assertNotIn("lifecycle-sequence", routed)

    def test_referenced_skills_all_in_manifest(self):
        # No dangling references: every skill the map names must exist.
        import re
        store = SkillStore.default()
        body = store.get_verified("lifecycle-sequence")
        section = self._section(body, "## Referenced skills (machine-checked: every name must exist in manifest.json)")
        names = re.findall(r"`([^`]+)`", section)
        self.assertTrue(names, "no referenced skills found")
        manifest = json.loads((STORE_DIR / "manifest.json").read_text())
        for n in names:
            self.assertIn(n, manifest, f"dangling reference: {n}")
            self.assertIn(n, store.names(), f"not loadable: {n}")

    def test_tracks_only_use_known_phases(self):
        # The three tracks may reference only real mission phases:
        # ACTIVE_PHASES (BUILD/VERIFY/REVIEW/SHIP) plus DEFINE/PLAN.
        import re
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from contract_loop import ACTIVE_PHASES
        allowed = set(ACTIVE_PHASES) | {"DEFINE", "PLAN"}
        body = SkillStore.default().get_verified("lifecycle-sequence")
        for track in ("## Track 1: Full feature", "## Track 2: Bugfix",
                      "## Track 3: Refactor (behavior-preserving)"):
            section = self._section(body, track)
            phases = re.findall(r"^-\s+([A-Z]+):", section, re.M)
            self.assertTrue(phases, f"no phases in {track}")
            for p in phases:
                self.assertIn(p, allowed, f"{track} references unknown phase {p}")

    def test_three_tracks_present_with_skip_rationale(self):
        body = SkillStore.default().get_verified("lifecycle-sequence")
        for track in ("## Track 1: Full feature", "## Track 2: Bugfix",
                      "## Track 3: Refactor (behavior-preserving)"):
            self.assertIn(track, body, track)
        # Skipped phases must be by explicit determination, never laziness.
        self.assertIn("never by laziness", body.lower())

    def test_map_vs_territory_rule(self):
        body = SkillStore.default().get_verified("lifecycle-sequence")
        low = body.lower()
        self.assertIn("map vs territory", low)
        self.assertIn("defect", low)

    def test_adoption_references_map(self):
        body = SkillStore.default().get_verified("osmani-adoption")
        self.assertIn("lifecycle-sequence", body)

    def test_tampered_sequence_refuses(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-ls-tamper-"))
        try:
            for p in STORE_DIR.iterdir():
                if p.suffix in (".md", ".json") and p.is_file():
                    shutil.copy(p, tmp / p.name)
            victim = tmp / "lifecycle-sequence.md"
            victim.write_text(victim.read_text() + "\nINJECTED LINE\n")
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCriticalThinking(unittest.TestCase):
    def _section(self, body, header):
        lines = body.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.strip() == header)
        out = []
        for ln in lines[start + 1:]:
            if ln.startswith("## ") or ln.startswith("---"):
                break
            out.append(ln)
        return "\n".join(out)

    def test_file_loads_and_pin_verifies(self):
        store = SkillStore.default()
        body = store.get_verified("critical-thinking")
        self.assertTrue(body.strip())
        raw = (STORE_DIR / "critical-thinking.md").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         store.pinned_hash("critical-thinking"))

    def test_attribution_present(self):
        body = SkillStore.default().get_verified("critical-thinking")
        self.assertIn("Addy Osmani", body)
        self.assertIn("LinkedIn", body)

    def test_nine_questions_present(self):
        body = SkillStore.default().get_verified("critical-thinking")
        low = body.lower()
        for phrase in ("right problem", "right way", "root cause",
                       "smaller analyzable", "hypotheses",
                       "shortcuts", "evidence sufficiently",
                       "when we're done", "communicate the solution"):
            self.assertIn(phrase, low, phrase)

    def test_hypothesis_debug_mode_documented_not_built(self):
        body = SkillStore.default().get_verified("critical-thinking")
        low = body.lower()
        self.assertIn("hypothesis-driven debug mode", low)
        self.assertIn("not built", low)

    def test_not_floor_routed(self):
        from stances import FLOORS
        routed = {s for f in FLOORS.values() for s in f["skills"]}
        self.assertNotIn("critical-thinking", routed)

    def test_wired_into_mission_definition(self):
        # mission-definition is DEFINE-routed and runs the checklist first.
        from stances import FLOORS
        self.assertIn("mission-definition", FLOORS["DEFINE"]["skills"])
        body = SkillStore.default().get_verified("mission-definition")
        self.assertIn("critical-thinking", body)

    def test_referenced_mechanisms_all_exist(self):
        import re
        store = SkillStore.default()
        body = store.get_verified("critical-thinking")
        section = self._section(
            body,
            "## Referenced skills (machine-checked: every name must exist in manifest.json)")
        names = re.findall(r"`([^`]+)`", section)
        self.assertTrue(names, "no referenced skills found")
        manifest = json.loads((STORE_DIR / "manifest.json").read_text())
        for n in names:
            self.assertIn(n, manifest, f"dangling reference: {n}")
            self.assertIn(n, store.names(), f"not loadable: {n}")

    def test_tampered_checklist_refuses(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-ct-tamper-"))
        try:
            for p in STORE_DIR.iterdir():
                if p.suffix in (".md", ".json") and p.is_file():
                    shutil.copy(p, tmp / p.name)
            victim = tmp / "critical-thinking.md"
            victim.write_text(victim.read_text() + "\nINJECTED LINE\n")
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
