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
| observe | read_file, list_dir | — | teach intent (Feynman) |
| plan | read_file, list_dir | — | opinion / advise / triage intents; DEFINE+PLAN floors |
| build | + write_file | write_file (needs approval; SCOPE-bounded) | fix intent; BUILD floor (only after /approve-contract + SCOPE) |
| verify | + run_command | — | VERIFY/REVIEW floors (no write tool offered: tests can't be edited to force a pass) |
| ship | read_file, list_dir | — | ship intent; SHIP floor |

The permission gate computes the offered set from the mode alone, before the
model acts. Stances never widen it.

## Stances (8) — prototype `stances.py::STANCES` + `RUBRICS`

| Stance | Trigger | Mode affinity | Tool discipline | Rubric checks |
|---|---|---|---|---|
| steel-man | "i think / we should" | plan | reads only (mode) | fair restatement (≥2 shared terms); substantive counter-case in assumptions |
| feynman | "teach me / how does / learn" | observe | **no tool calls at all** | analogy → example → snapshot in order; one gap question |
| planning-grill | new task / raw idea | plan | no acting while questions open | exactly one question per turn; ask XOR advance (PLAN_RUSH fails); no tool calls in a question turn |
| first-principles | "fix / bug / patch" | build | scoped writes only | non-empty derived plan; hypothesized cause in assumptions |
| premortem | "ship it" / advise chain | ship/plan | reads only (mode) | failure scenarios named; plan survives them |
| devil's-advocate | VERIFY floor default | verify | test commands only | substantive attack on results in assumptions |
| advisor | chain wrapper (no procedure) | — | — | none (delegates to chain) |
| triage | vague agent complaint ("you're not working", "misbehaving") | plan | read-only; diagnosis, not repair | named failure mode; falsifier stated |

## Skills (11) — prototype `contract.py::SKILLS` (harness-injected bodies)

mission-definition, discovery, decision-analysis, domain, repo, code,
testing, code-review, verification, explainer, triage.

These are injected into the contract block by the router. The model never
fetches them. (Production: load bodies from files with sha256 — spec Phase C.)

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

## The old world: 16 repo skills (`~/workspace/awino-recovery/skills/`)

These are SKILL.md files the model must voluntarily fetch — the advisory
model this rebuild replaces. Mapping to the loop-owner:

| Repo skill | Loop-owner counterpart | Status |
|---|---|---|
| awino-discover | discovery skill + planning-grill stance | **done (ported via template, 2026-09-18)** — full interview procedure (detect-before-ask, frontier mission→user→goals→tenets→expectations→metric, diverge/converge, adaptive grill, confirm intent, 7 failure modes) in the discovery skill body; new-task routes mission-definition+discovery; grill enforces one-question-at-a-time and ask-XOR-advance |
| awino-debug | fix intent + first-principles | partial (no debug procedure yet) |
| awino-consult | advisor chain | partial |
| awino-evidence | verification skill + VERIFY floor | partial |
| awino-triage | triage stance + skill | **done (authored via template, 2026-09-18)** |
| awino-rpi | — | gap |
| awino-delegate | — | gap |
| awino-ralph | the harness loop itself | superseded |
| awino-memory | — | gap |
| awino-visualize | — | gap |
| awino-reproducibility | — | gap |
| awino-self-update | — | gap |
| awino-bootstrap | — | gap |
| awino-config-review | — | gap |
| awino-author-agent | — | gap |
| awino-author-tool | this template | superseded |

## Gaps (prioritized)

1. **~~discover is thin~~ resolved 2026-09-18** — the discovery skill now carries
   the full interview procedure and the grill enforces it (see awino-discover
   row above).
2. **debug procedure** — fix intent routes first-principles (cause in
   assumptions) but there's no reproduce→diagnose→fix procedure body.
3. **rpi / delegate** — multi-file change workflow and parallel subagent
   decomposition have no loop-owner form yet.
4. **memory** — no durable-memory skill in the harness; currently handled
   outside the loop.

When you fill a gap, follow `AUTHORING_TEMPLATE.md` and add the row here.
