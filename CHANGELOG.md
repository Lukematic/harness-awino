# Changelog

## Unreleased — plug-and-play projects (Tracks A–H)

The harness now owns project setup end to end. Open a project (or start a
chat in a fresh directory) and it configures itself — the user never types
setup commands by hand.

### Auto-init (Track A/H)

- `awino chat` runs `session_start_auto_init()` on session start: when
  `.awino/project.yaml` is absent, the full init flow runs automatically and
  prints one brief plain-language summary of what was set up. A 0-byte stub
  `project.yaml` is not trusted (removed, init re-runs).
- `awino init [dir]` remains as the explicit manual override; idempotent,
  adopts existing projects without clobbering.
- `awino status [dir]` — plain-language dashboard: environment checklist
  state, active role mode and why, DAG progress (done/doing/blocked),
  open blockers, last breadcrumb.
- `awino plan [dir]` — the mission task DAG in plain words: what's next,
  what's blocked and by what.
- Startup checklist (ok/warn/fail per item): python version, uv, **venv**
  (existing `.venv` used; created via `uv venv`, else `python3 -m venv`,
  else `python3 -m venv --without-pip` when ensurepip can't reach the
  network; half-created `.venv` moved aside to `.venv.broken-<ts>`,
  never built on top of), **just/make** (existing justfile/Makefile used;
  justfile scaffolded with test/lint/format recipes; `just` installed
  best-effort, breadcrumb recorded when unavailable), ruff
  (best-effort install into the venv), git repo, `.awino/` presence.
- Every `run_command` resolves the project venv executables first
  (`.venv/bin` on POSIX, `.venv/Scripts` on Windows — PATH prefix
  + `VIRTUAL_ENV`), so project commands use the venv python automatically.
- Seeds (`.awino/seeds/*.md` checklists) are parsed and their tasks imported
  into the task tracker on mission start.
- `.awino/project.yaml` is the source of truth: project, mission,
  done_criteria, env (python/venv/linters), docs manifest
  (spec/research/archive), principles (spec-before-code, contract-first,
  evidence-for-every-change), and `profile:` for the role environment.
- Scaffold for missing pieces only: `spec/`, `docs/research/`,
  `docs/archive/`, append-only `lessons.md`, README skeleton.
- All errors are plain-language: what happened, what it means, the one
  next action. Init degrades (breadcrumb + continue), never crashes the
  session.

### Memory registry (Track B)

- `.awino/registry/` auto-created on first mission in a project; no manual
  step. Tracks milestones (decision/completion/date), breadcrumbs (open
  threads, parked questions, exact stop point per mission), and a task
  tracker (open/doing/done/blocked).
- `registry audit` reports everything the registry believes, flagged as
  fact vs assumption vs outdated, plus a plain-language summary.
- Existing `.awino/seeds/*.md` templates keep working; `seed_save` also
  registers tasks.

### Skill security (Track C)

- `docs/SKILL_ADMISSION.md`: the admission checklist — hash/pin
  verification, network+data-needs declaration, adversarial sandbox run
  (injection attempt in input must not be followed or exfiltrated).
- Egress audit: backends expose `last_egress`; the loop journals an
  `egress` event per turn (turn, skill, destination, bytes in/out,
  declared true/false). A skill declaring `network: none` that performs
  egress is flagged `UNDECLARED` in the journal.
- `prototype/skills/network.json`: per-skill network declarations, default
  `none`; the compiled contract surfaces a `## NETWORK` section pre-turn.

### Intelligent role modes (Track D)

- Five role lenses as data (`prototype/skills/mode-*.md`, pinned): 
  **software-engineer** (SOLID, vertical slices, tests-as-proof),
  **ai-researcher** (hypothesis→experiment→evidence, assumption audits,
  citation chains, reproducibility), **ai-architect**
  (constraints→options→decision-matrix→ADR, contract-first interfaces),
  **forward-deployed-engineer** (thin-slice→deploy→iterate, runbooks,
  repro→isolate→fix→verify), **cybersecurity-engineer**
  (asset→threat→mitigation→verify, default-deny, adversarial testing).
- Deterministic router (`prototype/modes.py`): mission text + phase +
  registry context + configured profile → proposed role, recorded in the
  journal with its reason. Re-evaluated at mission start and phase
  boundaries; mid-mission triggers (secrets/security content, experiment
  results) switch the lens immediately. User can override anytime.
- A role mode is a lens, never a license: switching never expands tools
  or permissions — proven by contract-diff tests.

### Role environment profiles (Track E)

- `project.yaml` `profile:` (default `software-engineer`). Bootstrap
  applies per-profile scaffolding, creating only what's missing:
  researcher → `experiments/` + pinned requirements template + run-log
  convention; architect → `docs/adr/`; forward-deployed → `runbooks/` +
  stakeholder notes; security → `docs/security/` + threat-model template.

### Task DAG store (Track F)

- `prototype/registry.py` extends the tracker into a DAG: task ids,
  dependencies, states (open/doing/blocked/done), done criteria, evidence
  links, topological ordering, `whats_next()` (unblocked tasks only),
  blocked propagation. Mission compile generates the initial DAG from the
  mission + active role's decomposition playbook. Resume restores exact
  DAG state from the registry.

### Verification gate (Track G)

- The harness spawns a **separate verifier worker** (read-only, verifier
  stance) at the VERIFY phase; the builder never grades its own work.
- The verdict journaled on the worker answers exactly three questions per
  done criterion: mission goal → what's needed (evidence) → accomplished
  (yes/no + proof link).
- Hard gate in code: VERIFY → REVIEW is refused without a worker-journaled
  pass verdict (a verdict forged in the parent journal is ignored).
- Fail → findings become new DAG tasks and the mission routes back to
  BUILD. The verifier is role-aware: it loads the active role mode's
  Required Evidence checklist.

### Calibration labels (Track H)

- The contract block's `## CALIBRATION` section requires the model to
  label substantive claims `[Certain]` / `[Likely]` / `[Guessing]`;
  unlabeled claims are treated as `[Guessing]`.
