# A.W.I.N.O. MCP server (standalone)

Client-agnostic tool server exposing the harness's real machinery to any
MCP-compatible client: mission intake, the contract compiler, turn
validators, the judge panel, and the skill-synthesis pipeline.

> **History.** This directory was `integrations/kilo/` — a selectable
> loop-owner agent for Kilo Code's agent picker plus this MCP server.
> **The Kilo agent-picker track was retired on 2026-09-23**, superseded by
> the A.W.I.N.O. VS Code extension, which provides the same loop-owner
> surface natively inside VS Code (proven live, 34/34 GUI checks green)
> with stronger enforcement than the discipline-grade Kilo agent ever had.
> The retired agent file is preserved under `agents/` for history.
> **The MCP server below is unaffected and remains the supported path**
> for non-VS-Code clients.

## Install

1. **Install the harness** (stdlib only, zero dependencies):
   ```sh
   git clone <your Lukematic/harness-awino remote>
   cd harness-awino && pip install .
   ```
   Verify: `cd prototype && ./run_tests.sh` (295 tests, all green).

2. **Register the MCP server.** Merge `mcp.json` into your client's MCP
   settings (the `mcpServers` object), replacing
   `<ABSOLUTE_PATH_TO_REPO>` with the repo's absolute path on your machine.

## Tools exposed

- `awino_new_mission` — start a mission (runs the discovery interview)
- `awino_compile_contract` — compile a mission turn contract
- `awino_validate_turn` — validate a turn against its contract
- `awino_judge_turn` — judge panel verdict (fail-closed)
- `awino_synthesize_learning` — learning → sandbox-verified, sha256-pinned skill

All tools run under the same contract and approval gates as `awino chat`.
Nothing here phones home: stdlib only, no network, no paid APIs.

## Honest caveats

- **MCP state is per-server-process.** If the client restarts the MCP
  server, in-flight missions are gone. For durable, resumable missions,
  use `awino chat` or the VS Code extension.
- **Judges are deterministic here** (fail-closed, no model calls): they
  catch forged done-claims, unoffered tools, and header forgery — not
  subtle reasoning failures.
