"""Deterministic skill delivery: file-backed, sha256-pinned skill bodies.

The harness delivers full skill bodies into the contract block; the model
never fetches skills. These tests prove the delivery is deterministic and
tamper-evident: same name -> byte-identical body every time, and any
modification of a body or the manifest refuses to load.
"""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from skills import SkillIntegrityError, SkillStore, STORE_DIR


class TestSkillStore(unittest.TestCase):
    def test_default_store_loads_all_skills(self):
        store = SkillStore.default()
        names = store.names()
        self.assertEqual(len(names), 11)
        for n in names:
            body = store.get(n)
            self.assertIsInstance(body, str)
            self.assertTrue(body.strip(), n)

    def test_deterministic_same_name_same_bytes(self):
        a = SkillStore.default()
        b = SkillStore(STORE_DIR)  # fresh load, not the cache
        for n in a.names():
            self.assertEqual(a.get(n), b.get(n), n)
            self.assertEqual(a.pinned_hash(n), b.pinned_hash(n), n)

    def test_bodies_match_manifest_hashes(self):
        store = SkillStore.default()
        for n in store.names():
            raw = (STORE_DIR / f"{n}.md").read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),
                             store.pinned_hash(n), n)

    def test_unknown_skill_returns_none(self):
        self.assertIsNone(SkillStore.default().get("no-such-skill"))

    def test_get_verified_unknown_raises(self):
        with self.assertRaises(SkillIntegrityError):
            SkillStore.default().get_verified("no-such-skill")

    def test_tampered_body_refuses_to_load(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-skills-tamper-"))
        try:
            for p in STORE_DIR.iterdir():
                shutil.copy(p, tmp / p.name)
            victim = tmp / "repo.md"
            victim.write_text(victim.read_text() + "\nEXTRA INJECTED LINE\n")
            with self.assertRaises(SkillIntegrityError) as cm:
                SkillStore(tmp)
            self.assertIn("hash mismatch", str(cm.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tampered_manifest_refuses_to_load(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-skills-manifest-"))
        try:
            for p in STORE_DIR.iterdir():
                shutil.copy(p, tmp / p.name)
            man = json.loads((tmp / "manifest.json").read_text())
            man["repo"] = "0" * 64
            (tmp / "manifest.json").write_text(json.dumps(man))
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_skill_file_refuses_to_load(self):
        tmp = Path(tempfile.mkdtemp(prefix="awino-skills-missing-"))
        try:
            for p in STORE_DIR.iterdir():
                shutil.copy(p, tmp / p.name)
            (tmp / "code.md").unlink()
            with self.assertRaises(SkillIntegrityError):
                SkillStore(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_contract_injects_full_bodies_not_names(self):
        # The compiled contract block carries the full body text the model
        # will see — not just a name the model would have to fetch.
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from contract import compile_contract, SKILLS
        from state import ProjectState
        home = tempfile.mkdtemp(prefix="awino-skills-contract-")
        st = ProjectState(home, "sk")
        st.record("skills_routed", {"skills": ["repo", "code"],
                                   "trigger": "test"})
        block = compile_contract(st, turn_no=1)
        for name in ("repo", "code"):
            body = SKILLS[name]["body"].strip().split("\n")[0]
            self.assertIn(body, block,
                          f"full body of skill {name!r} missing from contract")
        # an unrouted skill must NOT be in the block
        self.assertNotIn("PROCEDURE testing:", block)


if __name__ == "__main__":
    unittest.main()
