# A.W.I.N.O. VS Code Extension — Chat UI Overhaul Spec (target 0.4.0)

**Status:** SPEC ONLY — not built. No source code was modified to produce this document.
**Target version:** 0.4.0 (current: 0.3.0).
**Source direction (user, 2026-09-23):** Wakandan-flavored chat GUI; Copilot-grade smoothness; Claude-style exposed model thinking per turn.

Three pillars:

1. **Wakandan flavor visual design** — Afrofuturist aesthetic: deep dark blue-black surfaces, vibranium-blue accents, gold highlights, spare geometric/tribal motifs. Regal and professional, never costume-y, never cluttered. Theme-aware via `var(--vscode-*)` with fallbacks.
2. **Copilot-grade smoothness** — token-by-token streaming, markdown rendering, code blocks with copy buttons, tool calls as tidy progress rows, polished diff display.
3. **Exposed thinking trace** — collapsible per-turn "Thinking" section (Claude-style), sitting next to a "Harness checks" feed. The pitch: *watch the model think AND watch the harness check it.*

## 0. Architecture facts this spec relies on (verified in tree)

- Sidecar protocol: JSON-lines on stdout. `prototype/awino_sidecar.py::_emit(obj)` writes one JSON object per line + flush. Events today: `ready`, `turn_result`, `approval_requested`, `compaction_proposed`, `command_result`, `cancel_ack`, `warning`, `error`, `bye`.
- `turn_id` already exists: `loop.py` generates `t{turn_no}` (e.g. `t7`); journal records and `approval_requested` already carry it.
- `src/extension.ts::onSidecarEvent` has a `default:` case that forwards unknown events to the chat webview. **New sidecar events therefore reach `chat.js` with zero routing changes** — only `chat.js` needs new renderers.
- Backends (`prototype/awino_sidecar.py`, also `prototype/backends.py`): `_chat(prompt, system) -> str`, currently `"stream": False`. OpenAI-compatible backend POSTs via `urllib` to `{endpoint}/v1/chat/completions`; Anthropic backend uses the Messages API. `Loop` calls `backend.generate(contract_block, history, feedback)` through `_ModeAwareBackend`.
- Webview: plain JS, no build step. `extension.ts` substitutes `{{CHAT_JS}}` / `{{MODELS_JS}}` with webview URIs.
- Tests: `node test/harness.js` (headless spawn of the real sidecar); live GUI proof documented in `proof/vscode_live_gui_test.md` (Playwright + Xvfb, official VS Code 1.139.0, driver `~/workspace/vscode-test-env/gui_test.py`, 34/34 checks, screenshots in `proof/vscode_gui/`).
- Tree views (`src/views.ts`): Contract, Journal, Learnings, Skills, Context, Modes — plain-text leaves today.

## 1. Design tokens

All custom properties are prefixed `--awino-`. Every usage pairs with a VS Code variable and a concrete fallback so light themes and high-contrast degrade gracefully.

### 1.1 Palette

| Token | Vibranium value (`body.awino-vibranium`) | Savanna value (`body.awino-savanna`) | Maps to / used for |
|---|---|---|---|
| `--awino-bg` | `#111A2E` | `#F6F1E5` | chat body background |
| `--awino-bg-2` | `#172036` | `#FFFEFA` | message cards, approval cards |
| `--awino-bg-3` | `#1E2A45` | `#ECE4D0` | input bar, code block headers |
| `--awino-vibranium` | `#00C6FF` | `#0B7FA6` | primary accent: links, phase chip, Send button, streaming caret |
| `--awino-vibranium-deep` | `#0B7FA6` | `#0B5A78` | borders, hover states |
| `--awino-vibranium-dim` | `rgba(0,198,255,.12)` | `rgba(11,127,166,.12)` | chip backgrounds, focus rings, progress-row backgrounds |
| `--awino-gold` | `#D4A937` | `#8A6D1F` | secondary accent: mode chip, approval-card chevron band, bead indicators |
| `--awino-gold-bright` | `#F0C94A` | `#8A6D1F` | highlights, active bead glow |
| `--awino-gold-dim` | `rgba(212,169,55,.12)` | `rgba(138,109,31,.14)` | mode chip background, thinking-section border |
| `--awino-text` | `#DCE7F7` | `#2B2517` | primary text fallback |
| `--awino-muted` | `#8B98B0` | `#77694F` | secondary text fallback |
| `--awino-ok` | `#4EC9B0` | `#1E7E5A` | success (keeps VS Code testing-green semantics) |
| `--awino-danger` | `#F14C4C` | `#C42B2B` | errors (keeps VS Code testing-red semantics) |
| `--awino-warn` | `#CCA700` | `#8A6D00` | warnings (unchanged from today) |

