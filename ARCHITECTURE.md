# A.W.I.N.O. Loop-Owner Rebuild — Architecture Draft

**Date:** 2026-09-18
**Status:** Draft for review. Nothing here is built yet.
**Decision it encodes:** A.W.I.N.O. stops being a CLI that stands beside the agent loop and becomes the loop itself. The per-turn contract moves from the model's context into the harness's code path.

---

## 1. Why a rebuild, not a patch

The GitHub version's failure is structural, not cosmetic:

- The "orchestrator" never decides. `machine.py`: *"There is no node whose action is 'decide'."* The human steps the loop.
- Gates bind only if the agent volunteers to call them. The governed party decides whether to be governed.
- Modes install prose into host harnesses. The only real forcing function is the host's.

No prompt improvement, gate addition, or mode rewrite changes this. Markdown has no execution semantics; prompt instructions are soft constraints on a probabilistic model; the governor and the governed are the same entity. The field's own docs concede it (Deep Agents: *"trust the LLM"*; pydantic-ai harness: *"no magic, it's capabilities all the way down"*).

**Therefore:** the contract must be executed by code that the model cannot skip, on every turn, whether the model cooperates or not. That is the entire rebuild.

---

## 2. Non-negotiable principles

1. **The harness runs the `while` loop.** Whoever runs the loop owns the conversation. A.W.I.N.O. is the loop.
2. **State lives in code, not in context.** Objective, mission, progress, mode — persisted in code-owned state, re-injected fresh every turn. The model never carries the contract; the harness does.
3. **Every turn emits a typed contract object.** Structured output, validated in code. Invalid output is retried or rerouted — never silently accepted.
4. **The judge is code-scheduled.** A second-model review runs because the loop says so, not because the worker asks for it.
5. **Enforcement happens at boundaries the model cannot argue with:** the model-call boundary (pre/post hooks), the output boundary (schema validation), the tool boundary (permissions, sandbox, approvals).
6. **Model-agnostic by construction.** The contract is code; models are interchangeable backends. Plug-and-play means the *contract* doesn't change when the model does.
7. **Proof, not claims.** Every enforcement point ships with an adversarial test that tries to defeat it.

---

## 3. The loop

```text
state = load_or_init(conversation_id)          # code-owned, persisted, event-sourced

while not state.done:
    # --- PRE-TURN: contract compilation (code, not prompt) ---
    contract = compile_contract(state)
    #   objective, mission, tools/skills available, setup, mode,
    #   open questions, progress summary, understanding checks

    # --- MODEL CALL: contract injected fresh, output typed ---
    turn = model.call(
        system=base_system + render(contract),  # injected EVERY turn via wrap hook
        messages=history,
        output_schema=TurnContract,             # objective, plan, tool_calls,
    )                                           # progress_delta, questions, done_claim

    # --- POST-TURN: validation (code) ---
    verdict = validate(turn, contract, state)   # schema + semantic checks
    if verdict == REJECT:
        turn = model.retry(verdict.feedback)    # bounded retries, then escalate
        continue

    # --- JUDGE: always-on, code-scheduled ---
    judge_verdict = judge(turn, contract, state)  # separate model call; runs ALWAYS
    if judge_verdict == FAIL:
        state.flags.append(judge_verdict.reason)
        continue  # worker does not proceed on a failed turn

    # --- TOOL BOUNDARY: permissions enforced where tools run ---
    results = execute(turn.tool_calls,
                      permissions=state.mode.permissions,
                      approvals=approval_gate)   # human gate on consequential actions

    # --- STATE: externalized, persisted ---
    state = reduce(state, turn, results)        # append-only event log
    persist(state)

    # --- LOOP GUARDS ---
    enforce_budgets(state)  # max iterations, token/time budgets, stall detection
```

Nothing in this loop is optional for the model. The model produces `turn`; everything else is code.

---

## 4. The per-turn contract, field by field

This is the user's contract, each item mapped to a mechanism. No item is prose-only.

| Contract item | Mechanism (code) |
|---|---|
| **User's objective** | Extracted at conversation start into `state.objective` via structured output; re-validated when the user redirects. Stored in state, re-injected every turn. Drift detector flags when tool calls stop serving it. |
| **Mission** | `state.mission`: the current mission with measurable done-criteria. Set explicitly, updated as it changes. The loop refuses `done_claim=true` unless done-criteria verify in code. |
| **Tools** | Tool registry is code. `prepare_tools` per step: only tools relevant to the mission+mode are even offered. The model cannot call what it cannot see. |
| **Skills** | Skill catalog is code. Skill selection is a code-side retrieval step (match mission → skills), injected into the contract. The model doesn't "choose" skills; the harness loads them. |
| **Setup** | Preconditions checked in code before the loop body runs (auth, environment, budget). Missing setup blocks the turn with a specific error, not a model apology. |
| **User understanding** | `state.open_questions` + `state.assumptions`. Validator rejects turns that act on flagged ambiguities instead of asking. Assumptions the model makes are extracted into the typed output and surfaced, not buried in prose. |
| **User input** | Every user message is parsed into a typed `UserInput` event (new objective? redirect? question? approval/denial?). Redirects update `state.objective` — the contract recompiles next turn automatically. |
| **Progress** | `progress_delta` is a required schema field every turn; the reducer appends it to the event log. "Are we close?" is answered from the log + done-criteria, computed, not claimed. |
| **Mode/skill selection** | **Code-routed, not model-chosen.** A router (deterministic rules + optional classifier) sets `state.mode` from the mission and input type. Modes are permission profiles enforced at the tool boundary. The model operates *inside* the mode; it does not select it. |

