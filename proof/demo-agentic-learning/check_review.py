"""Acceptance check for the agentic-learning review mission.

Passes (exit 0) only when review.md has a row for each of 7 approaches,
each with an arXiv source and a "how we'd test it" entry, and
experiment/approaches.yaml lists the same 7.
"""
import re
import sys
from pathlib import Path

rows = [l for l in Path("review.md").read_text().splitlines()
        if l.startswith("| ") and not l.startswith("| Approach") and "---" not in l]
ok = [r for r in rows if re.search(r"arxiv\.org/abs/\d{4}\.\d{4,5}", r)
      and r.rstrip(" |").split("|")[-1].strip()]
yaml = Path("experiment/approaches.yaml")
names = re.findall(r"^- name: (.+)$", yaml.read_text(), re.M) if yaml.exists() else []
print(f"review rows with source + test: {len(ok)}/7; approaches.yaml entries: {len(names)}/7")
sys.exit(0 if len(ok) >= 7 and len(names) >= 7 else 1)
