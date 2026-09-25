# Changelog

## Unreleased (0.7) — the loop runs end to end

- The mission loop now completes on its own: a failing test run routes
  VERIFY → BUILD for repair (three identical failures trigger the
  three-strike rethink); `task_update` closes the plan's tasks and needs an
  evidence file to mark one done; the journal integrity check passes
  end to end. Proven by `tests/test_e2e_mission.py`.
- The model's tool list is generated from the real schemas (the old prompt
  said `list_dir` takes no arguments, so it could only list the root);
  `search_files` accepts its documented arguments.
- Stances: the model picks one per turn and says why, within the phase's
  allowed set; phase floors always run. Challenges fire on ideas, "is this
  a good idea", "should we", "X vs Y". With no mission, "build X" starts the
  interview instead of a write mode.
- Stories: `story_plan` and `stretch_goal` tools; a Stories panel with the
  brag board; start / resume / close commands; open stories at session
  start; the user is asked to close a verified story.
- One project memory in `<project>/.awino`, shared by the CLI and the
  extension (old `~/.awino-loop` state is copied in once).
- Chat: replies render as prose with a "Next" line (no raw contract
  header); "Sidecar ready" shows once; model errors name the cause and the
  fix.

## 0.6.1 — native-tools auth hotfix (2026-09-25)

- Fix: tool-using turns returned HTTP 401 on keyed OpenAI-compatible
  endpoints. `OpenAICompatibleBackend` inherited the Ollama
  `_chat_tools()`, which sent only `Content-Type` (no `Authorization`),
  posted to `host + /v1/chat/completions` instead of the resolved
  `chat_url` (an endpoint ending in `/v1` became `/v1/v1/...`), read an
  unset `temperature`, and let `HTTPError` escape unmapped. Bedrock SigV4
  inherited the same unsigned request. The native-tools path now uses
  `_signed_headers()` + `chat_url`, identical to the ordinary chat path.
- Regression tests (`tests/test_native_tools_auth.py`) drive
  `generate(tools=...)` against a local server and assert the Bearer /
  SigV4 header on the wire, the URL, and that a 401 surfaces as
  `endpoint HTTP 401` with no key material in the turn.

## 0.6.0 — build loop (2026-09-25)

Pre-release (beta channel). The autonomous coding-agent release:

- Recursive model→tool→result→model loop: single-shot turns replaced
  with recursive rounds; per-round contract recompilation, full
  validation chain every round (schema → normalization → semantics →
  drift → judge → stance → typed coercion), harness tools
  `task_add`/`task_update` (duplicate-safe, one-doing policy) and
  evidence-gated `attempt_completion`, round budget (25), stall
  warn-once-then-halt, cooperative cancellation.
- New agent tools: `search_files`, `find_symbol`, `git_status`,
  `git_diff`, `diagnostics`; `run_command` with Popen polling and
  terminate→kill cancellation.
- State-authoritative context compaction + hooks lifecycle.
- Hash-chained journal: tamper-evident event log (mutation, reorder,
  missing-middle, and malformed-line detection).
- Layered skill loading: four-layer runtime (core/ceremony/mechanical/
  reference), 53 skills.
- Native tool application in the VS Code extension: delegated
  WorkspaceEdit, native diff review, terminal integration, git
  checkpoints/revert, approval batching.
- Interview convergence: model-callable `set_mission` tool
  (observe/plan only, worker-excluded, revision-tracked) so the
  discovery interview records a mission instead of asking forever.
- Chat transcript persistence: `retainContextWhenHidden` + host-side
  `ChatHistory` (500 messages) + `chatReady` handshake with replay.
- Header model picker: the provider pill opens a model QuickPick
  (discovered models, manual entry, Models & Providers shortcut).
- Backend error detail: fallbacks carry `Type: message`, not just the
  exception type.

## 0.5.4 — header model picker (2026-09-25)

Pre-release (beta channel). One reported request:

- Model selection from the chat header: clicking the provider pill
  ("provider · model") now shows a model picker — the provider's
  discovered models with the current one checked, manual entry, and a
  shortcut to Models & Providers — instead of jumping to Settings. The
  ⚙ gear keeps opening Models & Providers. Changing the model marks
  settings dirty and the existing flow offers the sidecar reconnect.

## 0.5.3 — interview-convergence + transcript-persistence hotfix (2026-09-25)

Pre-release (beta channel). Fixes two reported blockers:

- Discovery interview could ask questions forever: the model is now told
  to converge and has a model-callable `set_mission` tool (observe/plan
  only, non-consequential, revision-tracked) that records the mission and
  done criteria, unblocking the floors.
- Chat transcript wiped on tab switch: `retainContextWhenHidden` plus a
  host-side transcript buffer and a `chatReady` handshake that replays the
  transcript and re-pushes fresh chrome on every view (re)load.
- Backend fallbacks now include the exception message (e.g. `endpoint
  HTTP 404`), not just the exception type.

## 0.5.2 — honest-gaps fix program (2026-09-25)

Every previously documented "honest limitation" below is now fixed with
tests, not just documented. 123 new tests across 8 workstreams.

