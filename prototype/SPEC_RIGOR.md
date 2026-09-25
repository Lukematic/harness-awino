# SPEC_RIGOR — Awino Rigor Coach

Background engineering-practice auditor ported from
[agent-rigor](https://github.com/MeherBhaskar/agent-rigor) (MIT,
MeherBhaskar; source observed at commit
`393717630c69090dbb43a7233084a2540be0e815`, cloned to
`~/workspace/agent-rigor-src`). Branch: `feature/rigor-coach`.

The coach scores a mission's **journal evidence** against the Five Laws —
never the model's claims. It is read-only except for one journaled event
(`rigor_report`): the report itself becomes evidence.

## 1. Skill-port mapping

Agent Rigor's `core/` modules and skill pack → Awino. Every ported skill
carries Agent Rigor attribution (MIT, MeherBhaskar) in its header.

| Agent Rigor source | Awino skill | Phase routed | Status | Why |
|---|---|---|---|---|
| `skills/distillation` (spec distillation) | `rigor-distillation` | DEFINE | Ported | Spec-before-code discipline at mission definition |
| Five Laws (`SYSTEM_CORE.md` Laws) | `rigor-laws` | DEFINE | Ported | Layer-1 guardrails; adapted (see §2) |
| `skills/interrogation` (deep interview) | `rigor-interrogation` | none (explicit use) | Ported | Deep-dive questioning is opt-in; the discovery grill covers routine turns |
| `skills/decomposition` (task breakdown) | `rigor-decomposition` | PLAN | Ported | Ordered task lists with status |
| `skills/iteration` (convergent loops) | `rigor-iteration` | BUILD | Ported | Converge, don't thrash |
| `skills/checkpoint` (state snapshot) | `rigor-checkpoint` | SHIP | Ported | Known-good snapshot before release |
| Proof cycles (TDD loop, `CHEATSHEET.md`) | `rigor-proof-cycles` | BUILD | Ported | Test-first, evidence-per-change |
| Pentagonal audit (5-axis review) | `rigor-pentagonal-audit` | VERIFY | Ported | Correctness/readability/architecture/security/performance |
| `skills/entropy` (dead-code/side-effect scan) | `rigor-entropy` | REVIEW | Ported | Regression/dead-code/side-effect sweep |
| Doom-loop protocol (`CHEATSHEET.md` 3-strike) | `rigor-three-strike` | circuit-breaker only | Ported | Injected **only** while the breaker is active (§4) |
| Scope containment (Minimal Authority) | `rigor-scope` | PLAN | Ported | Declare scope before touching the tree |
| Experiential consolidation | — | — | **Skipped (covered)** | Awino already persists `learning_recorded → synthesis_admitted`; a second system would be duplication |
| `PLAN.md` / `progress_log.md` / `learned_rules.md` filenames | — | — | **Skipped (function ported)** | Scored as *functions* via journal equivalents (plan_updated, progress_recorded, learning_recorded), never forced filenames |

All 11 rigor skills are SHA-256 pinned in `prototype/skills/manifest.json`
(29 pins total). Tampering any pinned file raises `SkillIntegrityError` at
load — the coach's rubric cannot be silently edited.

## 2. Five Laws — Awino adaptation

Agent Rigor's laws override user requests. In Awino the **user is the top
principal**, so the laws bind the *model's* conduct (harness guardrails),
not the user:

1. **Observable Proof** — completion claims need verifiable evidence.
2. **Atomic State Transitions** — known-good → known-good; broken states
   reverted, never committed.
3. **Preserved Intent** — never change code whose purpose you can't
   articulate.
4. **Declared Uncertainty** — say "I don't know" immediately; guessing is
   the failure.
5. **Minimal Authority** — only the files/scope the task needs.

An explicit user override (approval denied→granted on the same id,
operator resume after stall/escalation) is journaled by the coach as an
**informational finding** — never scored, never a lecture.

## 3. State-machine mapping

Agent Rigor: MISSION SYNTHESIS → EXECUTION → VERIFICATION → PERSISTENCE,
with ROLLBACK & RETHINK as the failure path.

Awino: DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP.

Gap found and fixed: Awino verification failure previously routed straight
back to BUILD (fix-forward). The doom-loop circuit breaker (§4) now adds
the rollback-and-rethink discipline: on the third consecutive identical
failure the loop escalates with `doom_loop_detected`, injects
`rigor-three-strike`, and demands STOP → DIAGNOSE → ROLLBACK → LOG → RETRY
evidence before another attempt. The breaker clears on `turn_validated`.

