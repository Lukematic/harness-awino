# A.W.I.N.O. Loop-Owner — Architecture (as built)

**Status:** Built and proven. This document describes the system on `main`.
It supersedes the 2026-09-18 draft (which proposed a pydantic-ai substrate —
the build went stdlib-only instead; no external runtime dependency).

## 1. The decision

A.W.I.N.O. is the `while` loop that owns the conversation. The per-turn
contract (objective → mission → tools/skills → progress) is compiled from
code-owned state on every turn, validated before the model acts and again
before anything executes. The model produces a typed turn object; everything
else is code the model cannot skip.

## 2. Non-negotiable principles

1. **The harness runs the loop.** Whoever runs the loop owns the conversation.
2. **State lives in code, not in context.** Objective, mission, progress, mode — persisted in code-owned state, re-injected fresh every turn.
3. **Every turn emits a typed contract object.** `TurnContract`: objective, plan, tool calls, progress delta, questions, done claim. Invalid output is retried or rerouted — never silently accepted.
4. **The judge is code-scheduled.** A panel of N judges votes by quorum on every turn because the loop says so, not because the worker asks. No single gatekeeper; fail-closed.
5. **Enforcement happens at boundaries the model cannot argue with:** the model-call boundary (pre-turn), the output boundary (schema + semantic validation), the tool boundary (permissions, sandbox, approvals), the pre-execute boundary (contract re-check before any tool runs).
6. **Model-agnostic by construction.** The contract is code; models are interchangeable backends. Swapping models changes nothing about enforcement.
7. **Proof, not claims.** Every enforcement point ships with adversarial tests that try to defeat it. Done means the hostile model cannot complete a mission without satisfying the contract.

## 3. The loop

```text
state = load_or_init(conversation_id)          # code-owned, persisted, event-sourced

while not state.done:
    # --- PRE-TURN: contract compilation (code, not prompt) ---
    contract = compile_contract(state)         # objective, mission, tools/skills,
                                               # mode, open questions, progress

    # --- ROUTING: mode x stance x skill (code, not model choice) ---
    mode   = route_mode(contract)               # permission profile
    stance = route_stance(user_input, floor)    # reasoning procedure + rubric
    skills = route_skills(contract)             # injected bodies, SHA-256 pinned

    # --- MODEL CALL: contract injected fresh, output typed ---
    turn = backend.call(system=base + render(contract), messages=history,
                        output_schema=TurnContract)

    # --- PRE-EXECUTE: validation (code) ---
    verdict = validate(turn, contract, state)   # schema + semantics
    if verdict == REJECT:                       # named break reason, journaled
        turn = retry_or_escalate(verdict)       # bounded retries, then operator

    # --- JUDGE: always-on, code-scheduled ---
    if judge_panel(turn, contract, state) == FAIL:
        continue                                # worker does not proceed

    # --- TOOL BOUNDARY: permissions enforced where tools run ---
    results = execute(turn.tool_calls,
                      permissions=mode.permissions,
                      approvals=approval_gate)   # human gate on consequential actions

    # --- STATE: externalized, persisted ---
    state = reduce(state, turn, results)        # append-only event log
    persist(state)

    # --- LOOP GUARDS ---
    enforce_budgets(state)                      # iterations, tokens, time, stall
```

Named break reasons (`MODE_UNKNOWN`, `NO_PLAN`, `CONTRACT_STALE`,
`SCOPE_INVALIDATED`, `TOOL_NOT_GRANTED`, `WRITE_WITHOUT_APPROVAL`,
`COMPLETION_WITHOUT_EVIDENCE`) are refused with journaled
`contract_refused` events — never silently.

## 4. Modes, stances, skills

- **Modes (5)** are permission profiles enforced at the tool boundary: observe, plan, build, verify, ship. The permission gate computes the offered tool set from the mode alone, before the model acts. Stances never widen it.
- **Stances (9)** are reasoning procedures with rubrics: steel-man, feynman, planning-grill, first-principles, premortem, devil's-advocate, advisor, triage, verifier. If it needs no rubric, it's a skill, not a stance.
- **Skills (50)** are injected knowledge — full bodies, never fetched by the model. SHA-256 pinned in `skills/manifest.json`, verified at load (fail-closed). Families: core loop, rigor coach, osmani port, agent personas, references, durable-memory.

Full inventory: `CAPABILITY_REGISTRY.md`.

## 5. Beyond the single turn

- **Mission lifecycle** (DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP) is graph topology in `loop.py` — a turn cannot skip verification because verification *is* the path. `done_claim=true` with unverified criteria never terminates the loop.
- **Approval binding** (`approvals.py`): an approval is cryptographically bound to the canonical plan bytes, acceptance criteria, scope, constraints, exclusions, base commit, approver, and timestamp. Plan drift, base-commit drift, out-of-scope changes, or tampered records are refused; re-approval forms a valid `supersedes_digest` chain.
- **Fan-out** (`loop.py`): parallel workers with atomic overlap/budget checks and a fail-closed synthesis barrier. Known limits: no per-worker model routing, no tournament, no loop-until-done.
- **Skill synthesis** (`synthesis.py`): learnings graduate to skills only through sandbox verification → SHA-256 pin → admit-on-pass. Injected learnings are refused; tampered skills raise at load.
- **Discovery interview**: fires on new tasks; the planning grill enforces one-question-at-a-time and ask-XOR-advance, and rejects question-drips and plan-rushes.

## 6. Surfaces (adapters, not separate loops)

One enforced loop lives in the Python sidecar. Surfaces are adapters:

- **VS Code extension** (`integrations/vscode/extension/`): the product surface — chat, contract view, journal, skills, modes, providers. Spawns the sidecar; cannot bypass the harness. The 0.5.x line bundles the Python runtime (zero setup).
- **MCP server** (`integrations/mcp-server/`): standalone, client-agnostic — contract compiler, turn validator, judge panel, skill synthesis.
- **Kilo / Claude Code integrations**: discipline-grade. Kilo owns its loop (the model could skip the harness tools); the Claude PreToolUse hook is inert until a project opts in via `.awino/`. `awino chat` remains the only full no-bypass guarantee.
- **Operator console**: the CLI inspects state, replays the event log, approves/denies gated actions, switches backends.

Conformance ladder: Context → Interactive → Autonomous-local → Hosted. See `docs/ADAPTER_CONTRACT.md`.

## 7. Backends and security model (honest limits)

- Backends: mock (tests), live local model, provider APIs. Provider keys are user-supplied; the harness never configures them unasked.
- Only provider-exposed thinking is displayed; hidden reasoning is never fabricated.
- No general prompt/source-file PII scrubber. Ollama offers zero-cloud-egress for the local path.
- Workspace cwd is not OS-level isolation; there is no dangerous-command blacklist — approved shell commands may address external/absolute resources.
- Full-file `write_file` remains a token-heavy operation with no patch tool.

## 8. Verification

`prototype/run_tests.sh` — 621 tests: routing, enforcement, judges, approvals, recovery, skills, rigor, osmani, fan-out, synthesis, adversarial proof. `proof/proof_session.py` drives a hostile model through a full mission and writes `TRANSCRIPT.md` with the event-log evidence for every blocked attack.
