"""Layered loading: core budget, layer placement, mechanical gates, trivial tier.

Covers the four-layer runtime adapted from
vscarpenter/claude-code-build-system (MIT), Part 0:
  core < 600 words (test-enforced) / ceremony routed by phase-intent-role /
  mechanical gates in code / reference pinned but never auto-loaded.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from common import make_loop  # noqa: E402
from contract import compile_contract  # noqa: E402
from loop import Loop  # noqa: E402
from backends import ScriptedBackend, ScriptedJudge  # noqa: E402
from skills import SkillStore, STORE_DIR  # noqa: E402
import stances  # noqa: E402
import modes  # noqa: E402
from floor_checks import (  # noqa: E402
    check_diff, check_files, diff_for_new_file,
)

# The always-injected rule block: the preamble plus these fixed sections.
# Dynamic state (mission text, done criteria, routed skill bodies, network
# declarations, progress) is not core — it varies per turn.
CORE_SECTIONS = ["## CONSTRAINTS", "## AUTONOMY", "## SETUP",
                 "## CALIBRATION", "## VERIFICATION",
                 "## STOP CONDITION", "## YOUR OUTPUT"]
CORE_BUDGET_WORDS = 600


def core_text(block: str) -> str:
    pre = block.split("## GOAL")[0]
    parts = [pre]
    for s in CORE_SECTIONS:
        start = block.index(s)
        nxt = len(block)
        for s2 in CORE_SECTIONS:
            if s2 == s:
                continue
            i = block.find(s2, start + 1)
            if i != -1:
                nxt = min(nxt, i)
        parts.append(block[start:nxt])
    return "\n".join(parts)


class TestCoreBudget(unittest.TestCase):
    def test_core_under_600_words(self):
        loop, _ = make_loop()
        loop.set_mission("Fix the login bug", ["artifact:fix.py", "manual"])
        block = compile_contract(loop.state)
        n = len(core_text(block).split())
        self.assertLess(n, CORE_BUDGET_WORDS,
                        f"core is {n} words, budget is {CORE_BUDGET_WORDS}")

    def test_core_sections_present(self):
        loop, _ = make_loop()
        loop.set_mission("M", ["manual"])
        block = compile_contract(loop.state)
        for s in CORE_SECTIONS:
            self.assertIn(s, block, f"core section missing: {s}")

    def test_precedence_in_core(self):
        loop, _ = make_loop()
        loop.set_mission("M", ["manual"])
        block = compile_contract(loop.state)
        # Contract preamble lines are `#`-comments; strip markers and
        # collapse whitespace before checking the precedence chain.
        low = re.sub(r"\s+", " ", re.sub(r"#", "", block.lower()))
        for term in ["safety", "irreversibility", "explicit instructions",
                     "approved spec", "process rules", "style"]:
            self.assertIn(term, low, f"precedence term missing: {term}")


class TestLayerPlacement(unittest.TestCase):
    def test_every_manifest_entry_has_exactly_one_layer(self):
        store = SkillStore.default()
        manifest = json.loads((STORE_DIR / "manifest.json").read_text())
        for name in manifest:
            layer = store.layer_of(name)  # KeyError if unassigned
            self.assertIn(layer, ("ceremony", "mechanical", "reference"),
                          f"{name}: bad layer {layer!r}")

    def test_registry_matches_manifest_exactly(self):
        layers = json.loads((STORE_DIR / "layers.json").read_text())
        manifest = json.loads((STORE_DIR / "manifest.json").read_text())
        self.assertEqual(set(layers), set(manifest))

    def test_layer_counts(self):
        # 43 ceremony + 7 reference; no .md file is mechanical (mechanical
        # is code: floor_checks.py and the existing fail-closed gates).
        store = SkillStore.default()
        counts = {}
        for name in store.names():
            counts[store.layer_of(name)] = counts.get(store.layer_of(name), 0) + 1
        self.assertEqual(counts, {"ceremony": 43, "reference": 7})

    def test_spot_assignments(self):
        store = SkillStore.default()
        self.assertEqual(store.layer_of("testing"), "ceremony")
        self.assertEqual(store.layer_of("osmani-constraints"), "ceremony")
        self.assertEqual(store.layer_of("mode-software-engineer"), "ceremony")
        self.assertEqual(store.route_of("mode-software-engineer"), "role")
        self.assertEqual(store.layer_of("lifecycle-sequence"), "reference")
        self.assertEqual(store.layer_of("definition-of-done"), "reference")
        self.assertEqual(store.layer_of("layered-loading"), "reference")
        self.assertEqual(store.layer_of("verify"), "reference")

    def test_registry_out_of_sync_fails_closed(self):
        tmp = tempfile.mkdtemp(prefix="awino-layers-")
        try:
            for f in os.listdir(STORE_DIR):
                src = STORE_DIR / f
                if src.is_file():
                    shutil.copy2(src, os.path.join(tmp, f))
            layers_path = os.path.join(tmp, "layers.json")
            with open(layers_path) as fh:
                layers = json.load(fh)
            layers.pop("testing")
            with open(layers_path, "w") as fh:
                json.dump(layers, fh)
            from skills import SkillIntegrityError
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_user_registry_without_layers_defaults_to_ceremony(self):
        # A user-admitted registry (skill_add) ships no layers.json: its
        # skills enter only through explicit, screened admission — the
        # ceremony on-demand path — so they default there instead of
        # failing closed.
        import hashlib
        tmp = tempfile.mkdtemp(prefix="awino-userreg-")
        try:
            body = b"# my skill\n\nVERIFY: true\n"
            manifest = {"my-skill":
                        hashlib.sha256(body).hexdigest()}
            with open(os.path.join(tmp, "manifest.json"), "w") as fh:
                json.dump(manifest, fh)
            with open(os.path.join(tmp, "my-skill.md"), "wb") as fh:
                fh.write(body)
            store = SkillStore(tmp)
            self.assertEqual(store.layer_of("my-skill"), "ceremony")
            self.assertEqual(store.route_of("my-skill"), "ondemand")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestReferenceNeverAutoLoaded(unittest.TestCase):
    def _auto_routed(self):
        routed = set()
        for floor in stances.FLOORS.values():
            routed.update(floor["skills"])
        for _intent, _rx, _mode, _chain, skills in stances.INTENT_TABLE:
            routed.update(skills)
        routed.update(stances._INTENT_RIGOR["fix"])
        routed.update(stances._INTENT_RIGOR["ship"])
        # new-task intent fast-path
        routed.update(["mission-definition", "discovery",
                       "rigor-distillation", "rigor-laws"])
        routed.update(modes.ROLE_SKILL_NAMES.values())
        return routed

    def test_no_reference_skill_is_auto_routed(self):
        store = SkillStore.default()
        refs = {n for n in store.names()
                if store.layer_of(n) == "reference"}
        self.assertTrue(refs, "no reference skills found")
        routed = self._auto_routed()
        leaked = refs & routed
        self.assertEqual(leaked, set(),
                         f"reference skills auto-routed: {sorted(leaked)}")

    def test_reference_bodies_absent_from_compiled_contract(self):
        # The contract injects only routed skill bodies; a reference doc's
        # distinctive first line must never appear, in any phase.
        store = SkillStore.default()
        refs = [n for n in store.names()
                if store.layer_of(n) == "reference"]
        markers = {n: store.get(n).splitlines()[0] for n in refs}
        loop, _ = make_loop()
        loop.set_mission("M", ["manual"])
        for phase in ("DEFINE", "PLAN", "BUILD", "VERIFY", "REVIEW", "SHIP"):
            loop.state.snapshot["phase"] = phase
            # route the phase floor's skills the way the harness does
            loop.state.snapshot["skills"] = list(
                stances.FLOORS[phase]["skills"])
            block = compile_contract(loop.state)
            for n, marker in markers.items():
                self.assertNotIn(marker, block,
                                 f"reference {n} leaked into {phase} contract")


class TestFloorChecks(unittest.TestCase):
    DIFF_HDR = ("diff --git a/f.py b/f.py\nnew file mode 100644\n"
                "--- /dev/null\n+++ b/f.py\n@@ -0,0 +1,3 @@\n")

    def _diff(self, *added_lines):
        return self.DIFF_HDR + "".join("+" + l + "\n" for l in added_lines)

    def test_clean_diff_passes(self):
        d = self._diff("def f():", "    return 1")
        self.assertEqual(check_diff(d), [])

    def test_suppression_comments_fail(self):
        for line in ("x = 1  # noqa", "# type: ignore", "// @ts-ignore",
                     "# eslint-disable-next-line no-unused-vars"):
            with self.subTest(line=line):
                found = check_diff(self._diff(line))
                self.assertTrue(
                    any(f["check"] == "suppression-comment" for f in found),
                    f"not flagged: {line}")

    def test_removed_suppression_not_flagged(self):
        d = ("diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
             "@@ -1 +1 @@\n-# type: ignore\n+x = 1\n")
        self.assertEqual(check_diff(d), [])

    def test_stubs_fail(self):
        for line in ("    raise NotImplementedError", "    ...",
                     "    TODO: implement this", "# FIXME later"):
            with self.subTest(line=line):
                found = check_diff(self._diff(line))
                self.assertTrue(any(f["check"] == "stub" for f in found),
                                f"not flagged: {line}")

    def test_empty_catch_block_fails(self):
        d = self._diff("try:", "    f()", "except ValueError:", "    pass")
        self.assertTrue(any(f["check"] == "stub" and "catch" in f["detail"]
                            for f in check_diff(d)))

    def test_skipped_tests_fail_without_reason(self):
        # A bare skip with no reason string fails the floor. (A Go
        # t.Skip always takes an argument; the empty-string form is the
        # reason-less case the check must catch.)
        for line in ("@pytest.mark.skip", "@unittest.skip",
                     "describe.skip('x', () => {})", 't.Skip("")'):
            with self.subTest(line=line):
                found = check_diff(self._diff(line))
                self.assertTrue(
                    any(f["check"] == "skipped-test" for f in found),
                    f"not flagged: {line}")

    def test_skip_with_reason_passes(self):
        d = self._diff('@pytest.mark.skip(reason="needs GPU runner")')
        self.assertEqual(check_diff(d), [])

    def test_secrets_fail(self):
        for line in ('api_key = "sk-live-9f2c4a7b1e3d5f6a8b0c"',
                     "password = 'CorrectHorse9BatteryStaple'",
                     "AKIAIOSFODNN7EXAMPLE"):
            with self.subTest(line=line):
                found = check_diff(self._diff(line))
                self.assertTrue(any(f["check"] == "secret" for f in found),
                                f"not flagged: {line}")

    def test_secret_placeholders_pass(self):
        for line in ('api_key = "YOUR_API_KEY"',
                     'password = "xxx"',
                     'token = "test"'):
            with self.subTest(line=line):
                self.assertEqual(check_diff(self._diff(line)), [],
                                 f"false positive: {line}")

    def test_deleted_test_file_fails(self):
        d = ("diff --git a/tests/test_old.py b/tests/test_old.py\n"
             "deleted file mode 100644\n"
             "--- a/tests/test_old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-def t():\n")
        found = check_diff(d)
        self.assertTrue(any(f["check"] == "deleted-test" for f in found))

    def test_diff_for_new_file_roundtrip(self):
        d = diff_for_new_file("new.py", "x = 1  # noqa\n")
        found = check_diff(d)
        self.assertTrue(any(f["check"] == "suppression-comment"
                            and f["file"] == "new.py" for f in found))

    def test_check_files(self):
        found = check_files([("a.py", "ok = 1\n"),
                             ("b.py", "pw = 's3cr3t-value-12345'\n"),
                             ("c.bin", "a\x00b")])
        self.assertTrue(any(f["file"] == "b.py" and f["check"] == "secret"
                            for f in found))
        self.assertFalse(any(f["file"] == "a.py" for f in found))
        self.assertFalse(any(f["file"] == "c.bin" for f in found))

    def test_multiple_checks_all_reported(self):
        d = self._diff("x = 1  # noqa", "    raise NotImplementedError",
                       "@pytest.mark.skip")
        checks = {f["check"] for f in check_diff(d)}
        self.assertEqual(checks, {"suppression-comment", "stub",
                                  "skipped-test"})


def _git_available():
    return shutil.which("git") is not None


class TestFloorGateIntegration(unittest.TestCase):
    """REVIEW -> SHIP refuses on floor violations, passes when clean."""

    def _loop_in_git_repo(self):
        if not _git_available():
            self.skipTest("git not available")
        repo = tempfile.mkdtemp(prefix="awino-floor-")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
        subprocess.run(["git", "config", "user.email", "t@t"],
                       cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo,
                       check=True)
        with open(os.path.join(repo, "base.py"), "w") as f:
            f.write("OK = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "base"],
                       cwd=repo, check=True, env=env)
        home = tempfile.mkdtemp(prefix="awino-test-")
        loop = Loop(home, "p1", ScriptedBackend([]), ScriptedJudge(),
                    sandbox_dir=repo)
        loop.set_mission("M", ["manual"])
        loop.state.snapshot["phase"] = "REVIEW"
        return loop, home, repo

    def test_clean_diff_allows_ship(self):
        loop, home, repo = self._loop_in_git_repo()
        try:
            with open(os.path.join(repo, "work.py"), "w") as f:
                f.write("def f():\n    return 1\n")
            r = loop.request_phase("SHIP", reason="done")
            self.assertEqual(r["status"], "ok", r.get("said"))
            self.assertTrue(any(e["type"] == "floor_checks"
                                and e["data"]["status"] == "pass"
                                for e in loop.state.events))
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(repo, ignore_errors=True)

    def test_violation_refuses_ship(self):
        loop, home, repo = self._loop_in_git_repo()
        try:
            with open(os.path.join(repo, "work.py"), "w") as f:
                f.write("def f():\n    ...  # not done\n")
            r = loop.request_phase("SHIP", reason="done")
            self.assertEqual(r["status"], "refused")
            self.assertIn("constraints floor", r["said"])
            self.assertIn("stub", r["said"])
            self.assertEqual(loop.state.snapshot["phase"], "REVIEW")
            self.assertTrue(any(e["type"] == "floor_checks"
                                and e["data"]["status"] == "fail"
                                for e in loop.state.events))
            self.assertTrue(any(e["type"] == "transition_refused"
                                for e in loop.state.events))
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(repo, ignore_errors=True)

    def test_secret_in_new_file_refuses_ship(self):
        loop, home, repo = self._loop_in_git_repo()
        try:
            with open(os.path.join(repo, "cfg.py"), "w") as f:
                f.write("api_key = 'sk-live-9f2c4a7b1e3d5f6a8b0c'\n")
            r = loop.request_phase("SHIP", reason="done")
            self.assertEqual(r["status"], "refused")
            self.assertIn("secret", r["said"])
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(repo, ignore_errors=True)

    def test_no_vcs_baseline_skips_journaled(self):
        # Plain temp dir, no git: the gate skips (journaled), transition ok.
        loop, home = make_loop()
        loop.set_mission("M", ["manual"])
        loop.state.snapshot["phase"] = "REVIEW"
        try:
            r = loop.request_phase("SHIP", reason="done")
            self.assertEqual(r["status"], "ok", r.get("said"))
            self.assertTrue(any(e["type"] == "floor_checks"
                                and e["data"]["status"] == "skipped"
                                for e in loop.state.events))
        finally:
            shutil.rmtree(home, ignore_errors=True)


class TestTrivialTier(unittest.TestCase):
    def _lifecycle(self):
        store = SkillStore.default()
        return store.get_verified("lifecycle-sequence")

    def test_track_zero_exists(self):
        body = self._lifecycle()
        self.assertIn("Track 0", body)

    def test_trivial_criteria(self):
        body = self._lifecycle().lower()
        for term in ["one file", "fewer than 20", "no interface change"]:
            self.assertIn(term, body, f"trivial criterion missing: {term}")

    def test_trivial_route_fix_verify_commit(self):
        body = self._lifecycle()
        track0 = body.split("## Track 0")[1].split("## Track 1")[0]
        for phase in ["BUILD", "VERIFY", "REVIEW", "SHIP"]:
            self.assertIn(phase, track0, f"{phase} missing from Track 0")
        self.assertIn("Skips DEFINE and PLAN", track0)

    def test_trivial_no_cicd_bypass(self):
        body = self._lifecycle()
        track0 = body.split("## Track 0")[1].split("## Track 1")[0]
        self.assertIn("osmani-cicd", track0)

    def test_boundary_rule(self):
        body = self._lifecycle()
        self.assertIn("boundary rule", body.lower())
        self.assertIn("which tier you picked and why", body.lower())

    def test_track_zero_skills_all_exist(self):
        # No dangling references in the new track.
        body = self._lifecycle()
        track0 = body.split("## Track 0")[1].split("## Track 1")[0]
        manifest = json.loads((STORE_DIR / "manifest.json").read_text())
        for name in re.findall(r"`([^`]+)`", track0):
            self.assertIn(name, manifest, f"dangling reference: {name}")


if __name__ == "__main__":
    unittest.main()
