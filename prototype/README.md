# Harness engine (developer notes)

The Awino engine: the turn loop, its gates, state, stories, receipts and
lessons. Plain Python 3.10+, standard library only, no dependencies.

Users start at [../docs/GETTING_STARTED.md](../docs/GETTING_STARTED.md).
The VS Code extension runs this code through `awino_sidecar.py`.

## Run

```bash
cd prototype
./run_tests.sh                                   # full suite (~1,000 tests, under 2 minutes)
python3 -m unittest discover -s tests -t .       # same, without the temp-dir sweep
ruff check --exclude tests .                     # lint; zero findings is the bar
python3 cli.py --help                            # the `awino` command
python3 chat.py                                  # interactive REPL on the echo demo
```

`pip install ./prototype` installs the `awino` command (`cli:main`). Set
`TMPDIR` to a roomy folder if `/tmp` is small: the sidecar tests create
temp workspaces.

The REPL talks to echo (default, canned replies) or a local Ollama model:
`AWINO_BACKEND=local OLLAMA_MODEL=qwen2.5:7b python3 chat.py`
(`OLLAMA_HOST` sets the address; default model `qwen2.5:1.5b`). Hosted
providers are reached only through the sidecar, which reads
`AWINO_API_KEY`, `ANTHROPIC_API_KEY`, `AWINO_ENDPOINT`, `AWINO_MODEL`,
`AWINO_TIMEOUT` and `AWINO_MAX_TOKENS` (default 8,192) from its
environment.

## Modules

### The loop

| File | What it does |
|---|---|
| `loop.py` | The owned turn loop. Builds the contract each turn, validates the model's reply, runs the judges, gates tools by permission level and approval, moves the mission through its phases, runs the repair loop (BUILD ↔ VERIFY), fan-out workers, crash recovery and the session autopilot. |
| `contract.py` | The turn contract: schema, permission levels (`MODES`), skill loading, done-criteria checks, and the contract text the model sees each turn. |
| `contract_loop.py` | The pre-turn and pre-execute contract checks, with named refusal reasons. |
| `stances.py` | Stances and their rubrics, the phase floor table, and the per-turn router for permission level, stance and skills. |
| `judges.py` | The judge panel: several judges, quorum of PASS votes, errors count as FAIL. |
| `verify.py` | The independent verifier's verdict: needed evidence per criterion, role-aware checklists. |
| `floor_checks.py` | Mechanical checks that used to be prompt text, including the SHIP scan for stubs and secrets in the change. |
| `rigor.py` | The rigor coach: scores a finished mission from journal evidence (`awino rigor`, **Awino: Show Rigor Report**). |
| `compaction.py` | Context compaction at ~85% of the window. State is kept; old conversation is summarized. |
| `hooks.py` | User hooks on loop events (turn start and end, tool results, approvals, mission complete). |
| `cancel.py` | Cooperative cancellation (Stop in the chat). |

### Models and tools

| File | What it does |
|---|---|
| `backends.py` | Model backends: echo (demo), scripted and hostile (tests), scripted judge, and Ollama. The hosted-provider backends (OpenAI-compatible, Bedrock SigV4, Anthropic) subclass the Ollama one in `awino_sidecar.py`. |
| `provider_tools.py` | Converts tool definitions and tool calls between the harness format and each provider's native format. |
| `tool_schema.py` | JSON schemas for every tool, and the tool catalog shown to the model. |
| `tools.py` | The sandboxed tools: `read_file`, `write_file`, `patch_file`, `list_dir`, `run_command`, `search_files`, `find_symbol`, `git_status`, `git_diff`, `diagnostics`. Paths cannot leave the workspace. |
| `approval_targets.py` | For `run_command` approval cards: the absolute paths a command touches and whether any are outside the workspace. Also `destructive_reason`, which decides what the autopilot may run without asking. |
| `secret_redaction.py` | Redacts known credential shapes before anything reaches the journal or the sidecar log. |

### State and memory

