# A.W.I.N.O. VS Code Extension — Specification

**Status:** spec first, build against it. Nothing below is "done" until proven by the tests in §9.

## 1. Goal

An agent IDE surface for A.W.I.N.O.: a VS Code extension that puts the
loop-owner harness inside the editor — chat, contract view, journal, skills —
while the **Python sidecar owns the turn loop in code**. The extension is a
surface; it cannot bypass the harness because the loop (contract compilation,
validation, judge, approvals) runs in the sidecar process, not in the webview.

This makes A.W.I.N.O. a competitor to Kilo/Cline/Roo-style extensions with one
structural difference: their loop is a prompt; ours is a program.

## 2. Architecture

```
┌─ VS Code ──────────────────────────────────────────────┐
│  extension.ts (Node)                                   │
│   ├─ spawns: python3 prototype/awino_sidecar.py        │
│   │        stdin:  JSON commands (one per line)        │
│   │        stdout: JSON events   (one per line)        │
│   ├─ chat webview (HTML/JS, no framework)              │
│   ├─ tree views: Contract · Journal · Learnings        │
│   └─ settings: provider / endpoint / model / timeout   │
└──────────────────────────┬─────────────────────────────┘
                           │ stdio, newline-delimited JSON
┌─ sidecar (Python, stdlib only) ───────────────────────┐
│  awino_sidecar.py                                      │
│   ├─ REAL Loop (loop.py) — the owned turn pipeline    │
│   ├─ REAL contract compiler / validators / judge panel │
│   ├─ WorkspaceSandbox(Sandbox) rooted at workspace    │
│   └─ backends: echo | ollama | openai | anthropic      │
│                (+ scripted, test-only, documented)     │
└────────────────────────────────────────────────────────┘
```

**No-bypass argument:** every model-proposed action flows through
`Loop.run_user_turn` → stages 0–4b (contract, sensor, permission gate,
validation, judge, stance rubric, pre-execute check, approval gate). The
webview never constructs turns, never calls tools, never edits files. The
only paths into the harness are the command verbs in §4, and each maps to a
real `Loop` method.

## 3. Components

| # | Component | File | Done when |
|---|-----------|------|-----------|
| 1 | Sidecar | `prototype/awino_sidecar.py` | §4 protocol holds; `test_sidecar.py` green 3× |
| 2 | Backends | in sidecar file | openai + anthropic via stdlib urllib; keys from env only; bad key → safe fallback, never crash |
| 3 | Workspace tools | `WorkspaceSandbox` in sidecar | read/write/list/run/search rooted at workspace; traversal refused in code; run_command approval-gated |
| 4 | Extension host | `integrations/vscode/extension/src/extension.ts` | compiles clean with `tsc`; spawns sidecar; routes events |
| 5 | Chat webview | `extension/webview/chat.html` (+ inline JS) | renders events; sends commands; approval modal; diff accept/reject |
| 6 | Tree views | in `extension.ts` | Contract (stage, criteria checkboxes live), Journal (turn history), Learnings/Skills |
| 7 | Settings | `package.json` contributes.configuration | provider dropdown, endpoint, model, timeout; API key via SecretStorage command |
| 8 | Commands | `package.json` contributes.commands | New Mission, From Seed, Save Seed, Inspect Contract, Rollback, Add Skill, Context add/remove/reorder (+ Set API Key) |
| 9 | Packaging | `vsce` | `.vsix` builds; result reported honestly |
| 10 | Docs | `integrations/vscode/README.md` | install, model setup, enforcement story, caveats, publish steps |
| 11 | Models UI | settings webview | provider dropdown, endpoint, model, API-key via SecretStorage, reconnect |
| 12 | Skills | sidecar `skill_add` + Skills tree | file/learn → screen → VERIFY checks in sandbox → sha256 pin → admit; refusals recorded |
| 13 | Seeds | `.awino/seeds/*.md` + commands | list / from-seed / save-seed round-trip |
| 14 | Context | `.awino/context*.md` + Context tree | add/remove/reorder; compiled into every contract block |
| 15 | MCP client | sidecar `McpClient` | stdio JSON-RPC (Content-Length framing), tools registered into TOOL_DEFS/MODES, same gates |

## 4. Sidecar protocol

### 4.1 Commands (stdin, one JSON object per line)