---

## 5. Enforcement points (concrete)

1. **Own the loop.** `while` loop in the harness. No host loop, no voluntary CLI.
2. **Per-turn prompt recompilation.** `wrap_model_request`-style hook: fresh contract block injected into the system prompt on *every* model request, compiled from code-owned state.
3. **Structured output + code-side validation.** `TurnContract` schema; validators check schema *and* semantics (e.g., tool calls ⊆ offered tools; `done_claim` only with verified criteria; no action on open ambiguities). Violations → bounded `ModelRetry` → escalate to user.
4. **Always-on judge.** Second model reviews each turn against the contract. Scheduled by the loop, unconditional. (Judgment content is LLM; the *scheduling* is code — that's what makes it enforcement.)
5. **Compiled state machine for the contract lifecycle.** Objective → mission → execution → verification → done is graph topology. A turn cannot skip verification because verification *is* the path.
6. **Event-sourced action stream.** All effects flow through typed, validated, persisted events. Anything outside the schema cannot happen.
7. **Tool-boundary enforcement.** Permissions, sandboxing, approval gates where tools execute. The one place the model cannot argue.

---

## 6. Substrate decision: build on pydantic-ai's hooks, not a hand-rolled loop

**Recommendation: pydantic-ai as the loop substrate.** Reasons:

- It already owns a model-agnostic loop across providers (OpenAI, Anthropic, Gemini, Bedrock, Ollama, etc.) — this is the plug-and-play the user expected.
- It exposes the exact interception points the design needs: `wrap_model_request` / `before_model_request` (per-turn contract injection), `output_validate` (schema + semantic validation), `ModelRetry` / `SkipModelRequest` (control flow on violation), `prepare_tools` (per-step tool offering), `UsageLimits` (budgets), `ApprovalRequired` / `ToolGuardrail` (tool-boundary enforcement).
- Its `pydantic-ai-harness` package (`GoalReanchor`, `TrajectoryJudge`, planning tools) is useful *reference and scaffolding* — but we treat its own caveat seriously ("no magic"): we keep the voluntary parts out of the enforcement path and reimplement the contract as code-owned.

**Why not the alternatives:**

- **LangChain Deep Agents:** good middleware slots, but its planning is prompt-driven and its docs say "trust the LLM." We'd be fighting the substrate's philosophy.
- **LangGraph alone:** excellent for the contract *state machine* (section 4's lifecycle as graph topology), but it provides zero turn semantics — we'd still hand-roll the model loop. Candidate as a *component* (contract lifecycle graph inside the pydantic-ai loop), not the substrate.
- **LangSmith:** observability only. Useful for tracing/evals of the new loop; not a runtime.
- **Hand-rolled loop from scratch:** rejected. No reason to reimplement provider abstraction, retries, and streaming in 2026.

**Proposed shape:** pydantic-ai agent as the runtime; A.W.I.N.O. contract engine mounted on its hooks; LangGraph optionally expressing the mission lifecycle; LangSmith (or equivalent tracing) for observability and the adversarial eval suite.

---

## 7. Model-agnostic plug-and-play (the original expectation, honored)

- The contract engine never sees model internals. It consumes `TurnContract` objects and produces contract blocks — plain data.
- Swapping models = changing the pydantic-ai model string. The contract, validators, judge scheduling, and tool boundary are untouched.
- The adversarial test suite runs against a *model matrix* (at least 2–3 providers). An enforcement point that only holds on one model is marked as failed — enforcement must be substrate-independent because it lives in code, not in any model's good behavior.

---

## 8. What happens to existing A.W.I.N.O. assets

| Asset | Fate in the rebuild |
|---|---|
| Knowledge base (chapters) | Becomes **contract content compiled by code**, not files the model may or may not read. Chapters are retrieved and rendered into the per-turn contract block by the harness. |
| Gates (`gate open/close`) | Become **code-enforced checkpoints** in the loop (verification nodes the turn cannot skip). The CLI remains as an operator console, not the enforcement mechanism. |
| Modes | Become **permission profiles + router rules**, enforced at the tool boundary. Mode selection moves from model choice to code routing. |
| Exams | Become the **verification suite for the new loop** — including adversarial exams that attempt to bypass each enforcement point. |
| CLI | Becomes the **operator console**: inspect state, replay the event log, approve/deny gated actions, switch models. It no longer *is* the framework; it *operates* it. |
| Host adapters (Claude Code / Kilo / Roo) | **Honest downgrade:** these become advisory-install mode (legacy). A closed host loop cannot be owned from outside — there is no public hook into Claude Code's turn. The loop-owner is its own runtime. This is stated plainly so nobody confuses the two modes. |

---

## 9. Hard limits (stated upfront, not discovered later)

1. **We cannot own Claude Code's (or any closed host's) conversation.** The loop-owner is a standalone runtime. Anyone wanting A.W.I.N.O. enforcement inside Claude Code gets the advisory installer, with its limits labeled.
2. **The judge's *content* is still LLM judgment.** What's enforced is that the judge *runs* and that its FAIL verdict *blocks*. Judge quality is improved via evals, not assumed.
3. **"User understanding" checks are heuristic.** The validator can catch acting-on-ambiguity patterns; it cannot guarantee comprehension. The mitigation is structural: assumptions are extracted, surfaced, and must be confirmed before consequential actions.
4. **Latency and cost.** Per-turn injection + validation + an always-on judge call adds tokens and time to every turn. Budgets and judge sampling (every turn for consequential work, sampled for trivial turns — with the sampling policy itself in code) manage this. This tradeoff is explicit.
5. **This is a rebuild, not a migration.** The current CLI-advisory architecture does not evolve into this; it gets replaced as the runtime while its knowledge content is absorbed.

---

## 10. Verification: proof, not claims

Each enforcement point ships with adversarial tests. The suite's job is to *defeat* the harness:

- **Skip attempts:** a test model is instructed to ignore the contract, skip planning, and claim done. The harness must block or retry — the test asserts on loop behavior, not model prose.
- **Schema attacks:** malformed `TurnContract` outputs, tool calls outside the offered set, `done_claim` with unverified criteria. Validators must reject; retries must be bounded; escalation must reach the operator.
- **Judge-bypass attempts:** worker output crafted to fool the judge. Track judge catch-rate as a metric, improve via evals.
- **Drift attempts:** long multi-turn runs where the objective subtly shifts. The drift detector must flag; the contract must recompile.
- **Model matrix:** the full adversarial suite runs against ≥3 models/providers. Enforcement that depends on one model's temperament fails the suite.
- **Done-criteria integrity:** `done_claim=true` with unmet criteria must never terminate the loop. This is the single most important test — it is the old `gate close` failure mode, now structurally impossible.

**Done means:** the adversarial suite is green across the model matrix, and a hostile test model cannot complete a mission without satisfying the contract. Not "the model usually complies."

---

## 11. Phased build (each phase measurably done)

- **Phase 1 — Loop skeleton + contract injection.** pydantic-ai loop running; `wrap_model_request` injects a compiled contract block from code-owned state every turn. *Done when:* the contract block appears in every model request in traces, for 3+ models.
- **Phase 2 — Typed turns + validation.** `TurnContract` schema; `output_validate` with semantic checks; bounded retry; escalation. *Done when:* adversarial skip/schema attacks are blocked in tests.
- **Phase 3 — Always-on judge.** Code-scheduled second-model review; FAIL blocks the turn. *Done when:* judge-bypass tests show measured catch-rate; bypasses become test cases.
- **Phase 4 — Tool boundary.** `prepare_tools` per-step offering; permissions/sandbox/approvals at execution. Modes become permission profiles. *Done when:* a turn cannot execute a tool outside its mode's profile, proven by test.
- **Phase 5 — State + lifecycle.** Event-sourced state, mission lifecycle as enforced path, drift detection, done-criteria integrity. *Done when:* the hostile-model full-mission test cannot finish without satisfying the contract.
- **Phase 6 — Operator console + model matrix.** CLI becomes the console (inspect, replay, approve); adversarial suite green across ≥3 providers. *Done when:* swapping models changes nothing about enforcement behavior.

---

## 12. Open questions (for the user)

1. **Runtime shape:** standalone TUI/CLI runtime first, or a server with a chat UI from the start? (Recommendation: CLI runtime first — Honda. The chat UI is Bugatti.)
2. **Judge sampling policy:** every turn unconditionally, or sampled for trivial turns to control cost? (Recommendation: unconditional in Phase 3; optimize with data later.)
3. **Scope of the first mission domain:** general coworker (Jarvis-like, any task) or one vertical first (e.g., coding coworker, where verification is easiest)? (Recommendation: general loop, but prove it on coding missions first — done-criteria are crisp there.)
4. **What happens to the GitHub repo:** does the loop-owner become A.W.I.N.O. 0.9 (replacing the advisory architecture), or a separate track while the advisory version continues? (Recommendation: it becomes the runtime; the advisory installer stays as a legacy compatibility layer, clearly labeled.)
