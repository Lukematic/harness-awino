# Kilo integration — proof transcript

Date: 2026-09-22. Driver: `proof/kilo_integration_proof.py`, which speaks
newline-delimited JSON-RPC to `prototype/awino_mcp.py` exactly like a Kilo
Code MCP client would. Real output below (mission ids vary per run).

```
== 1. handshake ==
  server: {'name': 'awino-mcp', 'version': '0.1.0'}
  [PASS] initialize ok
  tools: ['awino_new_mission', 'awino_compile_contract', 'awino_validate_turn',
          'awino_judge_turn', 'awino_synthesize_learning']
  [PASS] five tools listed
== 2. new mission (discovery interview) ==
  mission_id: mcp-c8cb9458
  interview open: True
  contract header: [A.W.I.N.O. | phase: IDLE | mode: observe | stance: advisor | skills:  | loop: 1...
  [PASS] mission created
  [PASS] interview framing present
== 3. compile contract ==
  [PASS] contract compiles
== 4. validate honest turn ==
  ok=True reasons=[]
  [PASS] honest turn validates
== 5. validate forged done-claim (must refuse) ==
  ok=False reasons=['done_claim with unverified criteria (forgery): no mission set']
  [PASS] forged done-claim refused
== 6. judge hostile turn (must FAIL) ==
  verdict=FAIL votes=[('DeterministicJudge#0', 'FAIL')]
  [PASS] hostile turn FAILs
== 7. synthesize real learning (must admit) ==
  status=admitted name=auto-note-echo-works-verify-run-command-echo-abc-s
  [PASS] real learning admitted
== 8. synthesize injected learning (must refuse) ==
  status=refused code=injection
  [PASS] injected learning refused

ALL PROOF CHECKS PASSED
```

## What this proves

- The MCP server completes a real JSON-RPC handshake and lists the five tools.
- `awino_new_mission` creates a mission in the harness's real `Loop` (real
  event-sourced state, real skill-store verification at startup) and returns
  the harness-compiled contract block plus the planning-grill interview framing.
- `awino_validate_turn` runs the real pipeline validators
  (`validate_schema` → `Loop.validate_semantics` → `check_pre_execute`):
  an honest turn passes; a forged `done_claim` is refused with a named reason.
- `awino_judge_turn` runs the real deterministic judge panel: a hostile
  done-claim against unmet criteria FAILs.
- `awino_synthesize_learning` runs the real synthesis pipeline in a temp
  sandbox + temp registry: a verifiable learning is admitted and hash-pinned;
  an injected learning is refused with code `injection`.
- Malformed JSON-RPC (`{not valid json}` → error -32700), unknown methods
  (-32601), unknown tools, and unknown mission ids are all handled without
  the server crashing (covered in `prototype/tests/test_mcp.py`).

## Honest gaps (also in integrations/kilo/README.md)

- Kilo Code owns the actual turn loop. The discipline is prompt-level: the
  agent calls the MCP tools because its role prompt says so. Code-level
  no-bypass exists only in `awino chat`.
- MCP missions live in the server process's memory; a server restart loses
  in-flight missions (state is event-sourced under a temp dir, not the
  user's project home).
- The MCP judge panel is deterministic-only (no model judges) — it catches
  forged done-claims, unoffered tools, and header forgery, not subtle
  reasoning failures.
- The Kilo agent frontmatter format (`mode: primary`, etc.) follows a
  community cheatsheet; verify against the user's Kilo Code version if the
  agent doesn't appear in the selector. The integration was not tested inside
  a live Kilo Code client — only against the MCP stdio protocol, which is
  what Kilo speaks.
