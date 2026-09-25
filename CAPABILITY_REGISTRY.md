# A.W.I.N.O. Capability Registry

Living inventory of every mode, stance, and skill. Check here before adding
anything — duplicates are how whack-a-mole starts. Authoring contract:
`AUTHORING_TEMPLATE.md`.

Rule of the house: **a mode is a permission profile, a stance is a reasoning
procedure with a rubric, a skill is injected knowledge.** If it needs no
rubric, it's a skill, not a stance. If it changes no tool permissions, it's
not a mode.

## Modes (5) — prototype `contract.py::MODES`

| Mode | Tools granted | Consequential | Routed when |
|---|---|---|---|
| observe | reads + search/symbols/git/diagnostics + set_mission, story_plan, stretch_goal | — | teach intent (Feynman); IDLE default |
| plan | same as observe | — | opinion / challenge / decide / advise / triage / new-task intents; DEFINE+PLAN floors |
| build | + write_file, patch_file | write_file + patch_file (need approval; SCOPE-bounded) | fix intent **with a mission** (without one: the interview); BUILD floor (only after /approve-contract + SCOPE) |
| verify | + run_command (sidecar: approval-gated) | — | VERIFY/REVIEW floors (no write tool offered: tests can't be edited to force a pass); a failing run routes back to BUILD |
| ship | reads + stretch_goal | — | ship intent; SHIP floor |

Harness tools in every mode: `task_add`, `task_update` (plan tasks need an evidence file to be done), `attempt_completion`. Workers never get `set_mission`, `story_plan` or `stretch_goal`.

The permission gate computes the offered set from the mode alone, before the
model acts. Stances never widen it.

## Stances (9) — prototype `stances.py::STANCES` + `RUBRICS`

Since 0.7 the model may declare the stance (`stance` + `stance_why`) within
`stances.PHASE_STANCES[phase]`; the phase floor (`PHASE_FLOOR`) rubric always
runs too. The triggers below are the fallback when nothing is declared.

| Stance | Trigger | Mode affinity | Tool discipline | Rubric checks |
|---|---|---|---|---|
| steel-man | "i think / we should / my idea / what about / is this a good idea / challenge me / should we / X vs Y" | plan | reads only (mode) | fair restatement (≥2 shared terms); substantive counter-case in assumptions |
| feynman | "teach me / how does / learn" | observe | **no tool calls at all** | analogy → example → snapshot in order; one gap question |
| planning-grill | new task / raw idea | plan | no acting while questions open | exactly one question per turn; ask XOR advance (PLAN_RUSH fails); no tool calls in a question turn |
| first-principles | "fix / bug / patch" | build | scoped writes only | non-empty derived plan; hypothesized cause in assumptions |
| premortem | "ship it" / advise chain | ship/plan | reads only (mode) | failure scenarios named; plan survives them |
| devil's-advocate | VERIFY floor default | verify | test commands only | substantive attack on results in assumptions |
| advisor | chain wrapper (no procedure) | — | — | none (delegates to chain) |
| triage | vague agent complaint ("you're not working", "misbehaving") | plan | read-only; diagnosis, not repair | named failure mode; falsifier stated |
| verifier | Track G — independent verification of builder work | verify | **no tool calls at all** (read-only judge) | per-criterion verdict shape (criterion / needed_evidence / accomplished / proof_link); never grades own builder work |

## Skills (53) — prototype `contract.py::SKILLS` (harness-injected bodies)

Pinned by sha256 in `prototype/skills/manifest.json` and verified at load by
`prototype/skills.py::SkillStore`. The model never fetches them; the router
injects full bodies into the contract block.

**Core loop skills (11):** mission-definition, discovery, decision-analysis,
domain, repo, code, testing, code-review, verification, explainer, triage.

**Osmani port (14):** osmani-adoption, osmani-constraints, osmani-failure-modes,
osmani-tdd, osmani-security, osmani-code-review, osmani-shipping, osmani-doubt,
osmani-api-design, osmani-source-driven, osmani-idea-refine, osmani-adrs,
osmani-observability, osmani-cicd. Adapted from Addy Osmani's `agent-skills`
(MIT); each carries a binding mechanism (not advice text).

**Rigor coach (11):** rigor-checkpoint, rigor-decomposition, rigor-distillation,
rigor-entropy, rigor-interrogation, rigor-iteration, rigor-laws,
rigor-pentagonal-audit, rigor-proof-cycles, rigor-scope, rigor-three-strike.

**Agent persona skills (5):** mode-ai-architect, mode-ai-researcher,
mode-cybersecurity-engineer, mode-forward-deployed-engineer,
mode-software-engineer.

**Pinned references (5):** definition-of-done, lifecycle-sequence,
critical-thinking, completion-summary, dora-metrics.

**Harness adoption (1):** incident-response.

**Bootstrap (1):** project-bootstrap — new-project scaffolding
(loop-owner counterpart of the old `awino-bootstrap` repo skill).

**Contract helper (1):** verify.

**Memory (1):** durable-memory — MemPalace cherry-pick: local-first
JSONL store (`.awino/memory.jsonl`), chunked entries, content-hash dedup,
offline keyword search. Routed on DEFINE + the new-task path (recall before
planning); the body teaches persist-at-closure. No compression (measured
2.7x with fidelity loss, not 30x), no daemon, no vector search.

**Debug + multi-file workflows (2):** debug — reproduce→diagnose→fix→verify
procedure with checklist gates (reproduction evidence before diagnosis,
ROOT CAUSE statement format, verification criteria); rpi — repeatable
multi-file implementation workflow in loop-owner form (file set named in
SCOPE, sequenced edits, verify each file, integrate; works through the
contract/mission machinery, never around it).

## Intent table — prototype `stances.py::INTENT_TABLE` (first match wins)

opinion → plan/steel-man; teach → observe/feynman; advise →
plan/steel-man→premortem; **triage → plan/triage** (before fix: vague
complaints must not fall through to the fixer); fix → build/first-principles;
ship → ship/premortem; new-task → plan/planning-grill; else floor default.

## The contract loop — prototype `contract_loop.py` (enforcement, not capability)

Two nested loops (BUILD_SPEC §4). The outer loop is the mission elevator
(DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP; phase gates in `loop.py`).
The inner loop is per-turn compile → validate → execute: every turn compiles
the objective→mission→tools→progress contract from code-owned state, checks
it before the model acts (pre-turn) and re-checks the proposed turn before
anything executes (pre-execute), and refuses broken contracts with NAMED
reasons — never silently. Refusals are `contract_refused` events
(stage + named breaks); the turn does not proceed and no tool executes.

| Break reason | Detected | Refusal |
|---|---|---|
| MODE_UNKNOWN | pre-turn | turn refused; backend never called |
| NO_PLAN | pre-turn | turn refused; backend never called |
| CONTRACT_STALE | pre-turn | turn refused; backend never called |
| SCOPE_INVALIDATED | pre-turn (BUILD w/o approved SCOPE) / pre-execute (write outside SCOPE) | turn refused; no execution |
| TOOL_NOT_GRANTED | pre-execute | turn refused; no execution |
| WRITE_WITHOUT_APPROVAL | pre-execute | execution refused; routed to the approval gate |
| COMPLETION_WITHOUT_EVIDENCE | pre-execute | turn refused; no execution |

Status: **done (2026-09-18)** — `tests/test_contract_loop.py` (12 tests),
suite 113/113 green ×3.

## Trust boundaries (2026-09-25 honest-gaps program)

- **`patch_file`** (`prototype/tools.py`): atomic unified-diff application
  (temp file + `os.replace`), fail-closed with named refusal codes
  (`BAD_HUNK_HEADER`, `BODY_COUNT_MISMATCH`, `CONTEXT_MISMATCH`,
  `AMBIGUOUS_MATCH`, `FUZZY_OFFSET`, `PATCH_TARGET_MISSING`, `EMPTY_PATCH`,
  `MULTI_FILE_PATCH`, `UNSUPPORTED_DIFF`). Zero fuzz, zero guessing.
  Build-mode only, consequential like `write_file`; applies and refusals
  journaled as `patch_applied` / `patch_refused`.
- **Secret redaction** (`prototype/secret_redaction.py`): high-confidence
  credential shapes (AWS `AKIA…` keys, `sk-`/`sk-or-v1-`/`sk-ant-` keys,
  `ghp_`/`gho_`/`github_pat_` tokens, `Bearer` tokens, `xoxb-`/`xoxp-`
  tokens, high-entropy `api_key`/`secret`/`token`/`password` assignments)
  are redacted at the journaling boundary (`ProjectState.record()` covers
  `events.jsonl`, in-memory events, journal exports; `persist_snapshot()`
  covers `snapshot.json`) and on the sidecar's log output path (`_emit()`
  stdout, `_RedactingStderr` wrapper). General PII scrubbing of arbitrary
  free text is deliberately NOT attempted (arms race). Follow-up:
  `prototype/awino_mcp.py`'s stderr/diagnostic path is not wrapped yet.
