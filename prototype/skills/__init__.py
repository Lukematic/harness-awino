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

STORE_DIR = Path(__file__).parent
MANIFEST = "manifest.json"
NETWORK_META = "network.json"  # Track C: skill -> network declaration


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
            # Windows checkouts (git autocrlf) store CRLF on disk while the
            # manifest pins LF bytes. Normalize line endings before hashing
            # so a line-ending-only difference is not mistaken for tampering;
            # any content change still alters the digest. The canonical LF
            # body is also what gets delivered to the harness.
            raw = raw.replace(b"\r\n", b"\n")
            got = hashlib.sha256(raw).hexdigest()
            if got != want:
                raise SkillIntegrityError(
                    f"skill {name!r} hash mismatch: manifest pins "
                    f"{want[:12]}..., file hashes {got[:12]}... "
                    f"(body may have been tampered with)")
            bodies[name] = raw.decode("utf-8")
        self._pinned = dict(pinned)
        self._bodies = bodies
        # Layered loading: every pinned skill is assigned exactly one
        # layer (ceremony / mechanical / reference). The built-in store
        # ships layers.json, and it must match the manifest exactly —
        # fail-closed like the manifest itself. A user-admitted registry
        # (skill_add) has no layers.json: its skills enter only through
        # explicit, screened admission, which IS the ceremony on-demand
        # path, so they default to ceremony/ondemand.
        layers_path = self.dir / "layers.json"
        if layers_path.is_file():
            try:
                layers = json.loads(layers_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                raise SkillIntegrityError(
                    f"skill layer registry unreadable: {e}") from e
            if set(layers) != set(bodies):
                raise SkillIntegrityError(
                    "layer registry out of sync with manifest: "
                    f"missing={sorted(set(bodies) - set(layers))} "
                    f"extra={sorted(set(layers) - set(bodies))}")
            for name, spec in layers.items():
                if not isinstance(spec, dict) or spec.get("layer") not in (
                        "ceremony", "mechanical", "reference"):
                    raise SkillIntegrityError(
                        f"skill {name!r} has an invalid layer assignment: "
                        f"{spec!r}")
        else:
            layers = {}
        self._layers = {
            name: layers.get(name,
                             {"layer": "ceremony", "route": "ondemand"})
            for name in bodies}
        # Track C: network declarations. Absence of the file or of an entry
        # means "network: none". This meta file is advisory (surfacing +
        # egress audit); integrity of the bodies is still the sha256 manifest.
        self._network: dict[str, dict] = {}
        meta_path = self.dir / NETWORK_META
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
                if isinstance(meta, dict):
                    self._network = {k: v for k, v in meta.items()
                                     if isinstance(v, dict)
                                     and not k.startswith("_")}
            except (json.JSONDecodeError, OSError):
                pass

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

    def layer_of(self, name: str) -> str:
        """The loading layer for a skill: ceremony, mechanical, or reference."""
        return self._layers[name]["layer"]

    def route_of(self, name: str) -> str:
        """How a ceremony skill reaches a turn: phase/intent/role/ondemand.

        Reference entries report "none" — they are never routed.
        """
        return self._layers[name]["route"]

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

    def network_declaration(self, name: str) -> dict:
        """Track C: network declaration for a skill.

        Returns {"network": "none"|"declared", "destinations": [...],
        "why": "..."}. Absence of an entry means "none" — the safe default.
        """
        decl = self._network.get(name, {})
        network = decl.get("network", "none")
        if network not in ("none", "declared"):
            network = "none"
        dests = decl.get("destinations", [])
        return {"network": network,
                "destinations": list(dests) if isinstance(dests, list) else [],
                "why": str(decl.get("why", ""))}

    def as_dict(self) -> dict:
        """Compatibility view: {name: {"name": name, "body": body}}."""
        return {n: {"name": n, "body": b} for n, b in self._bodies.items()}


_DEFAULT: SkillStore | None = None