| cmd | fields | effect |
|-----|--------|--------|
| `hello` | `workspace`, `provider`, `model?`, `endpoint?`, `project?`, `script?` | build the `Loop`; reply `ready`. Must be first. |
| `user_message` | `text` | run one owned turn in a worker thread; reply `turn_result` (+ `approval_requested` when paused) |
| `approve` | `id`, `decision`: `approve`\|`deny` | `loop.approve(id)` / `loop.deny(id)`; reply `turn_result` (resumed turn) |
| `command` | `name`, `args` | harness ops: `mission`, `status`, `contract` (compiled block, with context/skills sections), `approve-contract`, `done`, `rollback`, `learnings`, `journal`, `synthesize`, `seeds_list`, `mission_from_seed{name}`, `seed_save{name}`, `context_list`, `context_add{name,content}`, `context_set{name,content}`, `context_remove{name}`, `context_reorder{order}`, `skills_list`, `skill_add{name,path}`; reply `command_result` |
| `cancel` | — | cooperative: if a turn is in flight, its result is discarded on completion; queued turns dropped. Cannot interrupt an in-flight HTTP call (documented). |
| `bye` | — | reply `bye`, exit 0 |

Providers: `echo` (safe default, no model), `ollama` (local OpenAI-compatible,
no key), `openai` (any OpenAI-compatible endpoint + `AWINO_API_KEY`),
`anthropic` (`ANTHROPIC_API_KEY`), `scripted` (**test-only**: candidate turns
from `script`, still through the full pipeline — no bypass).

Malformed JSON on stdin → `error` event, never a crash. Unknown cmd →
`error` event. Commands before `hello` → `error` event.

### 4.2 Events (stdout, one JSON object per line, flushed)

| event | payload |
|-------|---------|
| `ready` | `protocol`, `project`, `provider`, `model`, `workspace`, `mcp` (per-server: ok/tools/error) |
| `turn_result` | `result` = the dict `run_user_turn`/`approve`/`deny` returned (`status`, `said`, `phase`, `mode`, `results`, …) |
| `approval_requested` | `turn_id`, `approvals`: `[{id, tool, args, diff?, old_exists?}]` — `diff` is a unified diff for `write_file` |
| `command_result` | `name`, `ok`, `result` |
| `cancel_ack` | `accepted`, `note` |
| `error` | `message` |
| `bye` | — |

Logging goes to stderr only. Stdout is the protocol channel.

### 4.3 Approval flow (end to end)

1. Turn pauses: `check_pre_execute` finds a consequential tool without a
   matching approval → `turn_result{status: awaiting_approval}` +
   `approval_requested` with diffs.
2. Extension shows a VS Code modal: tool, args, diff; buttons
   **Approve / Deny** (+ session toggle "Always allow this tool", default off —
   the operator delegating their own approval, not a model bypass).
3. Decision → `approve{id, decision}` → sidecar resumes via `loop.approve` /
   `loop.deny` → `turn_result` with execution results.
4. Stale approvals (revision/scope changed) are refused by the harness itself.

### 4.4 Tool set (workspace-rooted)

