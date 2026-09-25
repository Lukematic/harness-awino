# webview

Unbundled webview UIs — plain HTML + JS, no build step, no framework.

- `chat.html` / `chat.js` — the mission chat. Renders every §4 sidecar event:
  turn results (said text, phase/mode/persona chips, tool results), approval
  cards (tool, args, unified diff for `write_file`, cwd-resolved shell
  targets with an out-of-workspace flag for `run_command`, Approve/Deny), compaction
  proposals, errors, warnings. Sends only the §4 command verbs
  (`user_message`, `approve`, `stop`) back to the extension host. With no
  active mission it shows the interview banner instead of a blank chat.
- `models.html` / `models.js` — the Models & Providers panel: provider
  dropdown (echo/ollama/openai-compatible/anthropic), endpoint, model,
  timeout, API-key fields (SecretStorage only, never logged), Save &
  Reconnect, and the per-project environment switcher.

The webview never constructs turns, never calls tools, never edits files —
the harness loop runs in the Python sidecar, not here.
