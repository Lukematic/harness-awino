# A.W.I.N.O. for OpenCode

A.W.I.N.O. as an OpenCode bundle: model-agnostic (any OpenAI-compatible
endpoint), no VS Code extension, no sidecar.

| file | what it does |
|---|---|
| `opencode.json` | provider `awino` (OpenAI-compatible); endpoint, key and model from `AWINO_BASE_URL`, `AWINO_API_KEY`, `AWINO_MODEL` |
| `agents/awino.md` | primary agent persona: restate the mission as `Mission:` / `Done when:`, follow the routed stance, prove, close with a receipt |
| `agents/interviewer.md` | grills an idea into a mission; write/edit/patch denied |
| `plugins/awino.ts` | mission anchor (no tool call), protected ledger, `story_close`, stance + mission on every model call, redo on rule-breaking replies |

Use it: from your project folder, run
`OPENCODE_CONFIG_DIR=/path/to/opencode opencode` with the three env vars set
(an installer comes in Phase 2). Start it from the project folder: OpenCode
takes the project directory from where it was launched.

How a mission starts: you just ask. Your first real request (not a
greeting or a quick question) is recorded as the mission, in code. The model
never has to call a tool to get started. The agent restates it as
`Mission:` plus `Done when:` bullets, and the plugin saves those bullets as
the done criteria. Start a message with `mission:` to replace the mission.

What the plugin does:

- Records the mission anchor in `.awino/mission.json` and puts it in the
  system prompt of every model call, next to the routed stance.
- Journals every file edit under the current mission (`.awino/journal.jsonl`).
- `.awino/**` and `BRAG.md` can't be written by file tools, and bash commands
  that name them are blocked (a best-effort check on the command text).
- `story_close` writes `.awino/receipts/<id>.{md,json}` (promise, then proof),
  appends `BRAG.md` and archives the mission, so the next request starts a
  new one.
- Each user message is routed with the `prototype/stances.py` intent table,
  and the resulting stance goes into every model call.
- On `session.idle`, a planning-grill reply with more than one question gets
  one `[A.W.I.N.O. correction]` follow-up. This needs a long-lived session
  (the TUI or `opencode serve`). One-shot `opencode run` exits at the first
  idle, before the follow-up can land.

Known gaps: receipts list evidence without re-running it (done-criteria
verification comes in Phase 2), and done criteria are only captured if the
model writes a `Done when:` list.

Proof: `.github/workflows/opencode-e2e.yml` runs `tests/e2e.py`, real
OpenCode against `prototype/tests/fake_gateway.py`.