- **Approval-target visibility** (`prototype/approval_targets.py`):
  shell approval cards show the command's cwd-resolved absolute file
  targets and flag anything outside the workspace
  (`outside_workspace: true`). Visibility, not prohibition — there is no
  dangerous-command blacklist; the user stays the authority. Expansion,
  globs, pipes, and unknown command shapes are marked UNRESOLVED rather
  than silently skipped.
- **Fan-out routing** (`prototype/loop.py::fanout`): a fan-out request can
  carry a code-owned routing map (`model_routes`: name → backend) and each
  subtask may name its backend; unknown names raise `UnknownBackendError`
  ("fanout refused") in pre-flight, before any worker spawns. Tournament
  mode and loop-until-done remain unimplemented roadmap items.
- **Bedrock AWS profile/SSO** (`prototype/awino_sidecar.py`): stdlib-only
  SigV4 signing from the standard AWS credential chain (env →
  `~/.aws/credentials` → `~/.aws/config` with `source_profile` chaining →
  SSO cache via `GetRoleCredentials` — the SSO token is exchanged, never
  used directly as a signing key). Fail-closed with named errors
  (`AWS_SSO_TOKEN_EXPIRED`, `AWS_PROFILE_NOT_FOUND`,
  `AWS_CREDENTIALS_NOT_FOUND`, …); never sends unsigned requests.
  Honest limit: signatures are cross-validated against botocore in tests,
  but the first LIVE Bedrock call from this path is still untested here
  (no AWS credentials in this environment).

