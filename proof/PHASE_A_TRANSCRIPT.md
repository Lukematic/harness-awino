# Phase A — Execution proof (local backend)

BUILD_SPEC section 9 exit criteria: model -> tool -> approval ->
resume-after-kill works; deterministic skill delivery demonstrated.
No paid provider, no Windows host: proven against the local model
backend (OllamaBackend + llama.cpp server, Qwen2.5 GGUFs).

## Proof 3 — kill mid-effect -> reconcile (2026-09-22 19:59)

- child subprocesses died via os._exit(1) with no cleanup, leaving tool_called without tool_result on disk
- scenario A (effect not applied): new process recorded effect_unknown and paused for inspection; it did NOT replay the write; resolve_inspection('not_applied') executed it exactly once
- scenario B (effect applied, result lost): resolve_inspection('already_applied') verified the file digest and recorded the result without re-executing
- exactly one tool_result for the call id in both scenarios; no duplicate writes, no lost effects

## Live proof 1 — deterministic skill delivery (2026-09-22 20:10)

Backend: real 7B local model over HTTP (http://127.0.0.1:11434).

- turn status: contract_refused (wall 0.0s)
- skills routed by harness code: ['mission-definition', 'discovery']
- skill `mission-definition`: full body present in contract block, byte-identical to sha256-pinned `skills/mission-definition.md` (`4489d42b1955...`)
- skill `discovery`: full body present in contract block, byte-identical to sha256-pinned `skills/discovery.md` (`faec80d8278c...`)
- recompiled contract: SKILLS section byte-identical (deterministic)
- the model never fetched anything: skills arrived only via the injected block

## Live proof 2 — approval interrupt -> SIGKILL -> resume (2026-09-22)

- child: real subprocess, real 7B local model over HTTP; drove the loop to `awaiting_approval`, then SIGKILL (rc=-9, no cleanup)
- parent: fresh Loop over the same home dir — all state from disk (events.jsonl + snapshot.json)
- approval wait, pending approval id `ap-fcd41e5f`, and paused turn routing (t2) all survived the kill
- operator approved; `write_file INVENTORY.md` executed exactly once (no duplicate across the kill)
- event order across the kill: approval_requested(26) -> approval_granted(28) -> tool_called(29) -> tool_result(30)
- paused turn finalized after resume (turn_completed seq 36); criterion event:tool_called:write_file satisfied
- note: the 7B model defaulted to list_dir twice; it proposed the consequential write only after an explicit instruction