`read_file`, `write_file` (consequential → approval + diff preview),
`run_command` (consequential → approval, timeout, cwd=workspace),
`list_dir`, `search_files` (regex grep, non-consequential, read-only).
All paths resolved against the workspace root; traversal (`..` escapes)
refused in code. `search_files` is offered in every mode; `run_command`
deviation (approval-gated, unlike the core's verify mode) is sidecar-local —
the core `prototype/` behavior is unchanged.

## 5. Settings schema (`awino.*`)

| key | type | default | notes |
|-----|------|---------|-------|
| `awino.provider` | enum `echo\|ollama\|openai\|anthropic` | `echo` | model source |
| `awino.endpoint` | string | `""` | openai-compatible base URL or Anthropic base; empty → provider default |
| `awino.model` | string | `""` | empty → provider default |
| `awino.timeout` | number (s) | `180` | model HTTP timeout |
| `awino.pythonPath` | string | `python3` | sidecar interpreter |

API keys are **never** settings: `AWINO_API_KEY` / `ANTHROPIC_API_KEY` are
injected into the sidecar's env from VS Code SecretStorage
(`A.W.I.N.O.: Set API Key`). Keys never appear in events, logs, or settings.

## 6. Backend details

- `OpenAICompatibleBackend(OllamaBackend)`: full chat-completions URL +
  `Authorization: Bearer` when a key is present. Reuses the turn system
  prompt (extended with `search_files`), `_extract_json`, `_normalize`,
  `_fallback`.
- `AnthropicBackend(OllamaBackend)`: `POST {base}/v1/messages` with
  `x-api-key`, `anthropic-version: 2023-06-01`; parses
  `content[0].text`.
- Failure mode (unreachable host, 401/403, timeout, bad payload): the
  existing safe fallback — a clarifying question, **zero tool calls**.
  A bad key fails closed; it never crashes the sidecar.

## 7. Extension UX

- **Chat view** (activity bar): message list, input box, send; renders
  `turn_result.said`, phase/mode chips, tool results, errors. Send disabled
  while a turn is in flight; a Stop button sends `cancel`. With no mission,
  an interview banner shows instead of a blank chat (New Mission / From Seed);
  the harness itself opens the discovery interview (planning-grill stance).
- **Models view**: provider dropdown, endpoint, model name, API-key field
  (SecretStorage, never logged), Save & Reconnect, connection status.
- **Skills view**: packaged skills (name + sha256) and project-admitted
  skills (name + sha256); "Add Skill" from a file (screened, VERIFY-checked
  in the sandbox, hash-pinned) or from a recorded learning.
- **Context view**: `.awino/context.md` + `.awino/context/*.md` files with
  add (opens the file for editing), remove, and move up/down; the ordered
  content is compiled into every contract block.
- **Contract view**: mission text, phase, mode, live criteria checkboxes
  (`[x]`/`[ ]` from `status`), scope, pending approvals count.
- **Journal view**: effect journal (seq, tool, args summary, reused flag).
- **Learnings view**: recorded learnings + synthesized skills.
- **Commands**: `A.W.I.N.O.: New Mission` (asks objective + criteria, sends
  `command{mission}`), `A.W.I.N.O.: New Mission from Seed` (quickpick),
  `A.W.I.N.O.: Save Current Mission as Seed`, `A.W.I.N.O.: Inspect Contract`
  (shows the compiled `contract` block), `A.W.I.N.O.: Rollback` (asks seq,
  confirms, sends `rollback`), `A.W.I.N.O.: Add Skill`,
  `A.W.I.N.O.: Add Context File`.
- **Diff preview**: `write_file` approvals render old→new unified diff in
  the webview; Accept/Reject maps to approve/deny.

## 8. Packaging

- `npm install` needs nothing (zero runtime deps; `typescript` + `@types/vscode`
  + `vsce` as devDeps via npx).
- `npx -y tsc -p .` must compile clean.
- `npx -y @vscode/vsce package` attempted; result reported honestly.
- **Marketplace publishing is NOT done here** — it needs the user's identity.
  Steps are documented in the README; the task stops at a built `.vsix`.
- A headless VS Code GUI test is out of scope (no display/server in this
  environment); the sidecar protocol — the part that owns the loop — is fully
  tested headless instead. Stated as a gap, not faked.

## 9. Test plan (`prototype/tests/test_sidecar.py`)

Subprocess tests against the real sidecar, mirroring `test_mcp.py`:

1. `hello` → `ready`; every line on stdout parses as one JSON object.
2. Each workspace tool through a scripted turn: `read_file`, `list_dir`,
   `search_files`, `write_file` (approved), `run_command` (approved).
3. Path traversal (`../../etc/passwd`) refused — tool error, sidecar alive.
4. Approval round-trip: scripted turn proposes `write_file` in approved
   scope → `turn_result{awaiting_approval}` + `approval_requested` with a
   non-empty diff → `approve` → file written on disk, result in journal.
   Then deny path: denied write leaves no file.
5. Backend selection: `openai` provider against a stub HTTP server —
   401 → fail-closed fallback (no tool calls, asks a question, no crash);
   200 with a valid turn → turn flows.
6. Malformed stdin (`{not json`) → `error` event; sidecar survives; next
   valid command still works.
7. Cancel: stub server sleeps; `user_message` then `cancel` →
   `cancel_ack{accepted:true}` and no `turn_result` emitted.
8. Full repo suite green **3 consecutive runs**; log saved to
   `proof/vscode_integration_3x.log`.
9. Context: `context_add` then `command{contract}` shows the context text
   in the compiled block; `context_reorder` changes the order.
10. Seeds: `seed_save` after a mission, `seeds_list` shows it,
    `mission_from_seed` restores objective + criteria (verified via `status`).
11. Skills: `skill_add` with a VERIFY-bearing file → admitted with sha256;
    `skills_list` shows it; tampering the admitted body → registry load
    fails closed; a file with no VERIFY checks → refused; an injected file
    ("ignore all prior rules") → refused.
12. Interview: `user_message` with no mission → the harness opens the
    discovery interview (no blank free-chat), sidecar stays alive.
13. MCP client: fake MCP server over stdio → tools appear in `ready.mcp`
    and in the compiled contract's offered list; a scripted turn calling
    one pauses for approval; approving runs it and returns the server's
    output; a dead server → `mcp` error entry, hello still succeeds;
    a crashing tools/call → error result, sidecar alive.

## 10. Phases (measurable)

1. Spec (this file + §12 deltas) — done when every later artifact traces
   to a section.
2. Sidecar + backends + workspace tools + skills/context/seeds/MCP-client
   + tests — done when `test_sidecar.py` passes 3×.
3. Extension shell (package.json, extension.ts, chat webview, settings,
   Models view) — done when `tsc` is clean and the sidecar spawns from the
   extension host code path (spawning logic unit-testable without a GUI).
4. Approvals + diff preview + tree views (Contract/Journal/Learnings/
   Skills/Context) + commands + seeds UI — done when every §4 event has a
   renderer and every §5 setting is read.
5. Package — done when `vsce` result is recorded; README complete.

## 11. Honest gaps (carried into the README)

- The extension has **not** been exercised inside a live VS Code window —
  the GUI half is compiled, not run. The sidecar half is fully tested.
- `cancel` cannot interrupt an in-flight model HTTP call; it discards the
  result and drops queued turns.
- Sidecar missions persist under `~/.awino-loop` (event-sourced, resumable),
  but the sidecar process itself holds the `Loop`; killing VS Code
  mid-turn is crash-safe (state persisted every turn) but the in-flight
  model call is lost.
- `run_command` runs shell commands rooted at the workspace with the
  user's privileges — approval-gated, but the operator must still read
  what they approve.
- The `scripted` provider is test-only; it feeds candidate turns through
  the identical pipeline (no bypass), but it exists in the shipped file.
- Project-admitted skills are appended to the contract as full bodies every
  turn (v1: no per-turn routing, 8KB cap) — the packaged-skill routing is
  untouched.
- MCP servers are operator-configured code running on the user's machine:
  the harness gates their *calls* (contract + approval), but a server that
  lies in `readOnlyHint` could dodge approval-gating only if the operator
  enabled `trustReadOnlyHint` for it (default off). Configure only servers
  you trust; a dead/misbehaving server fails closed per call.
- Marketplace publish, extension signing, and auto-update are the user's
  steps, documented not done.

## 12. Scope deltas (user additions, folded in)

1. **Workspace tools first-class** — read/edit/diff/run/grep are the
   harness tool registry (TOOL_DEFS + MODES), not a side panel; the model
   proposes, the contract gates, the operator approves.
2. **Providers UI** — a Models settings webview (provider/endpoint/model/
   key via SecretStorage), not just raw JSON settings.
3. **User-addable skills** — Skills tree + Add Skill wired to the real
   machinery: injection screen → VERIFY checks executed in the sandbox →
   sha256 pin in the project registry → hash-verified load. Unverified
   prose and injected instructions are refused, recorded on the event log.
4. **Mission-first** — no mission ⇒ the discovery interview opens (the
   harness's own planning-grill behavior), never a blank chat; plus
   **mission seeds** (`.awino/seeds/*.md`) for reusable objective
   templates: list, launch from seed, save current mission as seed.
5. **User context** — `.awino/context.md` + `.awino/context/*.md`,
   managed from a Context tree (add/remove/reorder), compiled into every
   contract block the harness builds (appended after the harness sections;
   the turn header is untouched).
6. **MCP client** — the sidecar speaks MCP over stdio (Content-Length
   framing, hand-rolled JSON-RPC, stdlib only) and registers server tools
   into the harness registry: same offered/consequential computation, same
   pre-execute check, same approval gate. `trustReadOnlyHint` (default
   false) controls whether read-only-hint tools skip approval.

## Scope #5: Housekeeping, Compaction, FAIR

**Standard layout** — every project gets `.awino/` with `README.md`,
`config.json`, `contract.json`, `providers.yaml`, `journal/`,
`tool_results/`, `seeds/`, `context/`, `skills/`, `modes/`, `archive/`,
each with a README index (FAIR Findable).

**Housekeeping** — `housekeeping` sidecar command + `A.W.I.N.O.: Housekeep
Project` VS Code command. Archives stale tool results, rotates journals,
tidies unknown root files to `archive/` with microsecond timestamps
(collision-safe). Never deletes. Writes `housekeeping.json` manifest.
Auto-runs on mission close (only if close succeeds) and stage transitions.
`housekeeping.git_commit` is opt-in, default off; when on, commits only
`.awino/` via `git commit -- .awino`.

**Compaction** — at 85% of the context window (projected tokens before a
turn), the sidecar emits `compaction_proposed` with tier disclosure
(pinned: contract/criteria, never reduced; summarizable: oldest ~30% of
history; offloadable: tool outputs), token-savings estimate, and
pinned-safety list. The turn pauses for normal user approval via the
standard `approve`/`deny` flow (or `compaction_decide`). Denial records
`compaction_declined`, warns, and continues. Approval extractively
summarizes the oldest turns; full detail stays in the state journal.
`compaction.auto_approve` is per-project, default false.

**FAIR** — Findable: folder READMEs + manifests. Accessible: plain-text
JSON/Markdown (YAML only for `providers.yaml`, the explicit plain-text
exception). Interoperable: every JSON carries `schema_version`.
Reusable: portable seeds and skills. The `events` command exposes the
state event log (last 50, redacted) so decisions are auditable.
