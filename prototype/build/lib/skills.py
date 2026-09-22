"""Deterministic skill delivery: file-backed, sha256-pinned skill bodies.

Phase A exit criterion: "deterministic skill delivery demonstrated."
Phase C hardens this into mandatory loading.

The harness — never the model — decides which skills a turn may use
(stances.route_triple, from mission + input intent). This module delivers
the full body for each routed skill name:

- bodies live in ``prototype/skills/<name>.md`` (plain text, auditable),
- ``prototype/skills/manifest.json`` pins name -> sha256,
- the store verifies every body against the manifest at load; a mismatch
  raises SkillIntegrityError and the harness refuses to start the turn.

Determinism contract: the same skill name always yields the byte-identical
body, on every machine, on every run. The model never fetches skills; it
only ever sees what this store injected into the contract block.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

STORE_DIR = Path(__file__).parent / "skills"
MANIFEST = "manifest.json"


class SkillIntegrityError(Exception):
    """A skill body does not match its pinned hash, or the manifest is bad."""


class SkillStore:
    def __init__(self, store_dir: str | Path):
        self.dir = Path(store_dir)
        manifest_path = self.dir / MANIFEST
        if not manifest_path.is_file():
            raise SkillIntegrityError(
                f"skill manifest missing: {manifest_path}")
        try:
            pinned = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise SkillIntegrityError(
                f"skill manifest unreadable: {e}") from e
        if not isinstance(pinned, dict) or not pinned:
            raise SkillIntegrityError("skill manifest is empty or malformed")
        bodies: dict[str, str] = {}
        for name in sorted(pinned):
            want = pinned[name]
            path = self.dir / f"{name}.md"
            if not path.is_file():
                raise SkillIntegrityError(
                    f"skill file missing for pinned skill {name!r}")
            raw = path.read_bytes()
            got = hashlib.sha256(raw).hexdigest()
            if got != want:
                raise SkillIntegrityError(
                    f"skill {name!r} hash mismatch: manifest pins "
                    f"{want[:12]}..., file hashes {got[:12]}... "
                    f"(body may have been tampered with)")
            bodies[name] = raw.decode("utf-8")
        self._pinned = dict(pinned)
        self._bodies = bodies

    @classmethod
    def default(cls) -> "SkillStore":
        """The packaged skill set. Verified on first use; cached after."""
        global _DEFAULT
        if _DEFAULT is None:
            _DEFAULT = cls(STORE_DIR)
        return _DEFAULT

    def names(self) -> list[str]:
        return sorted(self._bodies)

    def pinned_hash(self, name: str) -> str | None:
        return self._pinned.get(name)

    def get(self, name: str) -> str | None:
        """Full body for a routed skill name, or None if unknown."""
        return self._bodies.get(name)

    def get_verified(self, name: str) -> str:
        body = self.get(name)
        if body is None:
            raise SkillIntegrityError(
                f"unknown skill {name!r}: not in the pinned manifest; "
                f"the harness cannot deliver what it cannot verify")
        return body

    def as_dict(self) -> dict:
        """Compatibility view: {name: {"name": name, "body": body}}."""
        return {n: {"name": n, "body": b} for n, b in self._bodies.items()}


_DEFAULT: SkillStore | None = None
