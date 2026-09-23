# A.W.I.N.O. × Kilo Code — selectable loop-owner agent

Switch into the A.W.I.N.O. loop-owner the way you switch into Plan or
Orchestrator mode: pick **awino-loop-owner** in Kilo's agent selector.
Behind it, a local MCP server (`awino-mcp`) gives the agent the harness's
real contract compiler, turn validators, judge panel, and skill-synthesis
pipeline.

## Install

1. **Install the harness** (stdlib only, zero dependencies):
   ```sh
   git clone <your Lukematic/harness-awino remote>
   cd harness-awino && pip install .
   ```
   Verify: `cd prototype && ./run_tests.sh` (205+ tests, all green).

2. **Install the agent file** — global (all projects) or project-local:
   ```sh
   # global
   mkdir -p ~/.config/kilo/agents
   cp integrations/kilo/agents/awino-loop-owner.md ~/.config/kilo/agents/
   # or project-local
   mkdir -p .kilo/agents
   cp integrations/kilo/agents/awino-loop-owner.md .kilo/agents/
   ```

3. **Register the MCP server.** Merge `integrations/kilo/mcp.json` into
   Kilo's MCP settings (the `mcpServers` object), replacing
   `<ABSOLUTE_PATH_TO_REPO>` with the repo's absolute path on your machine:
   ```json
   {"mcpServers": {"awino": {
     "type": "stdio",
     "command": "python3",
     "args": ["/abs/path/to/harness-awino/prototype/awino_mcp.py"],
     "alwaysAllow": ["awino_new_mission", "awino_compile_contract",
                     "awino_validate_turn", "awino_judge_turn",
                     "awino_synthesize_learning"],
     "disabled": false}}}
   ```
   `alwaysAllow` means the agent calls the harness tools without a
   per-call approval popup — the tools are read/validate-only against the
   harness's own state; they cannot touch your files.

4. **Export the skills** (optional but recommended):
   ```sh
   python3 prototype/export_kilo_skills.py        # -> ~/.kilo/skills/
   # or: python3 prototype/export_kilo_skills.py .kilo/skills
   ```
   This copies the hash-verified skill registry into Kilo's skill format.
   A tampered registry refuses to export.

5. **Restart Kilo Code**, then select the `awino-loop-owner` agent.

## How it works in practice

- New objective → the agent calls `awino_new_mission`, runs the discovery
  interview (one question per turn), then sets the mission with criteria.
- Every turn → `awino_compile_contract`, echo the header, draft the turn,
  `awino_validate_turn`, then `awino_judge_turn`. FAIL anywhere stops the turn.
- Completion only when the contract's criteria show verified — the same
  evidence rule as `awino chat`.
- Durable lessons get banked via `awino_synthesize_learning`; only
  sandbox-verified learnings become skills.

## HONEST CAVEATS — read before trusting this

1. **Kilo owns the loop, not the harness.** The agent-file + MCP tools give
   you the harness's real validators and judges, but the *discipline* is
   prompt-level: the agent calls the tools because its role prompt tells it
   to. A model that skips the calls is not stopped by code. The full
   no-bypass guarantee — turns that *cannot* reach a tool without passing
   the harness — exists only in `awino chat`, where the loop itself is code.
2. **MCP state is per-server-process.** Missions live in the `awino-mcp`
   server process's memory (event-sourced on disk under a temp dir). If
   Kilo restarts the MCP server, in-flight missions are gone — start a new
   one with `awino_new_mission`. For durable, resumable missions, use
   `awino chat`.
3. **Judges are deterministic here.** The MCP server wires the deterministic
   judge panel (fail-closed, no model calls). It catches forged done-claims,
   unoffered tools, and header forgery — not subtle reasoning failures.
4. **Agent frontmatter format** (`mode: primary`, `permission`, …) follows a
   community Kilo Code cheatsheet and the research available at build time.
   Verify against your Kilo Code version's agent docs if the agent doesn't
   appear in the selector.
5. Nothing here phones home: stdlib only, no network, no paid APIs. The
   model is whichever you select in Kilo.
