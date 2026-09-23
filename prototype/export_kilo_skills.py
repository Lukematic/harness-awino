#!/usr/bin/env python3
"""Export the A.W.I.N.O. pinned skill registry to Kilo Code skill format.

Reads the hash-verified SkillStore (the same registry the harness routes
from) and writes one Kilo SKILL.md per skill:

    <target>/<skill-name>/SKILL.md

Kilo loads skills from ~/.kilo/skills/ (global) or .kilo/skills/ (project).

Usage:
    python3 export_kilo_skills.py [target_dir]   # default: ~/.kilo/skills

Stdlib only. Fails closed: a tampered registry raises instead of exporting.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skills import SkillStore  # noqa: E402


def _description(name: str, body: str) -> str:
    """First non-empty, non-heading line of the body as the description."""
    for line in body.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:160]
    return f"A.W.I.N.O. skill: {name}"


def export(target_dir: str) -> list[str]:
    store = SkillStore.default()  # verified on load; tamper raises
    written = []
    for name in store.names():
        body = store.get_verified(name)
        dest_dir = os.path.join(target_dir, name)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "SKILL.md")
        with open(dest, "w", encoding="utf-8") as f:
            f.write("---\n")
            f.write(f"name: {name}\n")
            f.write(f"description: {_description(name, body)}\n")
            f.write("---\n\n")
            f.write(body)
            if not body.endswith("\n"):
                f.write("\n")
        written.append(dest)
    return written


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/.kilo/skills")
    written = export(target)
    print(f"exported {len(written)} skills to {target}")
    for w in written:
        print("  " + w)


if __name__ == "__main__":
    main()