### 1.1a Theme variants (user refinement, 2026-09-23 — supersedes the §10 light-theme non-goal)

Two Wakandan variants ship in 0.4.0, both in the same design language (§1.2 motifs: bead dots, chevron bands, geometric edges):

- **Vibranium** (dark): the palette above, lifted slightly from the first draft (`#0B101B` → `#111A2E`, `#101827` → `#172036`, `#162032` → `#1E2A45`) — deep blue-black, no pitch-black heaviness. Vibranium blue `#00C6FF` and gold `#D4A937` accents unchanged.
- **Savanna** (light): warm light variant — sand surfaces (`#F6F1E5` / `#FFFEFA` / `#ECE4D0`), dark warm text `#2B2517`, deeper vibranium `#0B7FA6` and gold `#8A6D1F` for contrast on light.

**Selection:** the chat webview defaults to the VS Code color theme — `body.vscode-light` → Savanna, otherwise Vibranium (the `vscode-*` blocks carry these as no-JS fallbacks). A small toggle in the chat header (`#theme-toggle`, "◐ Savanna/Vibranium") switches variants manually; the explicit `body.awino-savanna` / `body.awino-vibranium` classes sit later in the stylesheet than the `vscode-*` blocks so the manual choice wins at equal specificity. The manual choice persists in webview state (`vscode.setState({awinoTheme})`) for the webview's lifetime; it is a display preference only and is never journaled.

**Scope:** the chat UI gets the full two-variant treatment. The Models panel (§2.11) and tree views (§2.10) get light-touch consistency only (existing token accents, no retheme).

### 1.2 Motifs (spare, never cluttered)

