# Lifecycle Sequence (reference map)

The ordered skill chain per task type. Every skill declares "Routing: phase
floor X" (the territory); this document is the map — the canonical order in
which the mission planner walks the phases for each kind of work. Key
insight: not every task needs every skill. Phases are skipped only by
explicit task-type determination, never by laziness; each track below states
what it skips and why.

## Track 1: Full feature

- DEFINE: discovery interview (`discovery`) states the problem and grills the
  vague parts -> `mission-definition` drafts the contract (done-criteria are
  the acceptance criteria) -> `osmani-constraints` decides the quality bar
  once -> `osmani-adoption` determines the path: greenfield (full lifecycle
  from commit one) or brownfield (verification-first sequencing). Layer 1:
  `rigor-laws`, `rigor-distillation`.
- PLAN: task breakdown (`rigor-decomposition`) with acceptance criteria per
  slice; `rigor-scope` declares the boundary (WILL / WILL NOT modify);
  `decision-analysis` + devil's-advocate stance on non-obvious decisions;
  the premortem stance names the product failure modes. `domain` as needed.
- BUILD: incremental thin slices (`code`, `repo`); `osmani-tdd` +
  `rigor-proof-cycles` (red-green-refactor, oracle first); `osmani-security`
  as the code is written (threat model first on untrusted input/auth/data
  paths); `rigor-iteration` Reason-Act-Observe loop.
- VERIFY: full suite green (`testing`); `rigor-pentagonal-audit` reviews the
  evidence on five axes; `rigor-interrogation` via explicit skill_add for deep
  verification; `osmani-security` re-checks the diff on security-relevant
  paths. Exit: exit code 0.
- REVIEW: `osmani-code-review` five-axis diff review with severity labels;
  `osmani-failure-modes` self-scan at the phase boundary; `rigor-scope`
  touch audit (every file in the declared scope or reverted);
  `rigor-entropy` reduction pass; the thin `code-review` checklist.
- SHIP: `osmani-shipping` (pre-launch checklist, rollback plan written
  BEFORE deploy, staged rollout, error-budget gate); `rigor-checkpoint`
  binary commit gate (`verification`); final gate: `definition-of-done` —
  the task is done only when its acceptance criteria AND the standing DoD
  both hold.

## Track 2: Bugfix

Skips DEFINE by explicit determination: the mission and contract already
exist; the bug is a defect against them, not a new objective.
- PLAN: `triage` the complaint (reproduce-first discipline); determine path:
  untested legacy code -> characterization tests first (`osmani-adoption`
  hard rule — no modification of untested legacy code without them).
- BUILD: the prove-it pattern (`osmani-tdd`): failing reproduction test
  BEFORE the fix -> minimal fix -> test passes; `rigor-proof-cycles`.
- VERIFY: full suite, no regressions (`testing`).
- REVIEW: `osmani-code-review` — the fix AND the regression test are
  reviewed together; a bugfix without a regression test is incomplete.
- SHIP: `osmani-shipping`; `definition-of-done` gate.

## Track 3: Refactor (behavior-preserving)

Skips DEFINE by explicit determination: no new capability, the contract
already describes the behavior; the work is changing structure, not
behavior.
- PLAN: `rigor-scope` DECLARE the boundary; characterization tests FIRST
  for any untested code (`osmani-adoption` hard rule) — this is the single
  most expensive shortcut in brownfield work, and it is forbidden.
- BUILD: simplify in thin slices; tests stay green after every edit
  (`osmani-tdd` refactor discipline); `rigor-iteration`.
- VERIFY: full suite green; behavior unchanged (`testing`).
- REVIEW: `osmani-code-review` — did complexity REDUCE or merely relocate?
  (count the concepts a reader must hold); `rigor-scope` touch audit.
- SHIP: `definition-of-done` gate; `rigor-checkpoint`.

## Map vs territory

This document is the map. Each skill's "Routing: phase floor" declaration is
the territory — what the harness actually loads. The mission planner follows
the map; the router enforces the territory. Any disagreement between them
(a skill the map needs but no floor routes, or a floor routing a skill the
map never calls for) is a DEFECT to fix, not a discrepancy to silently
resolve. `tests/test_osmani.py` pins the map's skill references to the
manifest so dangling references fail loudly.

## What this map does NOT take from the source

- The source's interview/idea front steps (`interview-me`, `idea-refine`)
  map to Awino's existing discovery interview (`discovery`) — no new skill.
- Observability, API-design, and frontend skills were deliberately NOT
  ported (per the port selection: genuine gaps only). Where the source
  sequence calls for them, this map uses the Awino equivalents that exist
  (`osmani-shipping` monitoring, `osmani-constraints` architecture
  boundaries) and states the gap plainly instead of inventing coverage.

## Referenced skills (machine-checked: every name must exist in manifest.json)

`discovery` `mission-definition` `osmani-constraints` `osmani-adoption` `rigor-laws` `rigor-distillation` `rigor-decomposition` `rigor-scope` `decision-analysis` `domain` `code` `repo` `osmani-tdd` `rigor-proof-cycles` `osmani-security` `rigor-iteration` `testing` `rigor-pentagonal-audit` `rigor-interrogation` `code-review` `rigor-entropy` `osmani-code-review` `osmani-failure-modes` `verification` `rigor-checkpoint` `osmani-shipping` `definition-of-done` `triage`

---
Attribution: adapted from agent-skills skills/using-agent-skills "Lifecycle Sequence" (MIT, Addy Osmani).
Adapted for Awino: SUPPORTING REFERENCE, not a routed skill — pinned in the
skill store for fail-closed integrity, never floor-routed into a turn's
context. The source's 16-step slash-command sequence is rewritten as three
task-type tracks over Awino's mission phases (DEFINE/PLAN/BUILD/VERIFY/
REVIEW/SHIP; ACTIVE_PHASES in contract_loop.py are BUILD/VERIFY/REVIEW/SHIP,
with DEFINE/PLAN as the planning phases). Only skill names from Awino's real
inventory are referenced; unported source skills are explicitly excluded
above rather than dangling. Wired by reference: the mission planner follows
this map at DEFINE; `osmani-adoption` determines greenfield vs brownfield;
`osmani-shipping` + `definition-of-done` close every track. The map-vs-
territory rule makes disagreements between this document and the per-skill
Routing declarations a defect, not a judgment call.
