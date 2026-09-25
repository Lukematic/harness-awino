# Layered Loading (reference: how the harness loads knowledge)

One principle: **every skill must earn its load, every turn.** The harness
compiles a turn contract from four layers. Prompt text is the most expensive
way to enforce a rule, so a rule that tooling can check is never left as
prompt text.

## The four layers

**Layer 1 — Core.** The rules required every turn: mission, done-criteria
status, phase, mode, scope, constraints, autonomy, verification, stop
condition, calibration, output schema, and the precedence rule below. Hard
budget: **under 600 words**, enforced by `tests/test_layered_loading.py`.
The core is injected once per turn by `compile_contract()`; nothing else
joins it. Core never names a skill — it names the mechanism (the floor
binding, the exit gate, the journal).

**Layer 2 — Ceremony.** Skills and commands loaded only when their
phase/intent requires them. Phase floors (`stances.FLOORS`), intent routes
(`stances.INTENT_TABLE`), role lenses (`modes.ROLE_SKILL_NAMES`), and
explicit `skill_add` requests (`rigor-interrogation`, `incident-response`)
are the only ways in. A ceremony skill is judgment the model must exercise
in the moment — it cannot be checked by code, so it arrives as text, exactly
when needed, and never before.

**Layer 3 — Mechanical.** Rules enforced by code, not prompts. Wherever the
corpus states a rule a deterministic check can verify, the check lives in
code and the prompt text defers to it. Current gates:

- Turn header: exact echo enforced by the header validator (`contract.py`).
- Scope: file tools confined to the sandbox; writes outside approved scope
  refused (`tools.py`, `loop.py`).
- Autonomy/mode: consequential tools need human approval; budgets are hard
  limits (`contract.py`, `loop.py`).
- Done criteria: forged completion claims refused; only harness-computed
  status counts (`contract.py`, `verify.py`).
- Skill integrity: SHA-256 pins verified at load (`skills/__init__.py`).
- VERIFY -> REVIEW: a journaled verifier-worker pass verdict is required
  (Track G, `loop.py`).
- REVIEW -> SHIP: the constraints floor — no new suppression comments, no
  stubs or empty catch blocks, no unexplained skipped/deleted tests, no
  secrets in the diff (`floor_checks.py`, `loop.py`). This mechanizes the
  FLOOR of `osmani-constraints`, which describes its own escalation:
  written first, then scripted, then tool-backed.
- Commit gate: binary checkpoint at SHIP (`rigor-checkpoint`, `verification`).

A mechanical gate fails closed: on violation the transition is refused with
the findings named, and the refusal is journaled. Gates never weaken the
record to make a change pass.

**Layer 4 — Reference.** Full supporting documentation: pinned by SHA-256,
integrity-checked at load, but **never automatically injected**. Available
on explicit request (`skill_add`, or the operator opening the file). The
map (`lifecycle-sequence`), the standing Definition of Done
(`definition-of-done`), the close-out template (`completion-summary`),
`critical-thinking`, `dora-metrics`, the verifier worker's full procedure
(`verify`), and this document.

## Precedence

When rules conflict, in order: **safety/irreversibility → the operator's
explicit instructions this session → the approved spec → process rules →
style.** The harness states this once in the core; it is never re-argued.

## The audit table

Every pinned entry, exactly one layer. "Route" is how a ceremony skill
reaches a turn; reference entries have no route (that is the point).

