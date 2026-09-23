# A.W.I.N.O. VS Code Extension

The agent IDE surface for A.W.I.N.O. — Kilo-class UX, harness-grade engine.

## What this is

A VS Code extension that puts the loop-owner harness inside the editor:
chat, contract view, journal, skills, context, modes. The **Python sidecar**
(`prototype/awino_sidecar.py`) owns the turn loop in code — the extension
is a surface and cannot bypass the harness.

## Layout

| Path | What |
|------|------|
| `package.json` | Extension manifest: views, commands, `awino.*` settings. Publisher placeholder `lukematic`. |
| `tsconfig.json` | Strict TS config. `tsc` must compile clean. |
| `src/sidecar.ts` | **vscode-free** sidecar client (JSON-lines protocol). Exercised headless by `test/harness.js`. Prefers the **bundled sidecar** (`bundled-sidecar/awino_sidecar.py` inside the `.vsix`); falls back to repo-relative path for development. |
| `scripts/bundle-sidecar.js` | Copies the Python sidecar into the `.vsix` at package time (`vscode:prepublish`). |
| `src/views.ts` | Tree data providers: Contract, Journal, Learnings, Skills, Context, Modes. |
| `src/extension.ts` | Activation: spawns sidecar, routes §4 events, commands, status bar, doctor, models panel. |
| `webview/chat.html` + `chat.js` | Mission chat. Plain JS, no build step. Renders every §4 event; approval cards with diffs. |
| `webview/models.html` + `models.js` | Models & Providers panel: provider/endpoint/model/timeout, environment switcher, keys via SecretStorage. |
| `resources/awino.svg` | Activity bar icon. |
| `test/harness.js` | Node harness: imports compiled `out/sidecar.js`, spawns the real sidecar, asserts hello→ready, turn_result, approval round-trip (approve + deny), fail-closed on unknown commands. |

## Commands

| Command | What |
|---------|------|
| `A.W.I.N.O.: New Mission` | Start a mission (objective + done criteria). |
| `A.W.I.N.O.: Approve Contract` | Approve DEFINE→PLAN or PLAN→BUILD (with optional scope). |
| `A.W.I.N.O.: Reconnect Sidecar` | Restart the sidecar (e.g. after changing providers). |
| `A.W.I.N.O.: Open Models & Providers` | Configure backends (OpenAI/Anthropic/Bedrock/Ollama/MCP). |
| `A.W.I.N.O.: Set Up AWS Bedrock` | Guided wizard: region → API key → connection test → model. |
| `A.W.I.N.O.: Import connections from other tools` | Permission-first scan of Claude Code / Kilo CLI / project `.env` configs; you tick what to import, confirm, and only then is it applied. Also offered once on first run. |
| `A.W.I.N.O.: Run Doctor` | Check sidecar health. |

## AWS Bedrock

The `bedrock` provider connects through Bedrock's OpenAI-compatible endpoint —
no native Bedrock client, no new sidecar backend: the sidecar speaks the OpenAI
chat-completions protocol to `https://bedrock-runtime.{region}.amazonaws.com/openai/v1`
with the Bedrock API key as the bearer token.

- **Setup:** run `A.W.I.N.O.: Set Up AWS Bedrock` (or the panel's
  *Bedrock region* + *Bedrock API key* fields). The endpoint is derived from
  the region; setting `awino.endpoint` explicitly overrides it.
- **Model field** accepts a model id (`anthropic.claude-…`), an
  inference-profile id (`us.anthropic.claude-…`), or a full ARN in any of the
  four Bedrock shapes: foundation-model (no account id), system
  inference-profile, application inference-profile, or provisioned-model.
  Bad ARNs are rejected with a plain-language error before anything is sent.
- **Keys** live in VS Code SecretStorage, reach the sidecar via env only, and
  never appear in settings, events, logs, or error text.
- **SSO / shared config (honest gap):** AWS SSO and `~/.aws` profile auth need
  per-request SigV4 signing, which the sidecar doesn't do — so the extension
  does not offer them as working options. Use a Bedrock API key here, or use
  Claude Code's Bedrock integration (it speaks SSO natively) with the
  A.W.I.N.O. skill.

## Importing connections from other tools

`A.W.I.N.O.: Import connections from other tools` ports model connection
details you already have elsewhere, so you don't retype them. Permission
first: nothing on disk is read before you say yes, and nothing is written
before you tick findings and confirm.

Sources (researched, not guessed):

- `~/.claude/settings.json`, `~/.claude/settings.local.json` (user) and
  `<project>/.claude/` (project) — AWS profile/region, model / ARN.
- `<project>/.env`, `<project>/.env.local` — regions, base URLs, model ids.
- Kilo **CLI** configs: `~/.config/kilo/kilo.jsonc`,
  `~/.config/kilo/opencode.json`, `<project>/.kilo/kilo.jsonc`,
  `<project>/kilo.jsonc` — provider base URLs, model ids.

What it extracts: provider kind, region(s), model ids / ARNs (validated with
the Bedrock ARN parsers), base URLs, AWS profile names. Secrets are NEVER
imported, copied, stored, logged, or displayed — a secret-looking value
becomes a note ("found a credential reference in <file> — enter it yourself
in the providers panel") and nothing more.

Kilo honesty: the Kilo **VS Code extension** keeps provider profiles in its
own VS Code globalState + SecretStorage — there is no file to read, and we
do not reach into another extension's storage. Kilo CLI's
`~/.local/share/kilo/auth.json` is a credential store by design and is
deliberately never read.


## Key design facts

- API keys are **never** in settings JSON. They live in VS Code SecretStorage
  and reach the sidecar via env vars only. They never appear in events, logs,
  or the contract block.
- Environments come from the project's `.awino/providers.yaml`; switching is
  explicit and journal-logged. Project A can never see project B's key.
- Approval flow: `approval_requested` → VS Code modal (Approve / Deny /
  "Always allow this tool" for the session). `write_file` approvals carry a
  unified diff. Deny/dismiss is the safe direction.
- Compaction at ~85% of the context window pauses the turn and asks the
  operator (modal + webview card). Denial is journaled; auto-approve is
  per-project and defaults off.
- Modes are data: stage defaults switch automatically; custom modes
  (planner, architect, debug-engineer, storyboard, tutor, …) are operator
  overlays, journal-logged.
- Skill-as-persona is a lens, not a license: only verified + SHA-256-pinned
  skills can be assumed, and the persona can never widen the contract's
  tool policy.

## Build & test

```bash
npm install            # devDeps only: typescript + @types/* (zero runtime deps)
npx tsc -p ./          # must compile clean
node test/harness.js   # spawn-path test against the real sidecar
npx @vscode/vsce package   # build the .vsix (result recorded honestly)
```

## Honest gaps

- **Live GUI tested:** The extension has been exercised inside a real VS Code
  window (1.139.0) via Playwright/CDP under Xvfb — see `proof/vscode_live_gui_test.md`.
  Contract progression, approved write, denied command, and zero orphans proven.
- Marketplace publishing needs the user's identity — steps are in
  `integrations/vscode/README.md`; the task stops at a built `.vsix`.
- **Providers:** OpenAI (any OpenAI-compatible endpoint), Anthropic, local Ollama,
  AWS Bedrock (via its OpenAI-compatible endpoint + Bedrock API key), plus MCP
  servers. Keys are user-supplied via SecretStorage; the agent does not configure
  them unasked.
- **`awino.script` is test-only:** Scripted turns still pass contract validation,
  judges, approvals, tools, and journaling — only the model text is canned.
