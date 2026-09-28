# Harness Awino

[![Download beta VSIX](https://img.shields.io/badge/download-beta%20VSIX-blue)](https://github.com/Lukematic/harness-awino/releases)

Awino is a coding agent that has to prove its work. The harness, not the
model, runs the loop. Each turn it builds a contract from saved state
(objective, mission, allowed tools, progress), checks the model's reply
against it, runs a judge panel, asks you before any write, and marks work
done only when there is evidence.

**New here? Start with [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).**
It covers install, connecting a model, your first mission, receipts,
lessons and the words you'll see.

Two parts live here:

- **`prototype/`**: the harness engine. Python standard library only. See
  [prototype/README.md](prototype/README.md).
- **`integrations/vscode/extension/`**: the VS Code extension. Chat,
  stories, approvals, side views. It talks to the engine through the Python
  sidecar and cannot bypass it. See
  [its README](integrations/vscode/extension/README.md) for commands,
  settings, troubleshooting and a glossary.

## Install the extension (beta)

Tagged pre-releases on the
[releases page](https://github.com/Lukematic/harness-awino/releases) carry
the `.vsix`. This downloads and installs the newest one:

```sh
VSIX_URL=$(curl -s https://api.github.com/repos/Lukematic/harness-awino/releases | python3 -c "
import json,sys
for r in json.load(sys.stdin):
    for a in r.get('assets', []):
        if a['name'].endswith('.vsix'):
            print(a['browser_download_url']); sys.exit()
print('NO_VSIX_FOUND'); sys.exit(1)
") && curl -sSL -o awino-beta.vsix "$VSIX_URL" && code --install-extension awino-beta.vsix --force
```

Or in VS Code: Extensions view → `…` → *Install from VSIX…*.

A Python runtime ships inside the package for Windows x64, Linux x64 and
Apple silicon. On an Intel Mac, install Python 3.10+ first.

After installing, connect a model. Until you do, Awino runs on `echo`, a
demo that returns canned replies. See
[Getting started, step 2](docs/GETTING_STARTED.md#2-connect-a-model-required).

## Run the tests

```sh
just test    # engine + extension tests (or run the two lines below)
just lint    # ruff on the engine; zero findings is the bar

cd prototype && ./run_tests.sh                   # engine: about 1,000 tests, under 2 minutes
cd integrations/vscode/extension && npm ci && npm run compile && npm test
```

Set `TMPDIR` to a folder with free space if `/tmp` is small. CI runs the
same checks on every push (`.github/workflows/ci.yml`).

## What's in the harness

| Piece | Where | What it does |
|---|---|---|
| Turn loop | `prototype/loop.py`, `contract_loop.py` | Builds and checks the turn contract before the model runs and again before any tool runs. Broken contracts are refused with a named reason. |
| Permission levels (5) | `prototype/contract.py` (`MODES`) | observe, plan, build, verify, ship. Decide which tools the model is offered. Chosen in code, capped by the phase. |
| Stances (9) | `prototype/stances.py` | Reasoning procedures with rubrics: steel-man, feynman, planning-grill, first-principles, premortem, devil's-advocate, advisor, triage, verifier. The model picks one the phase allows and says why. The phase's floor check always runs too (PLAN/BUILD first-principles, VERIFY devil's-advocate, REVIEW/SHIP premortem). |
| Skills (53) | `prototype/skills/` | Knowledge injected into the contract. Each file is SHA-256 pinned and checked at load. |
| Judge panel | `prototype/judges.py` | Several judges grade every turn. The turn passes only with a quorum (majority) of PASS votes. A crashed judge's vote counts as FAIL, and a tie fails. A failed verdict blocks the turn. |
| Approvals | `prototype/loop.py` | A write or command with side effects waits for your approval. The approval is bound to the exact arguments, mission revision and file scope; a scope change voids it. |
| Mission loop | `prototype/loop.py` | A write moves BUILD → VERIFY. The harness runs the project's `test` and `lint` recipes; a failure goes back to BUILD, a pass starts an independent verifier. Three identical failures force a rethink. Proven in `tests/test_e2e_mission.py`. |
| Stories and brag board | `prototype/story.py` | One story per piece of work: problem, plan, done criteria, branch, time spent. Closed stories go to the brag board. |
| Receipts | `prototype/receipt.py` | On close: what was promised, what proved it, forecast vs actual time. Copies as a PR description. |
| Lessons | `prototype/lessons.py` | Problems found in receipts become lessons shown to the model every turn until learned. |
| Setup offers | `prototype/setup_autopilot.py` | Proposes a justfile, `.gitignore` lines, `.env.example` and `.editorconfig`. Writes only what you approve. |
| Crash recovery | `prototype/state.py`, `loop.py` | The journal is hash-chained. After a crash, reads re-run and interrupted writes ask you "applied / not applied". A corrupt journal line is set aside, not fatal. |
| Tools | `prototype/tools.py` | `read_file`, `write_file`, `patch_file`, `list_dir`, `run_command`, `search_files`, `find_symbol`, `git_status`, `git_diff`, `diagnostics`, plus harness tools for tasks, stories and completion. |
| Secret redaction | `prototype/secret_redaction.py` | Known credential shapes are redacted before anything is written to the journal or the sidecar log. |
| Providers | `prototype/awino_sidecar.py`, `backends.py` | OpenAI-compatible, Anthropic, Bedrock (API key or SigV4 from your AWS profile), Ollama, and the echo demo. |
| Skill synthesis | `prototype/synthesis.py` | Turns a learning into a skill only after it passes in a sandbox. Admitted skills can be used as personas; they are not routed automatically. |
| MCP server | `prototype/awino_mcp.py`, `integrations/mcp-server/` | Contract compiler, turn validator, judge panel and skill synthesis for any MCP client. |
| Claude Code integration | `integrations/claude/` | Skill, subagent, `/awino` command and a PreToolUse gate hook. |

Project data lives in `<project>/.awino/`, shared by the CLI and the
extension. Full inventory: [CAPABILITY_REGISTRY.md](CAPABILITY_REGISTRY.md).

## Docs

For users:

- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md): install, first mission, troubleshooting.
- [integrations/vscode/extension/README.md](integrations/vscode/extension/README.md): commands, settings, glossary.
- [CHANGELOG.md](CHANGELOG.md): what changed, by release.

For contributors:

- [ARCHITECTURE.md](ARCHITECTURE.md): the system as built.
- [CAPABILITY_REGISTRY.md](CAPABILITY_REGISTRY.md): every permission level, stance, skill and route.
- [AUTHORING_TEMPLATE.md](AUTHORING_TEMPLATE.md): how to add a stance, skill or mode, with its tests.
- [docs/](docs/README.md): adapter contract, skill admission, testing notes, reviews, and `history/` (old specs and plans).
- [prototype/README.md](prototype/README.md): engine modules and how to run them.
- [integrations/vscode/extension/RELEASING.md](integrations/vscode/extension/RELEASING.md): how a beta `.vsix` is cut.
- [proof/](proof/README.md): scripts and transcripts that show the harness blocking attacks and running missions.

## Status

`main` carries 0.7.0: the full mission loop (write → test → repair →
verify → ship), model-chosen stances within phase limits, stories, receipts
and lessons, setup offers, crash recovery, and one project memory shared by
the CLI and the extension. The 0.7 plan and its audit are in
[docs/history/BUILD_PLAN_v0.7.md](docs/history/BUILD_PLAN_v0.7.md); the
latest review is [docs/TEAM_REVIEW_2026-09-27.md](docs/TEAM_REVIEW_2026-09-27.md).

The `.vsix` installs and passes the GUI proof in real VS Code on Windows
CI. It has not yet run a mission against a live model; that is the release
gate. Betas ship from `vsix-v*` tags.

"Awino" is a working name, not the final brand.