## 4. Doom-loop circuit breaker (loop.py)

- `_failure_signature(source, parts)`: normalized `source:part|part` for
  `turn_rejected` (error list) and `verify_failed` (unmet criteria).
- `_check_doom_loop`: walks the journal backward; bookkeeping events
  (tokens, tool calls, progress, learnings, doom_loop_detected itself)
  are neutral; `turn_validated`/`verify_passed`/`mission_done` break the
  chain; a different signature starts a new chain. Three consecutive
  identical failures → `doom_loop_detected` event + `doom_loop_active`
  snapshot flag.
- While active, `_sensor_route` appends `rigor-three-strike` to the routed
  skills (the **only** way that skill enters context) and prepends
  STOP/rollback/rethink feedback. `turn_validated` clears the flag.
- `state.py`: `doom_loop_active` snapshot field; `doom_loop_detected`
  handling; cleared on `turn_validated`.

## 5. Layered skill loading

Rigor skills are never bulk-loaded. Routing (`stances.py` FLOORS +
intent fast-paths in `route_triple`):

- DEFINE: `rigor-distillation`, `rigor-laws`
- PLAN: `rigor-decomposition`, `rigor-scope`
- BUILD: `rigor-iteration`, `rigor-proof-cycles`
- VERIFY: `rigor-pentagonal-audit`
- REVIEW: `rigor-entropy`
- SHIP: `rigor-checkpoint`
- `rigor-interrogation`: explicit/deep-interview use only
- `rigor-three-strike`: circuit-breaker injection only
- Conversational intents (opinion/teach/advise/triage) carry no rigor
  layer — they don't enter the mission execution loop. The `ship it`
  fast-path stays `["verification"]` (settled test); the SHIP floor
  default carries `rigor-checkpoint`.

## 6. Scoring signals (prototype/rigor.py)

Each check returns an evidence-linked finding: status ∈
pass (1.0) / partial (0.5) / fail (0.0) / unknown (excluded) / info
(never scored). `error_recovery` uses a continuous 0–1 value (step
fraction) with a bucketed status.

| Check | Weight | Law | Evidence source |
|---|---|---|---|
| observable_proof | 3.0 | L1 | mission_done vs last code change (journal `write_file` ts **and/or** git commit ts via probe) vs last verification ts (`verify_passed`, test-run cmd). PASS only if verification provably postdates the last change |
| atomic_transitions | 2.0 | L2 | journal: `verify_failed` with no later green verification/test before `mission_done` → FAIL; `rollback` → PASS; healed-by-green-test → PASS. With `--repo`: replaced by the git probe — broken-state subject markers (`wip/broken/fixup/tmp/do-not-merge`) or unpushed commits → PARTIAL |
| tests_green | 2.0 | L1 | **latest conclusive** outcome among verify events and test-run exit codes wins; stale `verify_passed` cannot mask a later failed run |
| three_strike | 2.0 | ERP | `doom_loop_detected` events or ≥3-identical-failure clusters; rollback+recovery → PARTIAL; otherwise FAIL |
| error_recovery | 2.0 | ERP | per failure cluster (2+ consecutive same-signature): STOP (breaker/escalation/stall), DIAGNOSE+LOG (learning/progress/assumption recorded), ROLLBACK, RETRY (validated/passed/done) — scored as step fraction |
| minimal_authority | 1.5 | L5 | files written vs declared scope (`contract_approved`/`scope_changed` scope lists); no declared scope → breadth heuristics (>15 FAIL, >8 PARTIAL) |
| regression_test | 1.5 | L1 | bugfix-kind missions only: a test-path file touched → PASS else FAIL; other kinds → UNKNOWN |
| preserved_intent | 1.0 | L3 | ≥half the modified files named in assumption/progress/learning records → PASS; zero rationale → PARTIAL |
| declared_uncertainty | 1.0 | L4 | blocked signals (stalled/escalated/drift/stance_rubric_failed) with zero `questions_asked` → FAIL; else PASS |
| tests_run | 1.0 | L1 | any test command or verify event → PASS; tool calls but none → FAIL; research kind / no tool calls → UNKNOWN |
| lint_evidence | 1.0 | L2 | last lint cmd: exit≠0 → FAIL; ran with no journaled result → PARTIAL; exit 0 → PASS; none → UNKNOWN |
| task_list | 1.0 | file fn | `plan_updated` with tasks or ≥2 real done criteria → PASS; single `manual` checkbox → FAIL |
| learnings | 1.0 | file fn | `learning_recorded`/`synthesis_admitted` → PASS; done with none → PARTIAL; in flight → UNKNOWN |
| decision_log | 0.5 | file fn | journal is append-only by construction; `progress_recorded` notes → PASS; >30 events with none → PARTIAL |

