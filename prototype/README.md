# A.W.I.N.O. loop-owner — Phase 0 prototype

Plain Python, stdlib only. No model API calls anywhere: all model behavior
goes through `ModelBackend` with mock backends (scripted / hostile / echo).

## Layout

- `state.py` — event-sourced per-project state (`<home>/projects/<id>/`:
  `events.jsonl` append-only, `snapshot.json`, `artifacts/`). Crash recovery
  reconciles the tail: unknown effects pause, never blind-replay.
- `contract.py` — TurnContract schema, code-routed mode selection, code-side
  skill loading, done-criteria verification, per-turn contract compiler.
- `tools.py` — sandboxed demo tools (`read_file`, `write_file`, `list_dir`);
  `write_file` is consequential (needs approval). No path escapes the sandbox.
- `backends.py` — `ModelBackend` interface; `ScriptedBackend`, `HostileBackend`
  (skip_plan / forge_done / unoffered_tool / act_on_ambiguity / malformed /
  shift_objective / omit_header / forge_header), `ScriptedJudge`, `EchoBackend`.
- `stances.py` — deterministic stance router (code rules), stance procedures
  loaded into the contract block, deterministic rubrics evaluated per turn.
- `loop.py` — the owned turn loop: inject contract every turn, schema +
  semantic validation, bounded retry then escalate, always-on judge
  (FAIL blocks), tool execution behind mode permission profiles + approval
  gate, event-sourced reduce + persist, budgets (max turns, stall, tokens).
- `chat.py` — REPL. `python chat.py [home]`; `/help` for commands.
  Auto-init runs on session start: in a directory without
  `.awino/project.yaml`, the harness sets the project up itself and
  prints a brief summary before the mission proceeds.
- `cli.py` — `awino init|status|plan|stories [dir]`: one-command project
  setup (idempotent), a plain-language project dashboard, the task DAG in
  plain words, and the story ledger in plain words.
- `story.py` — the STORY.md story ledger: Jira-shaped stories (story |
  spike | chore; open | doing | blocked | done) with problem, approach
  (the spine, written during planning), done criteria, blockers, linked
  seeds, branch, session log (with start/end timestamps — wall time
  accumulates as "time dedicated"), closed date + outcome. The registry
  (`.awino/registry/stories.json`) is the single source of truth;
  `STORY.md` at the project root is RENDERED from it on every mutation
  (generated-file header; hand-edits are overwritten): open sections
  newest-on-top, done stories as the Brag board at the bottom with
  closed date, outcome, and time dedicated. Session start mandatorily
  presents open stories; `request_phase` refuses ->BUILD when the doing
  story's spine (problem/approach/done criteria) is empty; a passing
  verifier verdict journals `story_ready_to_close` and ASKS the user to
  close (close authority stays human). Stale-story rule: a new mission
  or `story_start` while stories are doing/open journals
  `stale_stories`. Every 25 turns (`STORIES_NUDGE_EVERY`), untouched
  open stories journal `stories_nudge`. `plan_story` writes the approach
  with a required six-part shape — (a) breakdown, (b) existing
  approaches, (c) user guidance, (d) A/B/C + Bugatti-brief pitch (Honda
  first), (e) ordered steps with success/failure criteria that seed the
  task DAG, (f) Bugatti proposal — and cycles the PLAN-affine stances
  (architect -> researcher -> engineer), journaling each contribution;
  the user can override stances. Brief-first: the Bugatti expands only
  on request (`expand_bugatti`); IRON RULE — pitched, never built
  unasked. Parked ideas resurface on their revisit date and convert to
  spikes. Planning DNA: `docs/STORY_PLANNING.md`. Git lifecycle
  (`docs/STORY_GIT_RULES.md`): branch per issue, commit early/often
  locally, best-effort push + PR on close, offline-safe, never
  published.
- `bootstrap.py` — Track A/H: startup checklist (python, uv, venv,
  just/make, ruff, git, `.awino/`), venv creation/adoption, justfile
  scaffold, seed-checklist parsing, `.awino/project.yaml` source of
  truth, project scaffolding, per-profile environment folders.
- `registry.py` — Tracks B/F: auto-created `.awino/registry/` with
  milestones, breadcrumbs, and a task DAG (dependencies, states, done
  criteria, evidence links, topological order, what's-next, blocked
  propagation, self-audit).
- `modes.py` — Track D: five role lenses (software-engineer,
  ai-researcher, ai-architect, forward-deployed-engineer,
  cybersecurity-engineer) + the deterministic router that proposes the
  lens from mission text, phase, registry context, and profile.
  Switching is journaled with its reason and never expands permissions.
- `verify.py` — Track G: the verifier's verdict computation —
  goal → needed evidence → accomplished (yes/no + proof) per criterion,
  role-aware required-evidence checklists.
- `demo.py` — scripted two-project human+AI session. `python demo.py`.
- `tests/` — adversarial suite (spec §7) plus plug-and-play, registry,
  DAG, modes, profiles, verification-gate, skill-security, and
  battle-hardening tests.

## Run

```bash
cd ~/workspace/awino-rebuild/prototype
./run_tests.sh        # full adversarial suite
python demo.py        # scripted two-project session
python chat.py        # interactive REPL (EchoBackend mock)
```

## Enforcement claims (what Phase 0 proves)

With mock backends, the suite proves the *mechanism*, substrate-independently:

- the contract block is injected on every model call (asserted on backend calls)
- schema + semantic validation reject hostile turns; retries are bounded, then
  the operator is escalated — the mission is never completed by a bad turn
- `done_claim=true` with unverified criteria NEVER terminates the loop
  (forgery is rejected; only code-verified criteria complete a mission)
- the judge runs on every validated turn and FAIL always blocks the turn
- consequential tools need a human approval bound to the plan revision;
  stale approvals are rejected; denials execute nothing
- crash mid-effect reconciles without duplicate writes or lost approvals
- two projects stay isolated; missions persist across restarts
- **turn header contract**: the harness renders
  `[A.W.I.N.O. | phase: <FLOOR> | stance: <STANCE> | mission: <id> | run: <id>]`
  as the first line of every turn from code-owned state; the validator asserts
  the turn echoes it back verbatim — omitted or falsified headers reject the turn
- **elevator gate (named)**: PLAN → BUILD requires an approved contract
  (`/approve-contract`); the transition is a code check, refused otherwise —
  a write can execute (with tool approval) while the elevator stays in PLAN
- **deterministic stance triggers**: input patterns route stances in code
  ("i think|we should" → steel-man; "how does|teach me|learn" → feynman;
  new objective / raw idea → planning grill); firing loads the procedure into
  the contract block and rubric-evaluates the output — keyword-only fake
  opposition fails the rubric and the turn is rejected
- **next-action line**: every contract block ends with
  `Floor: | Next action: | Blocked on:` compiled from live state
