"""Acceptance check for the live agentic-learning mission: an actual paper.

Passes (exit 0) only when review.md is a written literature review, not a
table: the required sections, at least 1,500 words, one subsection per
approach (7+), 7 distinct arXiv sources, and experiment/approaches.yaml
listing 7 approaches. Prints what is missing so the model can fix it.
"""
import re
import sys
from pathlib import Path

REQUIRED = ["abstract", "scope", "comparison", "challenge", "experiment",
            "recommendation", "references"]
problems = []
p = Path("review.md")
text = p.read_text() if p.exists() else ""
if not text:
    problems.append("review.md missing or empty")
heads = [h.strip().lower() for h in re.findall(r"^##\s+(.+)$", text, re.M)]
for r in REQUIRED:
    if not any(r in h for h in heads):
        problems.append(f"missing a '## ...{r.title()}...' section")
subs = re.findall(r"^###\s+.+$", text, re.M)
if len(subs) < 7:
    problems.append(f"only {len(subs)} '### ' approach subsections (need 7)")
words = len(re.findall(r"\b\w+\b", text))
if words < 1500:
    problems.append(f"only {words} words (need 1,500)")
ids = set(re.findall(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", text))
if len(ids) < 7:
    problems.append(f"only {len(ids)} distinct arXiv sources (need 7)")
y = Path("experiment/approaches.yaml")
names = re.findall(r"^- name: .+$", y.read_text(), re.M) if y.exists() else []
if len(names) < 7:
    problems.append(f"experiment/approaches.yaml lists {len(names)} approaches (need 7)")

print(f"words={words} sections={len(heads)} approach_subsections={len(subs)} "
      f"arxiv_sources={len(ids)} yaml_entries={len(names)}")
for pr in problems:
    print("MISSING:", pr)
sys.exit(1 if problems else 0)
