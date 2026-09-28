# A.W.I.N.O. for OpenCode

A.W.I.N.O. as an OpenCode bundle: model-agnostic (any OpenAI-compatible
endpoint), no VS Code extension, no sidecar.

| file | what it does |
|---|---|
| `opencode.json` | provider `awino` (OpenAI-compatible); endpoint, key and model from `AWINO_BASE_URL`, `AWINO_API_KEY`, `AWINO_MODEL` |
| `agents/awino.md` | primary agent: mission first, follow the routed stance, prove, close with a receipt |
| `agents/interviewer.md` | grills an idea into a mission; write/edit/patch denied |
| `plugins/awino.ts` | mission gate, protected ledger, `set_mission`, `story_close`, routed stance on every model call, redo on rule-breaking replies |

Use it: `OPENCODE_CONFIG_DIR=/path/to/opencode opencode` with the three env
vars set (an installer comes in Phase 2).

What the plugin enforces:

- `write`/`edit`/`patch` are blocked until `.awino/mission.json` exists; only
  `set_mission` creates it.
- `.awino/**` and `BRAG.md` can't be written by file tools, and bash commands
  that name them are blocked (a best-effort check on the command text).
- `story_close` writes `.awino/receipts/<id>.{md,json}` (promise, then proof)
  and appends `BRAG.md`.
- Each user message is routed with the `prototype/stances.py` intent table,
  and the resulting stance goes into the system prompt of every model call.
- On `session.idle`, a planning-grill reply with more than one question gets
  one `[A.W.I.N.O. correction]` follow-up. This needs a long-lived session
  (the TUI or `opencode serve`). One-shot `opencode run` exits at the first
  idle, before the follow-up can land.

Known gaps: bash can still write ordinary files before a mission exists (only
write/edit/patch are gated), and receipts list evidence without re-running it.
Done-criteria verification comes in Phase 2.

Proof: `.github/workflows/opencode-e2e.yml` runs `tests/e2e.py`, real
OpenCode against `prototype/tests/fake_gateway.py`.