## The old world: 16 repo skills (`~/workspace/awino-recovery/skills/`)

These are SKILL.md files the model must voluntarily fetch — the advisory
model this rebuild replaces. Mapping to the loop-owner:

| Repo skill | Loop-owner counterpart | Status |
|---|---|---|
| awino-discover | discovery skill + planning-grill stance | **done (ported via template, 2026-09-18)** — full interview procedure (detect-before-ask, frontier mission→user→goals→tenets→expectations→metric, diverge/converge, adaptive grill, confirm intent, 7 failure modes) in the discovery skill body; new-task routes mission-definition+discovery; grill enforces one-question-at-a-time and ask-XOR-advance |
| awino-debug | fix intent + first-principles + `debug` skill | **done (2026-09-25)** — reproduce→diagnose→fix→verify procedure with checklist gates (reproduction evidence before diagnosis, ROOT CAUSE statement format, verification criteria); routed on fix intent |
| awino-consult | advisor chain | partial |
| awino-evidence | verification skill + VERIFY floor | partial |
| awino-triage | triage stance + skill | **done (authored via template, 2026-09-18)** |
| awino-rpi | `rpi` skill (loop-owner multi-file workflow) | **done (2026-09-25)** — file set named in SCOPE, sequenced edits, verify each file, integrate; works through the contract/mission machinery, never around it |
| awino-delegate | `Loop.fanout` — parallel workers, atomic overlap/budget preflight, fail-closed synthesis barrier | **done (merged)** |
| awino-ralph | the harness loop itself | superseded |
| awino-memory | durable-memory skill + `prototype/memory_store.py` (MemPalace cherry-pick: local-first JSONL, chunking, content-hash dedup) | **done (2026-09-25)** |
| awino-visualize | — | gap |
| awino-reproducibility | — | gap |
| awino-self-update | — | gap |
| awino-bootstrap | project-bootstrap skill | **done** |
| awino-config-review | — | gap |
| awino-author-agent | — | gap |
| awino-author-tool | this template | superseded |

## Gaps (prioritized)

1. **~~discover is thin~~ resolved 2026-09-18** — the discovery skill now carries
   the full interview procedure and the grill enforces it (see awino-discover
   row above).
2. **~~debug procedure~~ resolved 2026-09-25** — the `debug` skill carries the
   reproduce→diagnose→fix→verify procedure and is routed on fix intent.
3. **~~rpi~~ resolved 2026-09-25** — the `rpi` skill is the loop-owner
   multi-file change workflow.
   (Delegate is done: the fan-out primitive merged — `Loop.fanout` with
   atomic overlap/budget preflight and a fail-closed synthesis barrier.)
4. **~~memory~~ resolved 2026-09-25** — MemPalace head-to-head verdict:
   cherry-pick, not wholesale adoption. `prototype/memory_store.py`
   implements the three winners (local-first JSONL indexing, word-boundary
   chunking with byte-identical reassembly, content-hash dedup) as
   store/recall/search, and the `durable-memory` skill (routed on DEFINE and
   the new-task path) teaches recall-before-planning and persist-at-closure.
   Compression deliberately not built.

## Standards (merged)

- **Adapter contract** (`docs/ADAPTER_CONTRACT.md`): the provider-neutral
  controller protocol every surface must honor; seven adapter requirements;
  Context → Interactive → Autonomous local → Hosted conformance ladder.
  One enforced loop in the Python sidecar; surfaces are adapters.

When you fill a gap, follow `AUTHORING_TEMPLATE.md` and add the row here.