| Skill | Layer | Route / note |
|---|---|---|
| code | ceremony | BUILD floor |
| code-review | ceremony | REVIEW floor — the thin checklist; `osmani-code-review` is the deep review. Both kept: speed vs depth. |
| completion-summary | reference | mission-close template, pulled on demand |
| critical-thinking | reference | standing reference |
| decision-analysis | ceremony | PLAN floor + advise intent |
| definition-of-done | reference | the standing bar; cited by gates, never injected |
| discovery | ceremony | DEFINE floor + new-task intent |
| domain | ceremony | PLAN floor + opinion/teach/advise/triage intents |
| dora-metrics | reference | SHIP-side measurement reference |
| explainer | ceremony | teach intent |
| incident-response | ceremony | on-demand via explicit skill_add (preemptive, no phase floor) |
| layered-loading | reference | this document |
| lifecycle-sequence | reference | the map; the planner consults it, the router enforces the territory |
| mission-definition | ceremony | DEFINE floor + new-task intent |
| mode-ai-architect | ceremony | role lens, loaded only when the role is active |
| mode-ai-researcher | ceremony | role lens |
| mode-cybersecurity-engineer | ceremony | role lens |
| mode-forward-deployed-engineer | ceremony | role lens |
| mode-software-engineer | ceremony | role lens |
| osmani-adoption | ceremony | DEFINE floor |
| osmani-adrs | ceremony | REVIEW floor |
| osmani-api-design | ceremony | PLAN floor |
| osmani-cicd | ceremony | SHIP floor |
| osmani-code-review | ceremony | REVIEW floor — the five-axis procedure; pairs with the thin `code-review` checklist |
| osmani-constraints | ceremony | PLAN floor — decides the bar; the FLOOR section is mechanized by `floor_checks.py` (see below) |
| osmani-doubt | ceremony | PLAN floor |
| osmani-failure-modes | ceremony | PLAN floor |
| osmani-idea-refine | ceremony | DEFINE floor |
| osmani-observability | ceremony | BUILD floor |
| osmani-security | ceremony | BUILD + REVIEW floors |
| osmani-shipping | ceremony | SHIP floor |
| osmani-source-driven | ceremony | BUILD floor |
| osmani-tdd | ceremony | BUILD floor — TDD discipline; pairs with VERIFY-floor `testing` (run the suite) and `rigor-proof-cycles` (the loop rhythm) |
| project-bootstrap | ceremony | on-demand, **currently unwired** — no phase, intent, or command routes it. Flagged: wire it to the bootstrap command or move it to reference. |
| repo | ceremony | BUILD floor + fix intent |
| rigor-checkpoint | ceremony | SHIP floor |
| rigor-decomposition | ceremony | PLAN floor |
| rigor-distillation | ceremony | DEFINE floor + new-task intent |
| rigor-entropy | ceremony | REVIEW floor |
| rigor-interrogation | ceremony | on-demand via explicit skill_add (deep verification) |
| rigor-iteration | ceremony | BUILD floor + fix intent |
| rigor-laws | ceremony | DEFINE floor + new-task intent |
| rigor-pentagonal-audit | ceremony | VERIFY floor |
| rigor-proof-cycles | ceremony | BUILD floor + fix intent |
| rigor-scope | ceremony | PLAN floor |
| rigor-three-strike | ceremony | circuit-breaker injection only — never phase- or intent-routed |
| testing | ceremony | VERIFY floor — "run the suite, exit code is the verdict"; the thin runner to `osmani-tdd`'s discipline |
| triage | ceremony | triage intent |
| verification | ceremony | SHIP floor — the ship-gate checklist |
| verify | reference | the verifier worker's full procedure. The worker's *behavior* is mechanical (Track G gate + `verify.py`); this document is the human-readable reference for it. Unrouted by design. |

User-admitted registries (via `skill_add`) ship no `layers.json`: a skill
that enters only through explicit, screened admission is already on the
ceremony on-demand path, so it defaults to `ceremony`/`ondemand` instead of
failing closed. Reference-ness is a property of the built-in corpus, never
of user skills.

## What moved into the mechanical layer
These rules existed as prompt text and are now enforced by code:

1. **The constraints floor** (`osmani-constraints` §3 FLOOR) → `floor_checks.py`,
   gated at REVIEW -> SHIP. The skill itself demands this escalation
   ("written-only first, then scripted"): no new suppression comments
   (`@ts-ignore`, `eslint-disable`, `# noqa`, `type: ignore`, …), no stubs
   or empty catch blocks (`NotImplementedError`, `...`, bare `pass` in
   `except`, TODO/FIXME left in new code), no skipped/deleted tests without
   a reason, no secrets in new code. Each finding names the file and line.
2. **The core word budget** → asserted by test every run (under 600 words).
3. **Layer placement** → `skills/layers.json` + `layer_of()`; the test suite
   asserts every manifest entry has exactly one layer and that reference
   entries are never floor-routed.

Deliberately NOT mechanized: `osmani-constraints` "ENFORCED WITH NUMBERS"
(coverage ≥ 80%, lint/type zero errors) — those are per-project commands
that belong in the project's CI, not the harness. The harness mechanizes
only what is project-independent.

## Deliberately duplicated (not moved, not merged)

- `code-review` (28 words) vs `osmani-code-review` (540): the thin checklist
  runs inside REVIEW on every change; the five-axis procedure runs when the
  diff warrants depth. Speed vs depth is a real distinction.
- `testing` vs `osmani-tdd`: the VERIFY floor needs "run the suite, trust
  the exit code"; the BUILD floor needs TDD discipline. Different phases,
  different jobs.
- `verification` (SHIP checklist) vs `verify` (worker procedure): the gate
  vs the manual. The gate is ceremony at SHIP; the manual is reference.
- `rigor-proof-cycles` vs `osmani-tdd`: the loop rhythm vs the discipline;
  they compose (documented in both).

## Attribution

Adapted from Part 0 of `vscarpenter/claude-code-build-system`
`standards/coding-standards.md` (MIT license): the four-layer runtime
(critical/user > conditionally loaded > mechanically enforced > reference),
the sub-600-word core budget, the "every skill must earn its load" rule,
and the precedence chain. Awino adaptations: the core is the compiled turn
contract rather than a static prompt file; ceremony routes through the
existing phase/intent/role machinery instead of slash commands; the
mechanical layer reuses the harness's existing fail-closed gates (scope,
header, Track G) and adds the constraints-floor diff check the corpus
already demanded; reference entries stay SHA-256 pinned because the
harness's integrity model requires it. The trivial tier and the boundary
rule live in `lifecycle-sequence`, which the source system does not have.

Layered loading: the harness routes this skill only as noted below. Never bulk-load all skills into one turn's context.
Routing: reference (on-demand only).