| File | What it does |
|---|---|
| `state.py` | Event-sourced project state: an append-only, hash-chained `events.jsonl` plus a derived snapshot. Thread-safe. Repairs a corrupt tail by keeping the valid prefix. |
| `registry.py` | `.awino/registry/`: milestones, breadcrumbs and the task list (a DAG with evidence links). |
| `story.py` | Stories and the brag board. The registry is the source of truth; `STORY.md` is rendered from it. `story_plan` writes the six-part plan; the BUILD gate needs problem, approach and done criteria. |
| `receipt.py` | Builds the receipt when a story closes: promise, proof, lesson, and a proof label. |
| `lessons.py` | Turns receipts into lessons, shows them in every contract, escalates repeats, retires learned ones. Stored in `.awino/lessons/lessons.json`. |
| `synthesis.py` | Turns a learning into a skill only after it passes in a sandbox, then pins and admits it. |
| `memory_store.py` | A local JSONL memory store. Not wired into the loop; see the note below. |

### Project setup

| File | What it does |
|---|---|
| `bootstrap.py` | `awino init` and chat auto-init: startup checklist, `.venv`, justfile, `.awino/project.yaml`, scaffold folders. |
| `setup_autopilot.py` | The setup offers in the extension: justfile, `.gitignore` lines, `.env.example`, `.editorconfig`. Nothing is written without consent; "never" is remembered. |
| `modes.py` | Role lenses (software engineer, AI researcher, AI architect, forward-deployed engineer, security engineer) and their router. They change what is routed into the contract, never permissions. |

### Entry points

| File | What it does |
|---|---|
| `cli.py` | The `awino` command: `chat`, `init`, `status`, `plan`, `stories`, `rigor`. |
| `chat.py` | The REPL. `/help` lists commands (`/mission`, `/approve`, `/approve-contract`, `/done`, `/story-start`, `/story-close`, …). |
| `awino_sidecar.py` | The headless process the VS Code extension starts. JSON lines on stdin and stdout. Owns provider calls (OpenAI-compatible, Anthropic, Bedrock with SigV4, Ollama), MCP servers, and every extension command. |
| `awino_mcp.py` | MCP server over stdio: mission intake, contract compiler, turn validator, judge panel, skill synthesis. See `../integrations/mcp-server/`. |
| `awino_gate.py` | Claude Code PreToolUse hook. See `../integrations/claude/`. |
| `export_kilo_skills.py` | Exports the pinned skills to Kilo Code format: `python3 export_kilo_skills.py [target_dir]` (default `~/.kilo/skills`). |

### Folders

- `skills/`: the 53 pinned skills and their manifest. See [skills/README.md](skills/README.md).
- `tests/`: the test suite. See [tests/README.md](tests/README.md).
- `docs/`: story planning and git rules the harness follows
  ([STORY_PLANNING.md](docs/STORY_PLANNING.md),
  [STORY_GIT_RULES.md](docs/STORY_GIT_RULES.md)). `tests/test_story.py`
  reads `STORY_PLANNING.md`, so it stays here.

## What `awino init` writes

In the target folder, only if missing: `.awino/` (with `project.yaml` and
`registry/`), `.venv/`, `justfile`, `README.md`, `lessons.md`, `spec/`,
`docs/research/` and `docs/archive/`. Existing files are kept. `STORY.md`
appears once a story starts. The VS Code extension is stricter: it creates
only `.awino/` and asks before any other file.

`lessons.md` is a free-form notes file for you. It is not the lessons
store; that is `.awino/lessons/lessons.json`.

## What the tests prove

With scripted and hostile model backends, the suite shows the mechanism
holds whatever the model does:

- The contract is sent on every model call, and the reply must echo the
  turn header exactly.
- Bad replies are rejected, retried a bounded number of times, then sent to
  you. A bad turn never completes a mission.
- `done_claim` with unproven criteria never ends the mission.
- The judge panel runs on every validated turn; FAIL blocks it.
- Consequential tools need an approval bound to the arguments, the mission
  revision and the scope. Stale approvals are refused; denials run nothing.
- A crash mid-effect recovers without duplicate writes or lost approvals.
- Projects stay isolated; missions survive restarts.
- PLAN → BUILD needs an approved contract; BUILD needs a planned story.
- VERIFY runs the project's own `test` and `lint` recipes.

## Known dead end

`memory_store.py` has tests but nothing in the loop calls it. The
`durable-memory` skill (routed on DEFINE turns) still tells the model to
use its `store` / `recall` / `search`, which no tool exposes. Either expose
it as tools or rewrite that skill and delete the module.
