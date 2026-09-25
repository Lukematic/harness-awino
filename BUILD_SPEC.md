# A.W.I.N.O. Loop-Owner — Merged Build Spec

**Date:** 2026-09-18
**Status:** **Complete.** All five phases built, tested, and merged. Kept for history — see `ARCHITECTURE.md` (as built) and `CHANGELOG.md` for the current system.
**Product:** a chat runtime where the conversation *is* the state. The human+AI pair chat; the harness owns the loop, carries missions project-to-project, and enforces the contract every turn in code.

---

## 1. Product definition

One chat session, one owned loop. The user talks; the harness:

- keeps the mission, plan, approvals, and progress in code-owned state (not in the model's memory),
- recompiles the contract and injects it fresh every turn,
- runs tools itself behind permission/approval boundaries,
- moves with the user project-to-project: each project has its own state directory, missions persist across sessions and restarts,
- refuses false completion: done is computed from evidence, never claimed.

This is the "Jarvis" shape: a coworker that owns the running conversation. It is **not** a plugin inside Kilo/Claude Code's chat — a closed host loop cannot be owned from outside. The harness is its own chat runtime (`awino chat`). Native-host bridges are a later, separately-accepted surface.

---

## 2. Why this, why now

Both source documents independently reached the same diagnosis:

- The GitHub version has an **execution void**: rich routing/policy/ledgers, but `_execute` prints a prompt and waits for a human; the "orchestrator" never decides (`machine.py`: *"no node whose action is 'decide'"*); gates bind only if the agent volunteers to call them.
- Markdown has no execution semantics. Prompt instructions are soft constraints on a probabilistic model. The governor and the governed are the same entity. The field's own docs concede it: Deep Agents — *"trust the LLM"*; pydantic-ai harness — *"no magic, it's capabilities all the way down."*
- Therefore the contract must be executed by code the model cannot skip, every turn, whether the model cooperates or not.

**Migration rule (from the whitepaper):** keep the mission and useful skill content. Replace the competing bespoke machine/stepper schedulers for new runs. Do **not** embed both old and new schedulers and call it integration. Old runs stay read-only; old receipts are not converted into new proof.

---

## 3. The contract schema (merged)

The whitepaper's eleven blocks plus the per-turn fields from the rebuild draft. Every block has an **executable mechanism** — no block is prose-only.

| Block | Executable mechanism | What still requires judgment |
|---|---|---|
| GOAL / objective | `state.objective`, extracted via structured output at start; drift detector flags tool calls that stop serving it | Whether the goal is worth pursuing |
| MISSION | Current mission + measurable done-criteria; `done_claim` rejected unless criteria verify in code | — |
| CONTEXT | Bounded context compiler with source IDs and project namespace | Which evidence matters most |
| REQUIREMENTS | Requirement IDs mapped to tests/review criteria | Completeness |
| CONSTRAINTS | Tool capabilities, sandbox mounts, network allowlist, policy checks | Unanticipated business constraints |
| INSTRUCTION PRIORITY | Trusted policy/config stored separately from retrieved content; untrusted data cannot write approvals or policy | Prompt injection can't be declared solved by ordering text |
| AUTONOMY | Permitted phase transitions, call/token/time limits, explicit interrupt rules | Whether an ambiguity is material |
| TOOLS | Pre-execution authorization bound to normalized arguments + current plan revision; per-step offering — the model can't call what it can't see | Some tool safety needs domain analysis |
| SKILLS | Code-side retrieval (mission → skills); harness loads full skill bodies into the contract — never model-voluntary fetch | Selection among plausible skills |
| SETUP | Preconditions checked in code before the turn runs; missing setup blocks with a specific error | — |
| DELEGATION | Scheduler enforces file ownership, concurrency, aggregate budgets | Whether parallel work is useful |
| USER INPUT (per turn) | Every user message parsed into a typed event: new objective? redirect? question? approval/denial? Redirects recompile the contract next turn | — |
| USER UNDERSTANDING (per turn) | `open_questions` + `assumptions`; validator rejects turns that act on flagged ambiguities instead of asking; assumptions extracted into typed output and surfaced | Comprehension itself is heuristic |
| PROGRESS (per turn) | `progress_delta` is a required schema field; reducer appends to the event log; "are we close?" answered from log + done-criteria, computed not claimed | — |
| MODE (per turn) | **Code-routed, not model-chosen.** Router sets `state.mode` from mission + input type; mode = permission profile enforced at the tool boundary | — |
| OUTPUT | Typed artifact schemas + user-facing rendering | Clarity, persuasion |
| VERIFICATION | Independent commands + reviewed qualitative criteria; worker cannot modify acceptance checks except via separately approved change | Coverage gaps, reviewer quality |
| STOP CONDITION | Release controller requires all applicable evidence or emits blocked/cancelled | Whether business goals are genuinely met |

**Stance/skill note:** stances (steel-man, Feynman, planning grill) are procedures the harness invokes and evaluates via rubric — the enforced part is the invocation, schema, and evaluation pathway, not mental compliance.

**Turn header, deterministic stance triggers, next-action line (ideas from the Kilo session — implemented in code):**
- **Turn Header Contract.** Every turn begins with a harness-rendered header: `[A.W.I.N.O. | phase: <FLOOR> | stance: <STANCE> | mission: <id> | run: <id>]`. Rendered from code-owned state, never model prose. The validator asserts the header is present and agrees with state — it is a position sensor, not decoration.
- **Elevator transition gate (named).** PLAN → BUILD requires a user-approved contract. This is the approval gate, now explicit: the transition is a code check, not a reminder.
- **Deterministic stance triggers (router rules, code).** Input patterns route stances: "i think / we should" → Steel-Man; "how does / teach me / learn" → Feynman Loop; new task / raw idea → Planning Grill. Firing a stance = harness loads the full procedure into the contract block + evaluates the output against a rubric (keyword-only opposition fails).
- **Next-action line.** Every turn's contract block ends with `Floor: <current> | Next action: <one concrete step> | Blocked on: <...>`, compiled from state. The human always sees where the elevator is and what's next.
- **Honest labeling.** As an `awino.md` edit inside Kilo, these four are `PROMPT-PATCH (debt)` — they load automatically but nothing verifies them. That edit is still worth doing as the advisory layer for Kilo sessions. Enforcement of the same four rules lives in the loop-owner, where the header is rendered, the gate is checked, and the triggers are routed in code.

**Mode × stance × skill: the per-turn triple.** The three compose orthogonally — each answers a different question, and the harness routes all three in code every turn:
- **Mode = what the turn may touch** (permission profile). Routed from mission phase + risk. Examples: observe (read-only), plan, build, verify, ship.
- **Stance = how the turn must think** (cognition procedure). Routed from input pattern + intent via deterministic triggers. Examples: steel-man, Feynman, planning-grill, first-principles, premortem, devil's-advocate.
- **Skill = what the turn may know/use** (capabilities). Retrieved from mission domain + task. Examples: coding, research, decision-analysis, writing, comms-coach.
- The same stance can run under different modes; the same skill can serve different stances. The turn header carries the triple so the combination is always visible: `[A.W.I.N.O. | phase: PLAN | mode: plan | stance: steel-man | skills: decision-analysis]`.
- Canonical combinations (router defaults):
  - "I think we should X" → plan + steel-man + domain(X) — opinions get challenged before commitment.
  - "Teach me Y" → observe + Feynman + explainer/domain(Y) — teaching needs analogy → gap → example.
  - New task/idea → plan + planning-grill + mission-definition — ideas get shaped before execution.
  - "Advise me on Z" → plan + steel-man→premortem + decision-analysis/domain(Z) — advice needs the counter-case plus failure rehearsal; the advisor's failure mode is telling you what you want to hear.
  - "Fix this bug" → build (scoped) + first-principles + repo/code — fixes need root cause, not patches.
  - "Ship it" → ship + premortem + verification — release needs failure rehearsal plus evidence.

**Stance × tools: who grants what (the four personas).** The triple composes orthogonally, and tool authority follows one hard rule: **stances never grant tools — modes grant them, stances can only add restraint.** The permission gate computes the offered tool set from the routed mode alone, before the model acts. Two stance rubrics already enforce tool discipline from the thinking side: Feynman rejects *any* tool call while teaching (`feynman: no tool calls while teaching`); planning-grill rejects tool calls in any turn that asks questions (`planning-grill: must not act in a turn that asks questions`). So a stance can narrow what the model may do with its granted tools, but it can never widen them — the model cannot think its way into more tools.

**The intent call uses no tools.** The elevator/intent sensor is harness code (pipeline step 2): it classifies the input and routes the triple before the model acts. It never calls tools, and the model never calls it — there is no path by which a turn escalates its own tool set. (Prototype: deterministic pattern matching on the input text. A production deployment may use a cheap classifier for the sensor, but it still sits on the harness side of the tool boundary: it classifies, it never executes.)

The four personas, as routed:

| Persona | Intent | mode × stance × skill | Tools granted (mode) | Tools the stance forbids | Why this shape |
|---|---|---|---|---|---|
| Steel | "I think / we should X" | plan × steel-man × domain(X) | reads: read_file, list_dir | writes/execution — never granted | An opinion never grants the right to act. Steel may read to build a stronger counter-case; the rubric checks fair restatement + substance. |
| Teacher | "Teach me / how does / learn Y" | observe × Feynman × explainer/domain(Y) | reads (mode grants them) | **all tool calls** — rubric rejects them | Teaching is pure explanation: analogy → gap question → example → snapshot. The teacher explains from injected skill context, not by fetching. |
| Researcher | New task / raw idea | plan × planning-grill × mission-definition | reads: read_file, list_dir | any tool call in a turn that asks questions | Discovery, not action: reads may inform the next question, but while questions are open the researcher investigates only to ask better. |
| Advisor | "Advise me on Z" | plan × steel-man→premortem × decision-analysis/domain(Z) | reads: read_file, list_dir | writes/execution — never granted | The advisor's failure mode is telling you what you want to hear, so the chain forces counter-case then failure rehearsal. Plan mode is deliberate: the advisor must not act on its own advice — acting on it re-enters the elevator as a new mission through DEFINE → approval → BUILD. |

Note the teacher row: the mode grants reads, the stance forbids all calls. That is the orthogonality working as designed — grant and restraint are separate dials, and both are enforced in code.

**Registry and authoring.** The full inventory lives in `CAPABILITY_REGISTRY.md` (every mode, stance, skill, intent, floor — prototype and the 16 legacy repo skills, with gaps marked). New capabilities are authored via `AUTHORING_TEMPLATE.md`: a mode is a permission profile, a stance is a procedure + rubric, a skill is injected knowledge. Check the registry first; duplicates are how whack-a-mole starts.

**Floor binding table (merged from the Kilo session's Floor × Skill × Stance × Autonomy matrix).** Each elevator floor binds autonomy level, stance, skills, permitted tools, and its exit gate. Autonomy vocabulary: **supervised** = the human approves each transition; **bounded** = the harness acts freely but only inside the approved contract/scope.

| Floor | Autonomy | Stance (router default) | Active skills | Permitted tools | Exit gate |
|---|---|---|---|---|---|
| 1 DEFINE | Supervised | planning-grill (one targeted question at a time) | mission-definition, discovery/interview | read-only; no code or file edits | contract drafted → human approves → PLAN |
| 2 PLAN | Supervised | first-principles | decision-analysis, domain skill | read-only; no edits | contract approved → BUILD |
| 3 BUILD | Bounded | first-principles (steel-man on approach choice) | coding/repo | edits only within approved SCOPE file list | diff produced → VERIFY |
| 4 VERIFY | Bounded | devil's-advocate on results | testing | bash test commands; raw output shown; modifying tests to force a pass is blocked | exit code 0 → REVIEW |
| 5 REVIEW | Bounded | premortem | code-review | diff inspection | no regressions/dead code/side effects → SHIP |
| 6 SHIP | Supervised | premortem | verification/release | deliver only | completion claimed only on evidence |

**Trigger rules (from the Kilo session, implemented in code):**
- **Scope change** ("now let's add A", "can we also do B?") → drop to DEFINE/PLAN immediately, invalidate the previous approval, draft a revised contract, wait for approval. Approvals never survive a scope change.
- **Learning intent** ("how does / teach me / learn") → Feynman enforced procedure: 1. everyday analogy, 2. one targeted gap question, 3. one concrete example, 4. one-sentence teaching snapshot. The rubric checks all four are present.

**Per-turn pipeline order (from the Kilo live demo).** Every turn executes in this order, in code:
1. **Contract ingestion** — compile and inject the current contract block (state → prompt).
2. **Elevator sensor** — classify input intent; route stance and phase (opinion → steel-man; learning → Feynman; new task → DEFINE; "approved/execute" → BUILD only via the approval gate).
3. **Autonomy & permission gate** — compute the permitted tool set for the current floor; reject anything outside it before the model acts.
4. **Header emission & structured response** — render `[A.W.I.N.O. | phase | loop | run | knowledge: n/m | stance]`, announce the stance with a one-line reason (`STANCE -> grill (active Floor 1 scoping before drafting any plan)`), then the phase-specific deliverable plus the next-action line.
- In Kilo this pipeline is narrated by the model; in the loop-owner each step is executed by the harness, and step 3 is a real tool-boundary check, not a stated rule.

---

## 4. The loop

```text
state = load_or_init(project_id, conversation_id)   # code-owned, persisted, event-sourced

while not state.done:
    contract = compile_contract(state)               # all blocks above, fresh every turn
    turn = model.call(
        system = base_system + render(contract),     # injected EVERY turn (wrap hook)
        messages = history,
        output_schema = TurnContract)                # objective, plan, tool_calls,
                                                     # progress_delta, questions, done_claim
    verdict = validate(turn, contract, state)        # schema + semantic checks (code)
    if verdict == REJECT:
        turn = model.retry(verdict.feedback)         # bounded retries, then escalate
        continue
    judge_verdict = judge(turn, contract, state)     # separate model; ALWAYS scheduled by code
    if judge_verdict == FAIL:
        state.flags.append(judge_verdict.reason)
        continue                                     # worker does not proceed on failed turn
    results = execute(turn.tool_calls,
                      permissions = state.mode.permissions,
                      approvals = approval_gate)      # human gate on consequential actions
    state = reduce(state, turn, results)             # append-only event log
    persist(state)                                   # crash-safe; resume reconciles effects
    enforce_budgets(state)                           # iterations, tokens, time, stall detection
```

Enforcement points, concretely: (1) the harness runs the `while` loop; (2) per-turn prompt recompilation from external state; (3) structured output + code-side validation with retry/reject; (4) always-on code-scheduled judge; (5) mission lifecycle as enforced path (DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP, with revise loops — a turn cannot skip verification because verification *is* the path); (6) event-sourced action stream — anything outside the schema cannot happen; (7) tool-boundary enforcement (permissions, sandbox, approvals) where tools execute.

---

## 5. Chat session as state (the product shape)

- `awino chat` opens a REPL. Normal chat turns go through the loop above; slash commands operate the harness: `/project <id>` (switch — state dir switches, mission context swaps), `/mission <text>` (set/replace mission with done-criteria), `/approve`, `/status`, `/done`.
- State layout: `<awino_home>/loop/projects/<project_id>/` — `events.jsonl` (append-only), `snapshot.json`, `artifacts/`. Crash → new process loads snapshot, replays/validates the tail, reconciles unknown effects before replaying (idempotency keys where supported; uncertain writes pause for inspection).
- Project-to-project: missions, approvals, and progress are per-project and survive session end. Switching projects is a state-dir switch, not a memory exercise.
- Lifecycle phases are graph state, not vibes. A trivial question takes the short read-only route with its own acceptance contract; a scope change returns to PLAN; a failed test returns to BUILD only while attempts and approval remain valid. No missing state falls through to unrestricted BUILD.

---

## 6. Substrate decision

**Unresolved between two finalists — decided by proof, not argument:**

- **Deep Agents + LangGraph:** actual agent factory, human-in-the-loop interrupts, durable checkpointing (SQLite/Postgres), subagent delegation. Best fit for the long-lived lifecycle. Caveats: defaults are model-voluntary (*"trust the LLM"* — skill middleware must be overridden with deterministic injection); safe shell + Windows persistence must be proven.
- **pydantic-ai (+hooks):** richest per-turn interception points (`wrap_model_request`, `output_validate`, `prepare_tools`, `ApprovalRequired`/`ToolGuardrail`), model-agnostic loop. Caveats: `StepPersistence` is explicitly not full graph-state checkpointing; package is 0.x Alpha.

**Phase 0 (prototype, this machine):** implement the loop in plain Python behind a `ModelBackend` interface with mock backends (scripted + hostile). This proves the *contract-in-code architecture* substrate-independently — the enforcement logic is the invention; the substrate is a plug-in.

**Phase A (kill criterion, Windows + real credentials):** pin deps, run model → tool → approval interrupt → resume-after-kill against a real provider. If Windows/provider execution, interrupt/resume, or deterministic skill delivery fails — stop. Run against both finalists if cheap; let the kill criterion pick.

**Not candidates:** LangSmith (observability only — flight recorder, not autopilot); hand-rolled provider loop (no reason to reimplement it); AutoHarness/neosigma/OpenHarness (reference material, not the runtime).

---

## 7. Verification: proof, not claims

**Adversarial suite (runs in Phase 0 with mock backends, then against the model matrix):**

- **Skip attempts:** hostile backend ignores the contract, skips planning, claims done. Harness must block/retry — assert on loop behavior, not prose.
- **Schema attacks:** malformed turns, tool calls outside the offered set, `done_claim` with unverified criteria. Validators reject; retries bounded; escalation reaches the operator.
- **Done-forgery:** `done_claim=true` with unmet criteria must **never** terminate the loop. The single most important test.
- **Judge bypass:** worker output crafted to fool the judge; track catch-rate as a metric.
- **Drift:** long runs where the objective subtly shifts; detector must flag; contract must recompile.
- **Ambiguity action:** turn acts on an open question instead of asking → rejected.
- **Crash/resume:** kill mid-effect; new process reconciles — no duplicate writes, no lost approvals.
- **Model matrix:** full suite against ≥3 providers. Enforcement that depends on one model's temperament fails.

**Acceptance matrix (from the whitepaper, condensed):** startup → real model/tool receipts (not printed labels); approval denied/stale → no effects; work → patch + protected test results bound to revision; review → ship requires current independent review + authorization; budget exhausted → terminal state, never auto-increase; provider portability → same journeys under two providers; packaging → real installed-artifact journey.

All hard negative tests pass — not an average. Suggested pilot bar: ≥90% rubric pass on held-out conversations with **zero unauthorized actions** (owner accepts the threshold before the eval runs).

---

## 8. Hard limits (stated upfront)

1. Cannot own a closed host's conversation (Kilo/Claude Code). The loop-owner is its own runtime.
2. The judge's *content* is LLM judgment; what's enforced is that it runs and its FAIL blocks. Judge quality improves via evals.
3. Understanding checks are heuristic; the structural mitigation is surfacing assumptions and requiring confirmation before consequential actions.
4. Per-turn injection + validation + always-on judge costs tokens and latency every turn. Sampling policy for trivial turns lives in code and is explicit.
5. Checkpoints are not exactly-once magic for external effects — reconcile, don't blindly replay.
6. Keep tracing local by default; no source/secrets/conversation content to cloud observability without explicit approval.
7. This is a rebuild, not a migration of the advisory architecture.

---

## 9. Phased build

- **Phase 0 — Architecture proof (mock backends, this machine).** Loop + contract + validation + judge + tool boundary + persistence, all tests green including hostile-backend suite. *Done when:* hostile mock cannot complete a mission without satisfying the contract.
- **Phase A — Execution proof (Windows + real provider, kill criterion).** 2–3 days. *Done when:* model → tool → approval → resume-after-kill works; deterministic skill delivery demonstrated. Failure → stop.
- **Phase B — Trusted lifecycle + policy.** Typed contract, immutable revisions, phase transitions, budgets, effect journal, sandbox. 4–6 days.
- **Phase C — Skills, stances, learning.** Mandatory skill loading with hashes, steel-man/Feynman/grill procedures with rubric evals. 3–5 days.
- **Phase D — End-to-end + delegation.** Full DEFINE→SHIP journeys, ≥2 workers with file ownership + shared budget. 4–6 days.
- **Phase E — Product surface + release.** `awino chat` as the owned runtime, operator console, clean install, rollback. 3–5 days.

---

## 10. Open questions

1. Runtime surface first: terminal chat (recommended — Honda) or local server + UI from the start?
2. Judge on every turn unconditionally, or sampled for trivial turns (policy in code either way)?
3. First proving domain: general coworker, or coding missions first where done-criteria are crispest? (Recommended: general loop, proven on coding first.)
4. Does the loop-owner become A.W.I.N.O. 0.9, replacing the advisory runtime, with the old CLI kept as a labeled legacy installer?
5. Owner acceptance needed before paid Phase A: provider endpoint + spend ceiling, sandbox/workspace bounds.
