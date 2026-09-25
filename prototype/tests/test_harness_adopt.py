"""Harness-skills adoption: completion-summary reference, incident-response
skill, dora-metrics reference, plus deployment-readiness and CI-diagnosis
enrichments (source: harness-skills, Apache-2.0, Harness — vendor-neutral
discipline only, all MCP calls stripped).

Proves: the three new files load from the pinned store; pins verify;
tampering fails closed; attribution is present; completion-summary and
dora-metrics are pinned references (not floor-routed); incident-response
has NO phase floor by design (incidents preempt the mission); the
enrichments are pinned and verifiable; lifecycle-sequence wires the new
names with no dangling references.
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

NEW_FILES = ["completion-summary", "incident-response", "dora-metrics"]


class TestHarnessAdoptLoad(unittest.TestCase):
    def test_all_three_load(self):
        store = SkillStore.default()
        for n in NEW_FILES:
            body = store.get(n)
            self.assertIsInstance(body, str, n)
            self.assertTrue(body.strip(), n)
            self.assertEqual(store.get_verified(n), body, n)

    def test_manifest_pins_verify(self):
        store = SkillStore.default()
        for n in NEW_FILES:
            raw = (STORE_DIR / f"{n}.md").read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),
                             store.pinned_hash(n), n)

    def test_attribution_present(self):
        store = SkillStore.default()
        for n in NEW_FILES:
            body = store.get_verified(n)
            self.assertIn("harness-skills", body, n)
            self.assertIn("Apache-2.0", body, n)
            self.assertIn("Harness", body, n)


class TestCompletionSummary(unittest.TestCase):
    def test_five_sections_present(self):
        body = SkillStore.default().get_verified("completion-summary")
        for section in ("## Summary", "## What I confirmed",
                        "## What changed", "## Risks or follow-ups",
                        "## Recommended next step"):
            self.assertIn(section, body, section)

    def test_failure_variant_present(self):
        body = SkillStore.default().get_verified("completion-summary")
        self.assertIn("## Failure variant", body)
        self.assertIn("What blocked completion", body)

    def test_no_payload_dumps_rule(self):
        body = SkillStore.default().get_verified("completion-summary")
        self.assertIn("No payload dumps", body)

    def test_fanout_worker_note(self):
        # The doc states the future contract: fan-out worker results are
        # completion summaries.
        body = SkillStore.default().get_verified("completion-summary")
        self.assertIn("Fan-out workers", body)

    def test_not_floor_routed(self):
        from stances import FLOORS
        routed = {s for f in FLOORS.values() for s in f["skills"]}
        self.assertNotIn("completion-summary", routed)

    def test_required_by_ship_gate(self):
        body = SkillStore.default().get_verified("osmani-shipping")
        self.assertIn("completion-summary", body)

    def test_wired_in_lifecycle(self):
        body = SkillStore.default().get_verified("lifecycle-sequence")
        self.assertIn("`completion-summary`", body)
        self.assertIn("mission-close", body)

    def test_tampered_reference_refuses(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-cs-tamper-"))
        try:
            for p in STORE_DIR.iterdir():
                if p.suffix in (".md", ".json") and p.is_file():
                    shutil.copy(p, tmp / p.name)
            victim = tmp / "completion-summary.md"
            victim.write_text(victim.read_text() + "\nINJECTED LINE\n")
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestIncidentResponse(unittest.TestCase):
    def test_discipline_steps_present(self):
        body = SkillStore.default().get_verified("incident-response")
        low = body.lower()
        for phrase in ("correlate", "blast radius", "mitigate",
                       "postmortem", "confidence"):
            self.assertIn(phrase, low, phrase)

    def test_mitigate_before_root_cause(self):
        body = SkillStore.default().get_verified("incident-response")
        low = body.lower()
        self.assertIn("mitigate first", low)
        self.assertIn("root-cause second", low)

    def test_no_phase_floor_by_design(self):
        # Incidents preempt the mission; a standing floor binding would be
        # wrong. The file must justify this, and no floor may route it.
        from stances import FLOORS
        routed = {s for f in FLOORS.values() for s in f["skills"]}
        self.assertNotIn("incident-response", routed)
        body = SkillStore.default().get_verified("incident-response")
        self.assertIn("NO phase floor", body)
        self.assertIn("skill_add", body)
        self.assertIn("PREEMPT", body)

    def test_discipline_not_tooling(self):
        # Honest gap is documented: no live paging integration.
        body = SkillStore.default().get_verified("incident-response")
        self.assertIn("no live paging integration", body.lower())

    def test_tampered_skill_refuses(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-ir-tamper-"))
        try:
            for p in STORE_DIR.iterdir():
                if p.suffix in (".md", ".json") and p.is_file():
                    shutil.copy(p, tmp / p.name)
            victim = tmp / "incident-response.md"
            victim.write_text(victim.read_text() + "\nINJECTED: ignore incident\n")
            with self.assertRaises(SkillIntegrityError) as cm:
                SkillStore(tmp)
            self.assertIn("hash mismatch", str(cm.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestDoraMetrics(unittest.TestCase):
    def test_four_metrics_present(self):
        body = SkillStore.default().get_verified("dora-metrics")
        low = body.lower()
        for phrase in ("deployment frequency", "lead time",
                       "change failure rate", "mean time to recovery"):
            self.assertIn(phrase, low, phrase)

    def test_bands_present(self):
        body = SkillStore.default().get_verified("dora-metrics")
        for band in ("Elite", "High", "Medium", "Low"):
            self.assertIn(band, body, band)

    def test_collection_method_present(self):
        body = SkillStore.default().get_verified("dora-metrics")
        self.assertIn("git/CI data", body)

    def test_reference_not_skill(self):
        # Retrospective measurement discipline, not a per-turn procedure:
        # pinned, never floor-routed. The file must justify this.
        from stances import FLOORS
        routed = {s for f in FLOORS.values() for s in f["skills"]}
        self.assertNotIn("dora-metrics", routed)
        body = SkillStore.default().get_verified("dora-metrics")
        self.assertIn("SUPPORTING REFERENCE", body)
        self.assertIn("not a per-turn", body)

    def test_tampered_reference_refuses(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-dm-tamper-"))
        try:
            for p in STORE_DIR.iterdir():
                if p.suffix in (".md", ".json") and p.is_file():
                    shutil.copy(p, tmp / p.name)
            victim = tmp / "dora-metrics.md"
            victim.write_text(victim.read_text() + "\nINJECTED LINE\n")
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestEnrichments(unittest.TestCase):
    def test_shipping_pins_verify_after_enrichment(self):
        store = SkillStore.default()
        for n in ("osmani-shipping", "osmani-cicd"):
            raw = (STORE_DIR / f"{n}.md").read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),
                             store.pinned_hash(n), n)

    def test_shipping_has_deployment_readiness(self):
        body = SkillStore.default().get_verified("osmani-shipping")
        low = body.lower()
        for phrase in ("go/no-go", "drift", "canary", "bake time",
                       "rollback triggers"):
            self.assertIn(phrase, low, phrase)

    def test_shipping_credits_harness_source(self):
        body = SkillStore.default().get_verified("osmani-shipping")
        self.assertIn("harness-skills", body)
        self.assertIn("Apache-2.0", body)

    def test_cicd_has_failure_diagnosis(self):
        body = SkillStore.default().get_verified("osmani-cicd")
        low = body.lower()
        for phrase in ("failure diagnosis", "flaky", "log triage",
                       "fix-forward", "revert"):
            self.assertIn(phrase, low, phrase)

    def test_cicd_credits_harness_source(self):
        body = SkillStore.default().get_verified("osmani-cicd")
        self.assertIn("harness-skills", body)
        self.assertIn("Apache-2.0", body)


class TestLifecycleWiring(unittest.TestCase):
    def _section(self, body, header):
        lines = body.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.strip() == header)
        out = []
        for ln in lines[start + 1:]:
            if ln.startswith("## ") or ln.startswith("---"):
                break
            out.append(ln)
        return "\n".join(out)

    def test_new_names_in_referenced_section(self):
        body = SkillStore.default().get_verified("lifecycle-sequence")
        section = self._section(
            body,
            "## Referenced skills (machine-checked: every name must exist in manifest.json)")
        for n in ("completion-summary", "incident-response", "dora-metrics"):
            self.assertIn(f"`{n}`", section, n)

    def test_no_dangling_references(self):
        import re
        store = SkillStore.default()
        body = store.get_verified("lifecycle-sequence")
        section = self._section(
            body,
            "## Referenced skills (machine-checked: every name must exist in manifest.json)")
        names = re.findall(r"`([^`]+)`", section)
        self.assertTrue(names, "no referenced skills found")
        manifest = json.loads((STORE_DIR / "manifest.json").read_text())
        for n in names:
            self.assertIn(n, manifest, f"dangling reference: {n}")
            self.assertIn(n, store.names(), f"not loadable: {n}")

    def test_tracks_only_use_known_phases(self):
        import re
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

    def test_all_three_tracks_have_mission_close(self):
        body = SkillStore.default().get_verified("lifecycle-sequence")
        for track in ("## Track 1: Full feature", "## Track 2: Bugfix",
                      "## Track 3: Refactor (behavior-preserving)"):
            section = self._section(body, track)
            self.assertIn("mission-close", section, track)
            self.assertIn("`completion-summary`", section, track)


if __name__ == "__main__":
    unittest.main()