**RigorScore** = Σ(weight × value) / Σ(weights) over scored checks only.
Unknown checks are **excluded** (not treated as passes). Overrides are
informational and never enter the sum.

### Unknown handling (fail-closed, never fail-open)

- No mission_done → proof checks UNKNOWN ("nothing claimed yet").
- No test/verification evidence → tests_green UNKNOWN, not PASS.
- Git probe unavailable / no commits in window → atomic_transitions UNKNOWN.
- Research missions → tests_run UNKNOWN. Non-bugfix → regression UNKNOWN.

## 7. Judge integration (judges.py)

`build_judge_panel("rigor")` = DeterministicJudge + two extra rules from
`rigor.rigor_extra_rules()` (one-way import: judges lazily imports rigor):

- **R3**: `done_claim` with zero verification events and zero test runs
  this session → FAIL (extends R2's unmet-criteria check).
- **R4**: "tests pass"/"verified" language with no tool results and no
  verify events → FAIL (extends R1's completion-language rule).

`loop._judge_summary()` adds `verify_events` and `test_runs` (additive
keys). Panel quorum and fail-closed behavior are unchanged. The full
pentagonal rubric lives in the VERIFY-phase skill and the coach's axis
coverage is journal-recorded; the judge enforces proof discipline at turn
scope, not full 5-axis review (documented limitation, §10).

## 8. Surfaces

- **CLI** (`prototype/cli.py`): `awino rigor --mission <id>`,
  `awino rigor --recent N`, `--repo DIR`, `--json`. Reads
  `<AWINO_HOME>/projects/*/events.jsonl`; defaults to the most recently
  modified project journal. Read-only; does not journal the report.
- **Sidecar** (`prototype/awino_sidecar.py`): `rigor_report` command —
  scores one mission or recent missions, appends exactly one
  `rigor_report` event per mission, returns concise text + structured
  per-check results.
- **VS Code** (`integrations/vscode/extension`): `Awino: Show Rigor
  Report` (`awino.showRigorReport`) → "Awino Rigor" output channel +
  separate rigor status-bar item (latest score; hidden when
  disconnected; never touches the provider/connection status). Prefers
  the latest journaled report; otherwise asks the sidecar to generate
  and journal one. `tsc --noEmit` clean.

## 9. Security / read-only boundaries

- The coach never executes project code. The optional git probe runs only
  `git log` and `git rev-list` (15s timeout, capped output, no pager,
  `GIT_TERMINAL_PROMPT=0`) and degrades to UNKNOWN on any failure.
- The coach's single write is the `rigor_report` journal event.
- User overrides are logged as informational findings, never penalties.
- Skill pins: 29/29 SHA-256 verified at load; tamper → `SkillIntegrityError`.

## 10. Honest limitations

1. The judge's R3/R4 enforce **proof discipline** (Laws 1–2) at turn
   scope, not the full pentagonal rubric — five-axis review needs the
   VERIFY-phase skill + a human or model reviewer; the judge cannot
   review a diff it never sees.
2. `minimal_authority` scope matching is prefix/substring heuristic on
   journal-declared scope lists.
3. `preserved_intent` matches filenames inside rationale text — a stated
   rationale that doesn't name files scores PARTIAL.
4. The git probe orders commits against journal events by timestamp;
   clock skew between the repo and the journal could misorder edge cases
   (PARTIAL is the fail-safe).
5. `error_recovery` credits steps anywhere after a cluster up to the next
   cluster — a later unrelated rollback could credit an earlier cluster.
6. Research missions and non-bugfix missions leave checks UNKNOWN rather
   than forcing inapplicable signals.
7. `rigor-interrogation` and the full interview depth are opt-in; routine
   turns use the discovery grill.
