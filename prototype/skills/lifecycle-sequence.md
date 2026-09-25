# Lifecycle Sequence (reference map)

The ordered skill chain per task type. Every skill declares "Routing: phase
floor X" (the territory); this document is the map — the canonical order in
which the mission planner walks the phases for each kind of work. Key
insight: not every task needs every skill. Phases are skipped only by
explicit task-type determination, never by laziness; each track below states
what it skips and why.

## Track 1: Full feature

- DEFINE: discovery interview (`discovery`) states the problem and grills the
  vague parts -> `osmani-idea-refine` sharpens the interview output
  (divergent->convergent, one-pager with a Not Doing list) BEFORE any spec
  is written -> `mission-definition` drafts the contract (done-criteria are
  the acceptance criteria) -> `osmani-constraints` decides the quality bar
  once -> `osmani-adoption` determines the path: greenfield (full lifecycle
  from commit one) or brownfield (verification-first sequencing). Layer 1:
  `rigor-laws`, `rigor-distillation`.
- PLAN: task breakdown (`rigor-decomposition`) with acceptance criteria per
  slice; `rigor-scope` declares the boundary (WILL / WILL NOT modify);
  `osmani-api-design` defines interface contracts before implementation
  (Hyrum's Law at every seam); `decision-analysis` + devil's-advocate stance
  on non-obvious decisions; `osmani-doubt` runs doubt cycles on the
  non-trivial ones (claim, extract, adversarial fresh-context review,
  reconcile, stop — bounded at 3 cycles); the premortem stance names the
  product failure modes. `domain` as needed.
- BUILD: incremental thin slices (`code`, `repo`); `osmani-tdd` +
  `rigor-proof-cycles` (red-green-refactor, oracle first); `osmani-security`
  as the code is written (threat model first on untrusted input/auth/data
  paths); `osmani-source-driven` verifies framework patterns against
  official docs (cited, not from memory); `osmani-observability` instruments
  the system as it is built (on-call questions first, then signals);
  `rigor-iteration` Reason-Act-Observe loop.
- VERIFY: full suite green (`testing`); `rigor-pentagonal-audit` reviews the
  evidence on five axes; `rigor-interrogation` via explicit skill_add for deep
  verification; `osmani-security` re-checks the diff on security-relevant
  paths. Exit: exit code 0.
- REVIEW: `osmani-code-review` five-axis diff review with severity labels;
  `osmani-adrs` — every significant decision in the diff has its ADR (written
  when the decision was made, reviewed before ship); `osmani-failure-modes`
  self-scan at the phase boundary; `rigor-scope` touch audit (every file in
  the declared scope or reverted); `rigor-entropy` reduction pass; the thin
  `code-review` checklist.
- SHIP: `osmani-shipping` (pre-launch checklist, rollback plan written
  BEFORE deploy, staged rollout, error-budget gate); `osmani-cicd` — no
  change ships without passing the automated quality gates (lint, types,
  tests, build, audit); `rigor-checkpoint` binary commit gate
  (`verification`); final gate: `definition-of-done` — the task is done only
  when its acceptance criteria AND the standing DoD both hold; mission-close:
  `completion-summary` — the structured close-out (what changed, what was
  confirmed, risks, next step).

## Track 2: Bugfix

Skips DEFINE by explicit determination: the mission and contract already
exist; the bug is a defect against them, not a new objective.
- PLAN: `triage` the complaint (reproduce-first discipline); determine path:
  untested legacy code -> characterization tests first (`osmani-adoption`
  hard rule — no modification of untested legacy code without them).
  `osmani-doubt` on the fix if the root cause is non-obvious (a doubt cycle
  is cheaper than a wrong fix in production).
- BUILD: the prove-it pattern (`osmani-tdd`): failing reproduction test
  BEFORE the fix -> minimal fix -> test passes; `rigor-proof-cycles`.
- VERIFY: full suite, no regressions (`testing`).
- REVIEW: `osmani-code-review` — the fix AND the regression test are
  reviewed together; a bugfix without a regression test is incomplete.
- SHIP: `osmani-shipping`; `osmani-cicd` gates (the same automated checks as
  every other change — no "trivial fix" bypass); `definition-of-done` gate;
  mission-close: `completion-summary`.

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
  (count the concepts a reader must hold); `osmani-adrs` for structural
  decisions (the seam contract you chose and the alternatives you rejected);
  `rigor-scope` touch audit.
- SHIP: `definition-of-done` gate; `osmani-cicd` gates; `rigor-checkpoint`;
  mission-close: `completion-summary`.

## Map vs territory

This document is the map. Each skill's "Routing: phase floor" declaration is
the territory — what the harness actually loads. The mission planner follows
the map; the router enforces the territory. Any disagreement between them
(a skill the map needs but no floor routes, or a floor routing a skill the
map never calls for) is a DEFECT to fix, not a discrepancy to silently
resolve. `tests/test_osmani.py` pins the map's skill references to the
manifest so dangling references fail loudly.

## Deliberately not ported

- `frontend-ui-engineering` — web-frontend craft guidance (components,
  styling, UX patterns); Awino missions are not always web apps, and
  frontend-specific technique would bias generic missions. Left out, not
  mapped.
- `browser-testing-with-devtools` — browser-automation technique for
  driving a live browser; Awino's verification is test/execution-based,
  and browser driving is a tool choice, not a phase discipline. Not ported.
- `performance-optimization` — profiling-then-optimizing is a
  specialization; the gap-check coverage already exists in
  `osmani-code-review`'s performance axis and `osmani-shipping`'s
  performance budgets. Kept out as a standalone skill to avoid a duplicate.
- `code-simplification` — covered by `rigor-entropy` (the entropy-reduction
  pass) plus `osmani-code-review`'s simplicity axis; porting it would be a
  near-duplicate. Left out.
- `deprecation-and-migration` — its portable core (plan deprecation at
  design time, migrate consumers deliberately) is folded into
  `osmani-api-design`'s Hyrum's-Law step rather than kept as a standalone
  skill. Not ported separately.

## Referenced skills (machine-checked: every name must exist in manifest.json)

`discovery` `mission-definition` `osmani-idea-refine` `osmani-constraints` `osmani-adoption` `rigor-laws` `rigor-distillation` `rigor-decomposition` `rigor-scope` `decision-analysis` `osmani-api-design` `osmani-doubt` `domain` `code` `repo` `osmani-tdd` `rigor-proof-cycles` `osmani-security` `osmani-source-driven` `osmani-observability` `rigor-iteration` `testing` `rigor-pentagonal-audit` `rigor-interrogation` `code-review` `rigor-entropy` `osmani-code-review` `osmani-adrs` `osmani-failure-modes` `verification` `rigor-checkpoint` `osmani-shipping` `osmani-cicd` `definition-of-done` `triage` `completion-summary` `incident-response` `dora-metrics`

---
Source: agent-skills (MIT, Addy Osmani)
Adapted for Awino: SUPPORTING REFERENCE, not a routed skill — pinned in the
skill store for fail-closed integrity, never floor-routed into a turn's
context. The source's 16-step slash-command sequence is rewritten as three
task-type tracks over Awino's mission phases (DEFINE/PLAN/BUILD/VERIFY/
REVIEW/SHIP; ACTIVE_PHASES in contract_loop.py are BUILD/VERIFY/REVIEW/SHIP,
with DEFINE/PLAN as the planning phases). Only skill names from Awino's real
inventory are referenced; unported source skills are explicitly excluded
above rather than dangling. Wired by reference: the mission planner follows
this map at DEFINE; `osmani-idea-refine` sharpens interview output into the
spec; `osmani-adoption` determines greenfield vs brownfield; `osmani-doubt`
cross-examines non-trivial decisions in any phase; `osmani-shipping` +
`osmani-cicd` + `definition-of-done` close every track. The map-vs-
territory rule makes disagreements between this document and the per-skill
Routing declarations a defect, not a judgment call.
Harness-skills adoption (Apache-2.0, Harness): `completion-summary` is the
mission-close for every track — no mission is closed without the structured
close-out. `incident-response` is not in any track: incidents PREEMPT the
mission rather than riding inside a phase (injected via skill_add when
declared). `dora-metrics` is consulted at the REVIEW retrospective —
delivery performance is judged on the record, not on impressions.
