# Awino VS Code extension

The VS Code surface for the Awino harness: mission chat, stories, approvals,
and side views for the contract, journal, skills and more.

The extension does not run the agent loop itself. It starts the Python
sidecar (`prototype/awino_sidecar.py`), which owns every turn. The extension
shows events and forwards your input and approvals. It cannot bypass the
harness.

**New user?** Read [docs/GETTING_STARTED.md](../../../docs/GETTING_STARTED.md)
first. It covers install, connecting a model and your first mission.

## Install (beta)

Download the `.vsix` from the
[releases page](https://github.com/Lukematic/harness-awino/releases), then:

```bash
code --install-extension <downloaded-file>.vsix
```

or in VS Code: Extensions view → `…` → *Install from VSIX…*.

A Python runtime ships inside the package for Windows x64, Linux x64 and
Apple silicon Macs. On an Intel Mac, install Python 3.10+ and make sure
`python3` is on PATH, or set `awino.pythonPath`.

## First run

1. Open a folder.
2. Open the chat (Awino icon in the activity bar). The setup card walks you
   through provider → key → model. You can also run
   **Awino: Models & Providers**.
3. For Anthropic, OpenAI-compatible or Bedrock API keys, run
   **Awino: Set API Key**. Ollama needs no key. For AWS, run
   **Awino: Set Up AWS Bedrock**.
4. Run **Awino: Reconnect Sidecar** if you changed settings by hand.
5. Type your goal in the chat.

Until you pick a provider you are on `echo`. Echo is a demo that returns
canned replies. It does no real work.

## Commands

All commands are in the Command Palette. Titles match `package.json`.

### Missions and contract

| Command | What it does |
|---|---|
| `Awino: New Mission` | Start a mission: objective, then done criteria. |
| `Awino: New Mission from Seed` | Start a mission from a saved seed (template). |
| `Awino: Save Current Mission as Seed` | Save the current mission as a reusable seed. |
| `Awino: Inspect Contract` | Open the current turn contract as a read-only document. |
| `Awino: Approve Contract` | Approve the plan and move to the next phase. Optionally give a file scope (comma-separated paths) to allow writes. |
| `Awino: Show Session Resume` | Show where the session left off in the chat. |
| `Awino: Show Rigor Report` | Show the rigor score for the latest mission, from journal evidence. |

### Stories

| Command | What it does |
|---|---|
| `Awino: Start Story` | Start a story: title, type (story, spike, chore), problem. |
| `Awino: Work on This Story` | Make a story the one this session works on. |
| `Awino: Close Story (to Brag Board)` | Close a story with an outcome. Writes its receipt. |
| `Awino: Show Story Receipt` | Show the receipt for a closed story. |
| `Awino: Refresh Stories` | Reload the Stories view. |

### Providers and keys

| Command | What it does |
|---|---|
| `Awino: Models & Providers` | Open the panel: provider, endpoint, model, timeout, keys, environments. |
| `Awino: Set API Key` | Store a key for OpenAI-compatible, Anthropic or Bedrock in SecretStorage. |
| `Awino: Clear API Key` | Remove a stored key. |
| `Awino: Set Up AWS Bedrock` | Guided setup: region → auth → key → connection test → model. |
| `Awino: Import connections from other tools` | Scan Claude Code, Kilo CLI and project `.env` files for endpoints and models. You choose what to import. Secrets are never copied. |
| `Awino: Switch Environment` | Switch to another environment from `.awino/providers.yaml`. |
| `Awino: Reconnect Sidecar` | Restart the sidecar, for example after changing providers. |

### Modes, personas, skills and context

| Command | What it does |
|---|---|
| `Awino: Invoke Mode` | Switch to a working mode (see Glossary). |
| `Awino: Dismiss Mode (back to stage default)` | Return to the phase's default mode. |
| `Awino: Assume Skill as Persona` | Use a verified skill as a lens for the next turns. It never adds permissions. |
| `Awino: Dismiss Persona` | Stop using the persona. |
| `Awino: Add Skill` | Add a skill file to the project. It is verified and hash-pinned first. |
| `Awino: Add Context File` | Add a named context file that is shown to the model. |
| `Awino: Remove Context File` | Remove a context file. |

### Project and recovery

| Command | What it does |
|---|---|
| `Awino: Set Up Project (justfile, .gitignore, …)` | Offer the setup chores (justfile, `.gitignore` lines, `.env.example`, `.editorconfig`). Nothing is written without a yes. |
| `Awino: Doctor Project` | Check the project for common problems and offer fixes. Each fix needs your approval. |
| `Awino: Housekeep Project` | Archive stale files and write the project manifest. |
| `Awino: Revert Last Checkpoint (undo Awino's writes)` | Restore the workspace to the checkpoint taken before Awino's last write batch. Your own uncommitted changes are kept. |
| `Awino: Rollback` | Roll back journaled effects at or after a sequence number. |
| `Awino: Refresh Views` | Reload all side views. |
| `Awino: Reset Onboarding` | Show the setup wizard again. Keys and settings are not touched. |

## Settings

| Setting | Default | What it does |
|---|---|---|
| `awino.provider` | `echo` | `echo` (demo), `ollama`, `openai` (any OpenAI-compatible endpoint), `anthropic`, `bedrock`. |
| `awino.endpoint` | empty | Base URL. Empty uses the provider default. For Bedrock it overrides the URL derived from the region. |
| `awino.model` | empty | Model name. Empty uses the provider default. |
| `awino.timeout` | `180` | Model HTTP timeout in seconds. |
| `awino.bedrockRegion` | empty | AWS region for Bedrock. The endpoint is derived from it. |
| `awino.bedrockAuthMode` | `api-key` | `api-key` (Bedrock API key from SecretStorage) or `aws-profile` (SigV4 from your AWS credentials). |
| `awino.bedrockAwsProfile` | empty | AWS profile name for `aws-profile` mode. |
| `awino.autoApproveAfterPlan` | `true` | After you approve a plan and its files, writes to those files and commands that stay in the workspace run without a card. Risky commands still ask (see Approvals). |
| `awino.mcpServers` | `[]` | MCP servers the sidecar starts over stdio: `{name, command, args?, env?}`. Their tools go through the same contract and approvals. |
| `awino.keyLabels` | `{}` | Friendly names for your keys, e.g. `{"openai": "Work"}`. Not secret. |
| `awino.pythonPath` | `python3` | Python used to start the sidecar. If you set it, it is used instead of the bundled runtime. If not, Awino uses the bundled runtime, then looks for Python on PATH. |
| `awino.script` | `null` | Test only. Scripted turns for the `scripted` provider. |

API keys are never settings. They live in VS Code SecretStorage and reach
the sidecar through environment variables only.

The sidecar also reads `AWINO_MAX_TOKENS` from the environment (default
8,192 output tokens).

## Views

The Awino activity bar has these views:

- **Mission Chat**: the conversation, approval cards, receipts.
- **Stories**: open stories and the brag board. Inline buttons: work on,
  close, show receipt.
- **Tasks**: the task list from the plan. Read-only; tasks close only with
  evidence.
- **Modes**: the working modes, with the active one marked.
- **Contract**, **Journal**, **Skills**, **Learnings**, **Context**: what the
  harness is enforcing, what happened, and what it knows.

## Stories, receipts and lessons

A **story** is the one piece of work this session is about: a problem, a
plan, done criteria and a branch. Awino will not move to BUILD until the
story has a problem, an approach and done criteria. The model fills these
in during planning with the `story_plan` tool.

When the verifier passes, Awino asks whether to close the story. Closing is
your call. A closed story moves to the **brag board** (task, date closed,
result and proof label) with a **receipt**:
what was promised, what proved it (commands and exit codes, the verifier's
verdict, files, commits, the journal hash) and forecast vs actual time. A
receipt is labelled PROVEN, PARTLY PROVEN, SELF-CHECKED or UNVERIFIED. The
receipt card has **Copy as PR description**.

Receipts produce **lessons**, for example "this step took 3× its forecast"
or "a check failed before passing". Lessons are shown to the model on every
turn and listed in the chat when a session starts. After three clean
closes a lesson is marked learned and dropped.

Files: `.awino/registry/stories.json`, `.awino/registry/receipts/`,
`.awino/lessons/lessons.json`. `STORY.md` in the project root is generated
from the registry; don't edit it by hand.

## Approvals

Any write or command with side effects shows an approval card in the chat.
The card is the only approval path.

- `write_file` and `patch_file` cards show a unified diff. Choose
  **Approve**, **View diff**, **Always allow** (this tool, this session) or
  **Deny**.
- `run_command` cards show the working directory and the absolute paths the
  command touches, and flag paths outside the workspace. Nothing is blocked
  by a list; you decide.
- Once decided, the card shows "Approved ✓" or "Denied".

**Autopilot after plan approval.** With `awino.autoApproveAfterPlan` on (the
default), approving the plan with a file list also approves, for this
mission: writes to those files, and commands that stay inside the
workspace. Each one is journaled as `auto_approved` with the reason. These
always still ask: deleting (`rm`, `del`, …), `sudo`, force flags, history
and branch changes (`git push`, `reset`, `clean`, `checkout`, …), package
installs and publishes, network tools (`curl`, `wget`, `ssh`), `docker`,
inline code (`-c`, `-e`), redirects into files, shell expansion, and
anything outside the workspace. Autopilot turns off when a new mission
starts or the scope changes. The rules are in
`prototype/approval_targets.py` (`destructive_reason`).

Context compaction (at about 85% of the context window) asks with a modal
dialog. Denial is journaled.

## AWS Bedrock

The `bedrock` provider uses Bedrock's OpenAI-compatible endpoint:
`https://bedrock-runtime.{region}.amazonaws.com/openai/v1`.

Two auth modes:

- **Bedrock API key** (`awino.bedrockAuthMode: "api-key"`, default). The key
  is sent as a bearer token.
- **AWS profile / SSO** (`"aws-profile"` plus `awino.bedrockAwsProfile`).
  The sidecar signs each request with SigV4, using only the Python standard
  library. Credentials come from environment variables, `~/.aws/credentials`,
  `~/.aws/config` (including `source_profile`) and the SSO token cache. For
  SSO, run `aws sso login --profile <name>` first. Problems stop the request
  with a named error such as `AWS_SSO_TOKEN_EXPIRED` or
  `AWS_PROFILE_NOT_FOUND`.

The model field takes a model id (`anthropic.claude-…`), an inference-profile
id (`us.anthropic.claude-…`) or a full ARN (foundation model, system or
application inference profile, provisioned model). Bad ARNs are rejected
before anything is sent.

Limit: signatures are checked against botocore in the tests, but no live
Bedrock call has been made through the profile path yet.

## Importing connections from other tools

**Awino: Import connections from other tools** copies connection details you
already have, so you don't retype them. Nothing is read until you agree, and
nothing is written until you tick findings and confirm.

It looks at:

- `~/.claude/settings.json`, `~/.claude/settings.local.json` and
  `<project>/.claude/`: AWS profile, region, model or ARN.
- `<project>/.env`, `<project>/.env.local`: regions, base URLs, model ids.
- Kilo CLI configs: `~/.config/kilo/kilo.jsonc`, `~/.config/kilo/opencode.json`,
  `<project>/.kilo/kilo.jsonc`, `<project>/kilo.jsonc`.

Secrets are never imported. A value that looks like a secret becomes a note
telling you to enter it yourself. The Kilo VS Code extension stores its
settings inside VS Code, which Awino does not read. Kilo CLI's `auth.json`
is never read.

## Troubleshooting

| You see | Do this |
|---|---|
| Replies are canned or mention echo | You are on the echo demo. Pick a provider (First run). |
| "not connected" / the sidecar won't start | Check the **Awino** output channel. If Python is missing, the error offers **Locate Python...**; pick a Python 3.10+ or set `awino.pythonPath`. |
| `HTTP 401` or "unauthorized" | Run **Awino: Set API Key**, then **Awino: Reconnect Sidecar**. |
| `AWS_SSO_TOKEN_EXPIRED` | Run `aws sso login --profile <name>`, then reconnect. |
| BUILD is refused | The story needs a problem, an approach and done criteria. Finish planning first. |
| A write is refused | Approve the contract with a file scope that includes that file. |
| VERIFY keeps sending work back to BUILD | Your project's `test` or `lint` recipe fails. The chat shows its output. |
| "The last session stopped in the middle of write_file …" | Check the file, then reply `applied` or `not applied`. |

## Glossary

| Word | Meaning |
|---|---|
| **Phase** | Where the mission is: DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP. Each move has a gate checked in code. Older docs and the turn header call this the "floor". |
| **Permission level** | What tools the model is offered: observe, plan, build, verify or ship. Chosen in code each turn and capped by the phase; the model never picks it. The contract calls this the "mode". |
| **Mode** | In the Modes view: a working style such as Interview, Planner, Architect, Code, Debug engineer, Test, Review, Storyboard, Tutor or Release. Each phase has a default. A mode never widens permissions. |
| **Stance** | How the model reasons this turn, with a rubric: steel-man, feynman, planning-grill, first-principles, premortem, devil's-advocate, advisor, triage, verifier. The model picks one the phase allows and says why. |
| **Floor** | The check that always runs in a phase, whatever stance the model picks: first-principles in PLAN and BUILD, devil's-advocate in VERIFY, premortem in REVIEW and SHIP. |
| **Skill** | Knowledge injected into the contract, such as a TDD or debugging procedure. Each skill file is SHA-256 pinned and checked at load. |
| **Honda / Bugatti** | Honda: the simplest plan that works, built first. Bugatti: the ambitious version, pitched briefly and never built unless you ask. |
| **Story** | One piece of work: problem, plan, done criteria, branch, time spent. |
| **Brag board** | The list of closed stories: task, date closed, result and proof label. |
| **Receipt** | Promise → proof → lesson for a closed story. Copies as a PR description. |
| **Lesson** | Something a receipt taught. Shown to the model every turn until learned. |
| **Approval** | A card in the chat asking before any write or command with side effects. After plan approval, safe actions inside the plan's files run without a card (autopilot). |

## Layout

| Path | What |
|---|---|
| `package.json` | Manifest: views, commands, `awino.*` settings. |
| `src/` | TypeScript for the extension host. See [src/README.md](src/README.md). |
| `webview/` | Plain HTML and JS for the chat and the Models & Providers panel. No build step. See [webview/README.md](webview/README.md). |
| `test/` | Node tests, run by `npm test`. See [test/README.md](test/README.md). |
| `scripts/bundle-sidecar.js` | Copies `prototype/` into `bundled-sidecar/` at package time. |
| `scripts/fetch-python-runtimes.js` | Downloads and hash-checks the bundled Python runtimes into `python/<platform>/`. |
| `resources/awino.svg` | Activity bar icon. |
| `RELEASING.md` | How a beta `.vsix` is cut and published to GitHub Releases. |

## Build and test

```bash
npm ci                     # dev dependencies only (TypeScript, @types)
npm run compile            # tsc; must compile clean
npm test                   # all node tests (needs python3 for the sidecar tests)
npm run vscode:prepublish  # compile + bundle the sidecar + fetch Python runtimes
npx @vscode/vsce package   # build the .vsix
```

## Known gaps

- No mission has run against a live model yet. That is the release gate.
- Marketplace publishing is not set up. Betas ship as GitHub pre-releases;
  see [RELEASING.md](RELEASING.md).
- The GUI was tested in real VS Code under Xvfb (Linux) and on Windows CI.
  See `proof/vscode_live_gui_test.md` and `.github/workflows/windows-gui-test.yml`.