- **Chevron band:** `repeating-linear-gradient(135deg, var(--awino-gold) 0 2px, transparent 2px 9px)`, rendered as a 3px-high strip. Used in exactly two places: top edge of approval cards, and under the Models panel `<h2>`.
- **Vibranium edge:** user messages get `border-left: 3px solid var(--awino-vibranium)` (replaces today's `--vscode-button-background` edge); assistant turn cards get `border-left: 3px solid var(--awino-vibranium-deep)`.
- **Kimoyo beads (status indicators):** `.bead` = 8px circle, `border-radius: 50%`. Used in the statusline and on tool-progress rows (see §5). Bead colors: vibranium = connected/active, gold = working/streaming (pulsing), `--awino-ok` = done, `--awino-danger` = error/pending-approval.
- **Thinking section:** `border-left: 2px dotted var(--awino-gold)` — dotted, not solid, to read as "inner monologue".

No background textures, no large decorative shapes, no emoji iconography. Motifs are 1–3px lines and 8px dots only.

### 1.3 Typography

Unchanged: `var(--vscode-font-family)`, 13px base. Code: `var(--vscode-editor-font-family, monospace)`, 12px in blocks, 11px diff.

## 2. Component styling (Wakandan flavor, concrete)

### 2.1 Chat messages

- `.msg`: `background: var(--awino-bg-2, var(--vscode-editor-background, #101827))`, `border-radius: 8px`, `padding: 10px 12px`, `margin-bottom: 12px`.
- `.msg.user`: `background: var(--awino-bg-3, var(--vscode-input-background, #162032))`, vibranium left edge (above).
- `.msg.turn`: assistant card, vibranium-deep left edge.
- `.msg.error` / `.msg.warn`: keep today's red/gold left edges, mapped to `--awino-danger` / `--awino-warn`.

### 2.2 Chips (phase / mode / persona / status)

- `.chip.phase`: `background: var(--awino-vibranium-dim)`, `color: var(--awino-vibranium)`, `border: 1px solid var(--awino-vibranium-deep)`, uppercase, 10px, letter-spacing .04em.
- `.chip.mode`: `background: var(--awino-gold-dim)`, `color: var(--awino-gold-bright)` (dark) / `var(--awino-gold)` (light), `border: 1px solid var(--awino-gold)`.
- `.chip.persona`: transparent background, `border: 1px dashed var(--awino-gold)`, gold text — dashed to signal "lens, not license".
- `.chip` (status): unchanged neutral badge.

### 2.3 Approval cards

- Container `.approval`: `background: var(--awino-bg-2)`, `border: 1px solid var(--vscode-panel-border)`, `border-radius: 8px`, `padding: 12px`, with the 3px chevron band on top (`::before`).
- Header: `Approval requested: <tool>` — tool name in `color: var(--awino-vibranium)`, monospace.
- Args shown as before (`<pre>` JSON); **diff preview uses the polished diff renderer** (§4.4): added lines `background: rgba(78,201,176,.12)`, removed lines `background: rgba(241,76,76,.12)`, hunk headers `@@` in vibranium, `max-height: 240px` scroll retained.
- Buttons: Approve = `background: linear-gradient(180deg, var(--awino-vibranium), var(--awino-vibranium-deep))`, dark text `#04222E` on dark theme for contrast (4.5:1+ vs `#00C6FF`); Deny = secondary. After click, disable + show bead spinner until the sidecar confirms (today the button just disables — keep that, add the spinner).
- The VS Code modal dialog path (`extension.ts::handleApprovalRequested`) is unchanged — the card is the in-chat record, the modal remains the decision path.

### 2.4 Thinking trace section (§6 for behavior)

- Collapsible block at the top of each assistant turn card, below chips: header row `◈ Thinking` (gold chevron `▸`/`▾` toggle) + `border-left: 2px dotted var(--awino-gold)` + `background: var(--awino-gold-dim)` at 40% alpha + 11px muted text.
- While streaming: open, with a pulsing gold bead + "thinking…" label.
- After `turn_result`: collapsed by default; header shows first 80 chars as summary.

### 2.5 Harness checks feed (§6 for behavior)

- Second collapsible block in the turn card: header `⬡ Harness checks` with vibranium chevron. Rows: check name (monospace, 11px) + verdict pill (`pass` = vibranium-dim/vibranium text; `fail` = danger; `warn` = gold) + detail in muted text.
- This is the visual half of "watch the harness check it".

### 2.6 Tool progress rows

- `.tools-live` container inside the streaming turn card. Each row: bead (gold pulsing while running → `--awino-ok` solid when done → danger on error) + tool name (monospace, vibranium) + summary (muted) + elapsed ms (right-aligned, muted).
- Replaces today's post-hoc `<table class="tools">` for streaming turns; the table stays as the fallback for non-streamed `turn_result`s.

### 2.7 Input bar

- `#inputbar`: `background: var(--awino-bg)`, top border `1px solid var(--vscode-panel-border)`.
- `#input`: `border: 1px solid var(--awino-vibranium-deep)`, `border-radius: 8px`; `:focus` → `outline: none; box-shadow: 0 0 0 1px var(--awino-vibranium)`.
- `#send`: vibranium gradient (same as Approve). `#stop`: secondary.
- Placeholder unchanged: "Message the loop owner… (a mission starts with the discovery interview)".

### 2.8 Statusline — Kimoyo bead cluster

Left cluster, then text, e.g.:

`● ● ● ○ ○  connected · openai · env (global) · model qwen2.5:1.5b`

Beads (8px, 4px gap), in order: **link** (vibranium solid = connected, gray hollow = not), **turn** (gold pulsing = turn streaming, hollow = idle), **approval** (danger pulsing = approval pending, hollow = none), **mode** (vibranium-dim solid = custom overlay active, hollow = stage default), **persona** (gold-dim solid = persona assumed, hollow = none). Tooltips via `title` attribute ("link: connected", "turn: streaming", …). Text part unchanged from today.

### 2.9 Banner (no active mission)

Keep the interview banner; restyle: `background: var(--awino-vibranium-dim)`, `border-bottom: 2px solid var(--awino-vibranium-deep)`, bold lead "No active mission." unchanged.

### 2.10 Sidebar tree views (`src/views.ts`)

Light touch only — tree views stay native VS Code:

- `ModesView`: keep the existing `●`/`○` active markers (they already read as beads); active mode's `description` gains `(active)` — no color API needed.
- `ContractView`: "Phase" leaf description shows the phase in UPPERCASE (matches the vibranium phase chip in chat).
- `JournalView`: error/denial rows (tool `deny`, `approval_denied`) prefixed `✕ `; approvals `✓ ` — text-only, no icon assets.
- View container title stays "A.W.I.N.O.".

### 2.11 Models & Providers panel (`webview/models.html`)

- `<h2>` gets the 3px chevron band underneath (same gradient as approval cards).
- Section labels (`Environment (per-project provider bindings)`) in gold, 12px, uppercase, letter-spacing.
- Inputs: focus ring `box-shadow: 0 0 0 1px var(--awino-vibranium)` (same as chat input).
- Save button: vibranium gradient; Clear stored keys: secondary.
- Key-state spans (`(stored)`/`(not set)`): `(stored)` in `--awino-ok`, `(not set)` muted.
- The two `.note` blocks keep muted text; the Bedrock endpoint note's `<code>` gets vibranium text.

## 3. Sidecar protocol additions (pillar 2 + 3)

Backwards-compatible by construction: every new event is a new `event` name on the existing JSON-lines stdout. Old clients hit `chat.js`'s `default:` case (renders a small `[event]` line) and still receive the full `turn_result`; new clients talking to an old sidecar simply never see deltas and render the block as today.

`turn_id` reuses the existing `t{turn_no}` format from `loop.py`.

### 3.1 New events

```jsonc
// A turn begins. Webview opens a streaming card shell (beads pulse, "thinking…" shows).
{"event": "turn_start", "turn_id": "t7", "phase": "BUILD",
 "mode": {"id": "build", "source": "stage"}, "persona": null}

// Reasoning chunk. Accumulates into the turn's Thinking section.
{"event": "thinking_delta", "turn_id": "t7", "text": "The user asked for X; the contract allows Y, so…"}

// Response text chunk. Accumulates into the turn body, re-rendered as markdown.
{"event": "said_delta", "turn_id": "t7", "text": "Here's the plan:\n\n1. …"}

// Tool execution progress → tidy rows (replaces post-hoc table for streamed turns).
{"event": "tool_progress", "turn_id": "t7", "tool": "write_file",
 "phase": "start", "summary": "notes.txt"}
{"event": "tool_progress", "turn_id": "t7", "tool": "write_file",
 "phase": "done", "summary": "notes.txt (42 bytes)", "ms": 12}
{"event": "tool_progress", "turn_id": "t7", "tool": "run_command",
 "phase": "error", "summary": "exit 1: …", "ms": 310}

// Harness deliberation → the "watch the harness check it" feed.
{"event": "harness_check", "turn_id": "t7", "check": "contract",
 "verdict": "pass", "detail": "phase BUILD · scope notes.txt · 3/4 criteria"}
{"event": "harness_check", "turn_id": "t7", "check": "judge:architect",
 "verdict": "pass", "detail": "plan covers rollback path"}
{"event": "harness_check", "turn_id": "t7", "check": "judge:devils-advocate",
 "verdict": "warn", "detail": "no test for empty input"}
{"event": "harness_check", "turn_id": "t7", "check": "approval_gate",
 "verdict": "pass", "detail": "write_file within approved scope"}
```

`turn_result` is unchanged and remains authoritative; it additionally carries the full accumulated `thinking` string and a `checks` array (the finalized harness_check rows) so non-streaming clients get everything in one event:

```jsonc
{"event": "turn_result", "result": {
  "said": "…full text…",
  "thinking": "…full reasoning (or null when the provider exposes none)…",
  "checks": [{"check": "contract", "verdict": "pass", "detail": "…"}],
  "phase": "BUILD", "active_mode": {"id": "build"}, "persona": null, "status": "ok" }}
```

### 3.2 Emission rules (sidecar)

- `turn_start` is emitted by `Loop` (or the sidecar dispatcher wrapping `run_user_turn`) **before** the first backend call of the turn, carrying the turn's `turn_id`, phase, mode, persona.
- Deltas are emitted per chunk as they arrive from the backend. Chunking: emit one event per backend chunk (SSE chunks are already small); cap event size at 4 KB of text per line (split larger chunks) to keep the JSON-lines parser happy.
- `thinking_delta` text is the model's reasoning **only**; the harness's own deliberation goes in `harness_check`. The two are never mixed in one stream.
- `harness_check` emission points (all already exist as journal/state records — this only surfaces them):
  - contract compiled → `check: "contract"`, verdict pass/fail;
  - each judge verdict (`judge_passed` / judge-failure records in `loop.py`) → `check: "judge:<stance>"`;
  - turn validation (`turn_validated`) → `check: "validation"`;
  - approval gate decision → `check: "approval_gate"`.
- On `cancel`, the webview freezes the card with a "cancelled" bead state; the sidecar's existing `cancel_ack` semantics are unchanged.
- If the provider exposes no thinking (Ollama/OpenAI-compatible non-reasoning models, echo, scripted), the sidecar emits **zero** `thinking_delta` events and sets `result.thinking = null`. The webview then renders the Thinking section header as "Thinking — not exposed by this provider" (honest, never fabricated).

### 3.3 Sidecar implementation hooks (Python, `prototype/awino_sidecar.py` + `prototype/backends.py`)

1. Add `_chat_stream(prompt, system)` generators:
   - `OpenAICompatibleBackend`: POST with `"stream": true`; parse SSE `data:` lines; yield `("said", delta)` for `choices[0].delta.content`; skip `[DONE]`.
   - `AnthropicBackend`: POST with `"stream": true`; parse SSE `content_block_delta`; map `delta.type == "thinking_delta"` → `("thinking", text)`, `delta.type == "text_delta"` → `("said", text)`. Handle `redacted_thinking` blocks by yielding nothing (thinking stays null).
   - `OllamaBackend`: Ollama's `/api/chat` supports `"stream": true` with per-line JSON `{"message": {"content": …}}`; yield `("said", content)`.
   - Base fallback: if a backend lacks `_chat_stream`, wrap `_chat` and yield its whole return as one `("said", …)` chunk — echo/scripted providers keep working with zero changes.
2. Thread an optional `stream_cb(kind, text)` through `_ModeAwareBackend.generate(contract_block, history, feedback, stream_cb=None)` → inner `generate(...)`. Default `None` preserves today's behavior exactly.
3. In the turn pipeline (where `Loop` calls `self.backend.generate(...)`), pass an emitter that calls `_emit` with the turn's `turn_id`. Emit `turn_start` before the call.
4. `harness_check` emission: wrap the existing journal-record calls (`judge_passed`, `turn_validated`, contract compile, approval gate) with a parallel `_emit` — one line, no behavior change.
5. `turn_result` enrichment: accumulate streamed `thinking` text (cap 8 000 chars, then `… [truncated]`) and the `checks` list into the result dict in `_emit_turn_result`.

Keys never appear in any new event (deltas are model text and harness verdicts only).

## 4. Webview rendering (pillar 2)

### 4.1 Streaming state machine (`webview/chat.js`)

- `streams: Map<turn_id, {card, thinkingEl, bodyEl, toolsEl, checksEl, thinkingBuf, saidBuf, checks}>`.
- On `turn_start`: build the card shell (chips + open Thinking section with pulsing bead + empty body + `.tools-live` + `.checks` containers), pin scroll state.
- On `thinking_delta` / `said_delta`: append to buffer, re-render that section only (thinking as escaped pre-wrap text; body as markdown — §4.2), update scroll only if pinned.
- On `tool_progress`: upsert the tool's row (start → pulsing gold bead; done → ok bead + summary + ms; error → danger bead).
- On `harness_check`: append a check row; also push to `checks` for the final summary.
- On `turn_result`: if a stream exists for the result's `turn_id`, finalize it in place (collapse Thinking, freeze tool rows, render final markdown once); else render the legacy block path (unchanged from today).
- Deltas for an unknown `turn_id` create a card on demand (robust against a missed `turn_start`); they never throw.
- Scroll pinning: `pinned = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 40`. Autoscroll only when pinned (Copilot behavior). A "↓ jump to latest" pill appears when unpinned and new content arrives (click → scroll to bottom, re-pin).

### 4.2 Markdown renderer (dependency-free, ~150 lines, no build step)

Escape HTML first, then render this subset (in order):

1. Fenced code blocks ` ```lang … ``` ` → §4.3.
2. ATX headings `#`/`##`/`###` → `<h3>/<h4>/<h5>` (capped to avoid giant type).
3. `**bold**`, `*italic*`, `` `inline code` ``.
4. Unordered lists (`-`, `*`) and ordered lists (`1.`) → `<ul>/<ol>`; nesting to 2 levels.
5. `[text](https://…)` links — `http(s)` only; anything else renders as plain text (no `javascript:`).
6. `> ` blockquotes → left gold-dotted border (echoes the thinking motif).
7. `---` → `<hr>`.
8. Tables: **not supported in 0.4.0** — rendered inside a code block, noted in the renderer comment (keeps the parser small and predictable).
9. Blank-line-separated paragraphs → `<p>`.

Re-render the full accumulated buffer on each `said_delta` (buffers are ≤ tens of KB; measured fine). The final `turn_result` re-renders once from `result.said`.

### 4.3 Code blocks with copy buttons

```
┌─ python ─────────────── [⧉ Copy] ┐
│ …code…                            │
└───────────────────────────────────┘
```

- Header bar: `background: var(--awino-bg-3)`, language label (vibranium, 11px, monospace), Copy button (secondary style, 11px).
- Copy: `navigator.clipboard.writeText` with `document.execCommand("copy")` fallback; button label flips to "Copied ✓" (uses `--awino-ok`) for 1.5 s.
- Body: `<pre>`, `var(--vscode-editor-font-family, monospace)`, 12px, horizontal scroll, `max-height: 400px` vertical scroll.

### 4.4 Diff display

Used in approval cards (`a.diff`) and fenced ` ```diff ` blocks:

- Line starting with `+` (but not `+++`) → `background: rgba(78,201,176,.14)`, `+` marker in `--awino-ok`.
- Line starting with `-` (but not `---`) → `background: rgba(241,76,76,.14)`, marker in `--awino-danger`.
- `@@ … @@` → vibranium, bold.
- Context lines → muted.
- Container keeps today's `max-height: 240px` scroll.

### 4.5 Stop / cancel during streaming

`#stop` posts `{type:"stop"}` as today. On `cancel_ack`, the in-flight card freezes: bead cluster shows grey, a muted "turn cancelled — effects already executed remain in the journal" line is appended (mirrors the sidecar's cancel semantics; nothing is un-rendered).

## 5. Thinking trace behavior (pillar 3)

- **During the turn:** Thinking section open at the top of the card, gold pulsing bead + streaming text (escaped pre-wrap, 11.5px). This is the "watch the model think" half.
- **Harness checks** stream into their own collapsible section below the body as `harness_check` events arrive — the "watch the harness check it" half. Both visible simultaneously: thinking on top, checks below the response body.
- **On `turn_result`:** Thinking collapses to a one-line summary (first 80 chars + "…" + char count). Checks stay expanded if any verdict is `fail`/`warn`, else collapse to `⬡ Harness checks · 4 passed`.
- **Provider matrix (honest):**
  - Anthropic with thinking enabled → native thinking blocks streamed.
  - Anthropic without thinking / OpenAI-compatible / Ollama / echo / scripted → `result.thinking = null`; section header reads "Thinking — not exposed by this provider". The Harness checks feed still streams (it comes from the harness, not the model), so the card is never empty.
- **Cap:** thinking buffer capped at 8 000 chars in the sidecar (`… [truncated]`); the webview additionally caps DOM text at the same bound.
- **Persistence:** thinking text is UI-only; it is NOT written to the journal, contract, or any `.awino/` file (reasoning traces can contain prompt-injected content; the journal keeps verdicts, not monologues).

## 6. File-by-file change list

| File | Change |
|---|---|
| `webview/chat.html` | Full CSS rework: §1 tokens (`body.vscode-dark` / `body.vscode-light` blocks), §2 component styles, bead + chevron + collapsible styles, streaming caret animation, "jump to latest" pill. No structural HTML changes except adding the pill element. |
| `webview/chat.js` | Add: streaming state machine (§4.1), markdown renderer (§4.2), code-block builder with copy (§4.3), diff renderer (§4.4), thinking + checks collapsible sections (§5), scroll pinning + jump pill, bead statusline (§2.8), `turn_start`/`thinking_delta`/`said_delta`/`tool_progress`/`harness_check` renderers. Keep: all existing event renderers, `send`/`stop`/`approve` message verbs, banner logic. |
| `webview/models.html` | §2.11 restyle: chevron band under `<h2>`, gold section labels, vibranium focus rings, key-state colors. No JS contract changes. |
| `webview/models.js` | No functional change. Optional: none. (Listed to record the decision: untouched.) |
| `src/extension.ts` | No routing changes required (default case forwards new events). Add: nothing. (Listed to record the decision: untouched.) |
| `src/views.ts` | `ModesView`/`ContractView`/`JournalView` text polish (§2.10): `✓`/`✕` prefixes, UPPERCASE phase. ~15 lines. |
| `src/sidecar.ts` | Untouched — the client is event-agnostic (`SidecarEvent` has index signature). (Listed to record the decision.) |
| `prototype/awino_sidecar.py` + `prototype/backends.py` | §3.2–3.3: `_chat_stream` generators, `stream_cb` threading, `turn_start`/`thinking_delta`/`said_delta`/`tool_progress`/`harness_check` emission, `turn_result` enrichment (`thinking`, `checks`). |
| `test/markdown.js` *(new)* | Fixture tests for the markdown renderer (run in node; renderer factored as a testable function — chat.js exposes `AwinoMarkdown` on `window` and as `module.exports` under node). |
| `test/streaming.js` *(new)* | Headless state-machine test: feeds a scripted event sequence (turn_start → deltas → tool_progress → harness_check → turn_result) into the chat.js stream logic via a minimal DOM shim; asserts card structure, collapse behavior, buffer caps. |
| `test/harness.js` | Extend: assert the new event order on a scripted streaming turn (`turn_start` first, ≥1 delta, `turn_result` last with `thinking`/`checks` fields); assert an old-style (non-streaming) turn still yields exactly today's events (back-compat). |
| `package.json` | `version` → `0.4.0`. No new dependencies (renderer is hand-written; zero runtime deps preserved). |
| `README.md` (extension) | Document the new events, the thinking provider matrix, and the markdown subset. |
| `proof/vscode_live_gui_test.md` | Append 0.4.0 section: new checks + screenshots (see §9). |

## 7. Effort estimate

| Step | Days |
|---|---|
| 1. Tokens + static chat.html restyle | 1.0 |
| 2. Markdown renderer + code blocks + diff (incl. `test/markdown.js`) | 1.5 |
| 3. Sidecar streaming protocol (Python) | 1.5 |
| 4. Webview streaming state machine (incl. `test/streaming.js`) | 1.0 |
| 5. Thinking trace + harness checks UI | 1.0 |
| 6. Models panel + tree view polish | 0.5 |
| 7. Live GUI proof (run + screenshot review + doc) | 0.5 |
| **Total** | **7.0** |

Steps 1–2 and step 3 are independent (webview vs sidecar) and can run in parallel; steps 4–5 depend on 2+3; step 6 depends on 1; step 7 depends on 4–6. No new dependencies, no Marketplace/identity work in this build.

## 8. Ordered build steps (fail-fast)

Each step lists its dependency, what is built, and its done-criterion. **If a step's done-criterion fails, stop — fix it before starting the next step. No stacking.**

**Step 1 — Tokens + static restyle.** Dep: none. Rewrite `webview/chat.html` CSS per §1–§2 (chat, cards, chips, approval cards, input, statusline beads, banner). No JS changes.
*Done when:* the file renders in plain Chromium with `body` class toggled `vscode-dark` / `vscode-light` / `vscode-high-contrast`; screenshots of both themes reviewed; every rule uses a `var(--vscode-*, <concrete fallback>)` pair (grep-verified, zero bare hex colors outside the token blocks).

**Step 2 — Markdown renderer.** Dep: step 1 (CSS classes exist). Implement §4.2–§4.4 in `chat.js` as `AwinoMarkdown` (testable under node), plus `test/markdown.js`.
*Done when:* `node test/markdown.js` passes — fixtures cover headings, bold/italic, inline code, fenced blocks (with ` ```diff `), lists (2-level), links (https + `javascript:` rejection), blockquote, hr, table-as-codeblock; an XSS fixture (`<script>`, `<img onerror>`) renders fully escaped, byte-checked.

**Step 3 — Sidecar streaming protocol.** Dep: none. Implement §3.2–§3.3 in `prototype/awino_sidecar.py` / `prototype/backends.py`.
*Done when:* extended `node test/harness.js` passes — a scripted streaming turn yields events in order `turn_start → (thinking_delta|said_delta|tool_progress|harness_check)+ → turn_result`, `turn_result.result` carries `thinking` and `checks`; a non-streaming turn yields exactly today's event set (back-compat); a 4 KB+ chunk is split across lines each ≤ 4 KB.

**Step 4 — Webview streaming.** Dep: steps 2, 3. Implement §4.1 in `chat.js` plus `test/streaming.js`.
*Done when:* `node test/streaming.js` passes — card shell on `turn_start`, incremental body growth, thinking accumulation with 8 000-char cap, tool row upsert start→done, check rows appended, `turn_result` finalizes in place (thinking collapsed, rows frozen), unknown-`turn_id` deltas create a card without throwing, scroll-pinning logic unit-verified; pressing Stop mid-stream freezes the card with the cancelled line.

**Step 5 — Thinking + checks UX.** Dep: step 4. Implement §5 behaviors (open-while-streaming, collapse rules, fail/warn keeps checks open, provider-matrix honest labels, no journal writes).
*Done when:* scripted provider turn (thinking = null) renders "not exposed by this provider" and still shows streamed harness checks; a synthetic thinking stream renders collapsed-with-summary after `turn_result`; journal export contains zero thinking text (grep-verified).

**Step 6 — Models panel + tree views.** Dep: step 1. Restyle `webview/models.html` per §2.11; `src/views.ts` text polish per §2.10; `tsc` clean.
*Done when:* `npx tsc -p ./` compiles clean; models panel screenshot reviewed in both themes; tree views render with new markers.

**Step 7 — Live GUI proof.** Dep: steps 4, 5, 6. Package 0.4.0 vsix; run the Playwright+Xvfb driver from `proof/vscode_live_gui_test.md` against it, extended with the §9 checks.
*Done when:* all checks green (34 legacy + new), screenshots visually inspected, `proof/vscode_live_gui_test.md` gains a dated 0.4.0 section, and the vsix is **not** published (publish gate unchanged).

## 9. Test plan

### 9.1 Unit (node)

- `test/markdown.js` (new): fixture-driven, byte-exact HTML assertions; XSS fixtures; `javascript:`-link rejection; table fallback.
- `test/streaming.js` (new): DOM-shim-driven state machine test (§8 step 4 criteria).
- Existing `test/harness.js`, `test/bedrock.js`, `test/connection_importer.js`: must stay green unchanged (except the planned harness.js extension).

### 9.2 Headless sidecar (Python + node)

- Event-order test on a scripted streaming turn (step 3 criteria).
- Back-compat test: 0.3.0-era webview logic (the `default:` case) against the new sidecar — renders without exceptions.
- Line-length test: no stdout line exceeds 4 KB of text payload.

### 9.3 Live GUI proof (modeled on `proof/vscode_live_gui_test.md`)

Extend `~/workspace/vscode-test-env/gui_test.py` with new checks (target: 34 legacy + 12 new = 46):

| # | Check | What it proves |
|---|---|---|
| 35 | stream_incremental | scripted turn with 3+ chunks visibly grows the card (DOM text length increases between polls) |
| 36 | thinking_section_present | Thinking collapsible exists on the turn card |
| 37 | thinking_collapses | after `turn_result`, Thinking is collapsed with an 80-char summary |
| 38 | thinking_honest_null | scripted provider shows "not exposed by this provider" |
| 39 | harness_checks_render | ≥1 `⬡ Harness checks` row rendered with a verdict pill |
| 40 | tool_progress_rows | tool row transitions start→done with bead + ms |
| 41 | copy_button | code block Copy button flips to "Copied ✓" on click |
| 42 | diff_colors | approval-card diff has both add/remove line backgrounds |
| 43 | beads_statusline | 5-bead cluster present; link bead vibranium when connected |
| 44 | wakandan_css_vars | computed styles: chat bg is dark blue-black (`#0B101B`-family) under `vscode-dark` |
| 45 | jump_to_latest | unpinning scroll + new delta shows the jump pill; click re-pins |
| 46 | no_thinking_in_journal | journal export contains no thinking text |

New screenshots appended to `proof/vscode_gui/`: `16_streaming_midturn.png`, `17_thinking_collapsed.png`, `18_harness_checks.png`, `19_approval_wakandan.png`, `20_models_wakandan.png`. Every screenshot visually inspected, same bar as the 0.1.0 set. Proof doc gains a dated 0.4.0 section; the run must exit 0.

## 10. Non-goals (explicitly out of 0.4.0)

- No new providers, no SSO/SigV4 work, no Marketplace publishing.
- No markdown tables, no LaTeX, no mermaid (subset is §4.2, frozen).
- No webview state persistence across reloads (streaming cards rebuild from `turn_result` on reconnect — same as today).
- No changes to the approval decision path (modal stays authoritative), the contract pipeline, judges, or journaling semantics.
- No light-theme background artwork — superseded 2026-09-23: Savanna is a full Wakandan light variant (§1.1a), not a plain token set.

## 11. Risks

1. **SSE parsing fragility** across OpenAI-compatible endpoints (vLLM, llama.cpp, OpenRouter dialects). Mitigation: the base fallback (non-streaming `_chat` → single chunk) keeps every provider working; streaming is best-effort per backend with the fallback always available.
2. **Thinking availability** varies by provider/model and by account settings (Anthropic). Mitigation: the honest-null path (§5) is a first-class UI state, not an error.
3. **Re-render cost** on every delta for long turns. Mitigation: buffers are small; if a turn exceeds 50 KB of `said`, switch body updates to append-only text mode (documented degradation, still correct).
4. **Scope creep on motifs.** Mitigation: §1.2 freezes the motif inventory (chevron band ×2 locations, beads, dotted gold border). Anything else needs a spec amendment.
