# Critical Thinking Checklist (DEFINE phase)

Nine questions asked BEFORE anything else — while the mission contract is
being drafted, when "are we solving the right problem" is still cheap to
answer. Each question names the Awino mechanism that answers it. This is a
reference checklist, not a skill: it is pinned for integrity and wired into
`mission-definition`, never floor-routed into a turn's context.

1. "How do we know we're solving the right problem?"
   -> `discovery` interview + `mission-definition`: the grill rejects
   question-drips and plan-rushes; the contract states the objective in one
   sentence. If the problem statement can't survive the grill, it isn't
   ready to become a mission.
2. "Are we solving it the right way? (rigor vs efficiency given constraints)"
   -> `osmani-constraints`: the rigor/efficiency tradeoff is decided once,
   in writing, at contract approval — not improvised per task.
3. "If we don't know the sources, how do we determine root cause?"
   -> `triage`: reproduce first, then localize. Guessing at causes without
   a reproduction is not diagnosis.
4. "How do we break the key question into smaller analyzable questions?"
   -> `rigor-decomposition`: strategic breakdown before tactical work.
5. "Once we have hypotheses, how do we structure work to evaluate them?"
   -> NOTE: this is the seed of the hypothesis-driven Debug mode on the
   Awino roadmap (unbuilt — not ordered, not built here). Until it exists,
   the approximation is `rigor-decomposition` (hypotheses as sub-questions)
   + `rigor-iteration` (Reason-Act-Observe per hypothesis). Document the
   connection; do not build the mode now.
6. "What shortcuts can we take under constraints without unduly
   compromising rigor?"
   -> `osmani-constraints` + `osmani-failure-modes`: a shortcut is allowed
   only with an explicitly DECLARED rigor tradeoff (what is relaxed, why,
   what risk is accepted). Shortcuts without a declared tradeoff are a
   failure mode, not efficiency.
7. "Does the evidence sufficiently support the conclusions?"
   -> the judge (evidence-scored, quorum-based) + `rigor-proof-cycles`:
   "proof, not claims" — conclusions rest on journal evidence, never on
   the model's say-so.
8. "How do we know when we're done / good enough?"
   -> `definition-of-done`: the task is done only when its acceptance
   criteria AND the standing DoD both hold.
9. "How do we communicate the solution clearly to stakeholders?"
   -> `explainer`: Feynman-format explanations — if it can't be explained
   simply, it isn't understood yet.

## Referenced skills (machine-checked: every name must exist in manifest.json)

`discovery` `mission-definition` `osmani-constraints` `triage` `rigor-decomposition` `rigor-iteration` `osmani-failure-modes` `rigor-proof-cycles` `definition-of-done` `explainer`

---
Attribution: adapted from Addy Osmani (LinkedIn) critical-thinking questions for engineers.
Adapted for Awino: a REFERENCE CHECKLIST, not a skill — pinned in the skill
store for fail-closed integrity, never floor-routed. Wired into
`mission-definition` (DEFINE phase): the checklist runs while the contract
is drafted, before PLAN/BUILD exist. Most questions map to existing
machinery (no new skills created for the covered parts); each names the
Awino mechanism that answers it. Question 5 is explicitly the seed of the
unbuilt hypothesis-driven Debug mode — the connection is documented and the
mode is NOT built here. "Judge panel" in Q7 refers to the existing
judge-panel machinery (judges.py), not a skill name.