- Bedrock AWS profile/SSO: stdlib-only SigV4 signing in the sidecar from
  the standard AWS credential chain (env → `~/.aws/credentials` →
  `~/.aws/config` with `source_profile` chaining → SSO cache exchanged
  via `GetRoleCredentials`, never used directly as a key). Named
  fail-closed errors (`AWS_SSO_TOKEN_EXPIRED`, `AWS_PROFILE_NOT_FOUND`,
  `AWS_CREDENTIALS_NOT_FOUND`); profile selection in the Bedrock setup
  flow (`awino.bedrockAuthMode`, `awino.bedrockAwsProfile`). Signatures
  cross-validated against botocore (26 tests). First LIVE Bedrock call
  still untested here — no AWS credentials in this environment.
- `patch_file` tool: atomic, fail-closed unified-diff application with
  named refusal codes; build-mode only, consequential like `write_file`.
- Secret redaction: high-confidence credential shapes redacted at the
  journaling boundary and on sidecar log output; cleartext secrets never
  persist. General PII scrubbing stays out of scope by design.
- Approval-target visibility: shell approval cards show cwd-resolved
  absolute targets and flag out-of-workspace addressing. No blacklist —
  the user stays the authority.
- Durable memory: MemPalace cherry-pick — local-first JSONL store
  (`.awino/memory.jsonl`), word-boundary chunking, content-hash dedup.
- `debug` + `rpi` skills: reproduce→diagnose→fix→verify procedure and the
  loop-owner multi-file workflow (52 skills total).
- Sidecar test temp-dir leak fixed at source (`SidecarClient.close()`
  removes its dirs; a stale-only sweeper guards crashed runs without
  touching live concurrent runs).
- Fan-out: per-worker model routing. A fan-out request can carry a
  code-owned routing map (`model_routes`: name → backend) and each
  subtask may name its backend; unknown names raise the new named error
  `UnknownBackendError` ("fanout refused") in pre-flight, before any
  worker spawns. Routing never weakens the atomic overlap/budget checks
  or the fail-closed synthesis barrier. Tournament mode and
  loop-until-done remain unimplemented (roadmap).

## 0.5.1 — beta-validated (on `main`)

The truthfulness release. Three bugs that made the extension lie about its
own state, plus the final wizard fixes — all covered by the beta battery
(wizard 6/6, seed→Tasks 8/8, provider matrix 14/14, extension suite 464/464).

- Dead sidecar no longer shows "connected" — status reflects the real process.
- Generated config no longer silently overrides the provider choice.
- Saved seeds appear in Tasks.
- Wizard: Step 3 rendering fix; silent hang on `SecretStorage.store()` without a working OS keyring fixed.
- Status bar refreshes after Invoke Mode (no more stale mode display).
- Key-status display no longer lies about env-fallback keys.

## 0.5.0

- Bundled Python: the runtime ships inside the VSIX (per-platform builds from pinned, hash-verified standalone CPython). Zero setup — no interpreter install, ever.
- Windows GUI proof: 17/17 checks on real Windows, sidecar proven to spawn from the bundled runtime with no system Python on PATH.

## 0.4.1

- Tasks panel, mission header, session resume, Python recovery.

## 0.4.0

- Competitor-teardown setup UX: onboarding wizard, Get-key buttons, provider docs, never-blank provider binding, model intelligence, provider pill.

## 0.3.0

- Bedrock provider (via its OpenAI-compatible endpoint) + permission-first connection importer (Claude Code, Kilo CLI, `.env` scan with secret-safety iron rule).

## Harness — integration batch (on `main`)

- **Durable memory (MemPalace cherry-pick)**: `prototype/memory_store.py` —
  local-first JSONL store at `.awino/memory.jsonl` with store/recall/search,
  word-boundary chunking (byte-identical reassembly) and content-hash dedup;
  the `durable-memory` skill (SHA-256 pinned, routed on DEFINE and the
  new-task path) teaches recall-before-planning and persist-at-closure.
  No compression (measured 2.7x with fidelity loss, not 30x), no daemon,
  no vector search.
- **Osmani skills (14)**: TDD, security, code review, shipping, constraints, doubt, API design, source-driven, idea refinement, ADRs, observability, CI/CD, failure modes, adoption.
- **Rigor coach (11)**: the agent-rigor practice auditor — five laws, doom-loop breaker, layered skills.
- **Harness-adoption standards**: completion summary, incident response, DORA metrics, deployment readiness, CI diagnosis; definition-of-done, lifecycle-sequence, critical-thinking references.
- **Fan-out**: parallel workers with atomic overlap/budget checks and a fail-closed synthesis barrier.
- **Adapter contract** (`docs/ADAPTER_CONTRACT.md`): provider-neutral controller protocol and the Context → Interactive → Autonomous-local → Hosted conformance ladder.
- **Agent persona skills (5)**: software-engineer, ai-researcher, ai-architect, forward-deployed-engineer, cybersecurity-engineer, with a deterministic router.
- **Plug-and-play projects**: auto-init on session start, startup checklist (venv/just/ruff provisioning), `.awino/project.yaml` source of truth, memory registry, task DAG store, skill egress audit, hard verification gate (separate verifier worker; no pass verdict, no REVIEW), calibration labels.

## In flight (not on `main` yet)

- Approval binding, verification budgets, hash-chained journal, layered loading, beta release pipeline — building on feature branches, merge after verification.
