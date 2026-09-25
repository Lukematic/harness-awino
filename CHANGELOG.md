# Changelog

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
