# Hotfix Conflict Analysis: feature/recursive-loop vs hotfix/chat-persist-053

**Date:** 2026-09-25
**Merge-base:** 96f5575 (Release 0.5.2)
**Feature branch:** feature/recursive-loop @ 598824a (+ controller review fixes)
**Hotfix branch:** hotfix/chat-persist-053 (v0.5.3: 2a780a0, 1bcd652; v0.5.4: ab19b6f, b9da1a1)

## Summary

`feature/recursive-loop` was branched from 0.5.2 (96f5575) BEFORE the hotfix.
It contains NONE of the hotfix changes. The eventual v0.6 merge MUST port all
of the following; they will not merge cleanly via git because v0.6 rewrote the
surrounding code (recursive loop, tool dispatch, contract modes).

## 1. Interview `set_mission` tool (hotfix commit 2a780a0) — MUST RETAIN

### 1a. prototype/tools.py — TOOL_DEFS entry
**Hotfix hunk** (tools.py:20-26):
```python
"set_mission": {"consequential": False, "args": ["text", "criteria"]},
```
With comment: harness-owned (not a Sandbox method); non-consequential;
dispatched by Loop._execute_single.

**v0.6 status:** MISSING. v0.6 TOOL_DEFS has no set_mission.
**Conflict:** v0.6's `_execute_single` was replaced by `_intercept_harness_calls`
(HARNESS_TOOLS) + sandbox dispatch. There is no `_execute_single` special-case
slot anymore.
**Integration:** Add the TOOL_DEFS entry AND a dispatch path. Do NOT put it in
`HARNESS_TOOLS` (contract.py:61) — those are offered in EVERY mode, but
set_mission must be observe/plan-only. Recommended: keep it as a harness-owned
tool dispatched in the sandbox-tool path (like the hotfix did), gated by
contract MODES.

### 1b. prototype/loop.py — _tool_set_mission() method
**Hotfix hunk** (loop.py, new method after _execute_single):
- `def _tool_set_mission(self, args: dict) -> dict`
- Refuses workers: `if self.state.snapshot.get("worker_id"): return {"error": ...}`
- Validates text non-empty, criteria is str
- Splits criteria on `re.split(r"[;\n]+", raw_criteria)`
- Calls `self.set_mission(text.strip(), criteria)` (revision-tracked)
- Returns `{"ok": True, "mission": {...}, "said": ...}`
- Tool errors are data (dict), never exceptions

**v0.6 status:** MISSING. No _tool_set_mission.
**Conflict:** v0.6 has `_execute_harness_tool` for HARNESS_TOOLS, but set_mission
is not a HARNESS_TOOL. Needs a new dispatch branch.
**Integration:** Port the method verbatim. Wire it into the tool execution path
for the set_mission tool name.

### 1c. prototype/contract.py — MODES observe/plan
**Hotfix hunk** (contract.py:18-32):
```python
"observe": {"tools": ["read_file", "list_dir", "set_mission"], ...}
"plan":    {"tools": ["read_file", "list_dir", "set_mission"], ...}
```
**v0.6 status:** v0.6 MODES observe/plan have `["read_file", "list_dir",
"search_files", "find_symbol", "git_status", "git_diff", "diagnostics"]` —
NO set_mission.
**Conflict:** Direct hunk conflict on the tools lists.
**Integration:** Append "set_mission" to observe and plan tools. Do NOT add to
build/verify (hotfix deliberately excludes it; build has write tools, and the
interview tool is meaningless there).

### 1d. prototype/backends.py — Ollama system prompt
**Hotfix hunk** (backends.py _OLLAMA_SYSTEM): documents
`set_mission {"text", "criteria"}` ("criteria is one string: the done criteria
separated by semicolons; call it when the discovery interview has converged").
**v0.6 status:** v0.6's prompt does not mention set_mission.
**Integration:** Add the set_mission documentation to the system prompt.

### 1e. prototype/awino_sidecar.py — tool profile + Interview stance
**Hotfix hunks:**
- `_apply_sidecar_tool_profile()`: documents set_mission as the interview
  convergence tool.
- BUILTIN_MODES Interview stance_prompt: "When the user's answers make the
  objective and done criteria crisp, call set_mission... Do not keep asking
  once it is crisp."

**v0.6 status:** MISSING (only the sidecar COMMAND _set_mission exists, which
is different — that's the operator command, not the model tool).
**Integration:** Port both documentation hunks.

### 1f. prototype/tests/test_set_mission_tool.py — NEW FILE (115 lines, 8 tests)
**v0.6 status:** MISSING.
**Integration:** Port the file. It tests the tool dispatch, worker refusal,
criteria parsing, and validation. May need adaptation for v0.6's dispatch path.

### 1g. prototype/tests/test_fanout.py — worker gate expectation
**Hotfix hunk** (test_fanout.py:224-230): The worker's tool gate is the
intersection of the worker's mode tools with the parent's policy; set_mission
(observe/plan-only) is NOT offered to workers. Comment explains: "A worker's
mission is fixed by its parent, so dropping it from the gate is correct."

**v0.6 status:** v0.6 loop.py:3262 computes
`parent_tools = list(MODES[parent_mode]["tools"]) + list(HARNESS_TOOLS)`.
If set_mission is in MODES["observe"]["tools"], a worker spawned from an
observe-mode parent would inherit it unless explicitly filtered.
**Integration:** When porting set_mission, ensure the fanout worker gate
excludes it (filter it out, or assert the intersection drops it). Port the
test expectation.

## 2. Backend error detail (hotfix commit 2a780a0) — MUST RETAIN

**Hotfix hunk** (backends.py, OllamaBackend.generate exception handler):
```python
# Before: f"backend error: {type(e).__name__}"
# After:  f"backend error: {type(e).__name__}: {e}"
```
Example: "backend error: RuntimeError: endpoint HTTP 404" (diagnosable) vs
"backend error: RuntimeError" (undiagnosable). Comment notes key material never
appears (key travels in Authorization header, not URL/exception text).

**v0.6 status:** v0.6 backends.py:432 still has the OLD format:
`f"backend error: {type(e).__name__}"` (no message).
**Conflict:** Direct line conflict; v0.6 did not pick up the hotfix.
**Integration:** Change to `f"backend error: {type(e).__name__}: {e}"`. Check
other backends (Anthropic, Bedrock) for the same pattern — the hotfix only
changed OllamaBackend, but v0.6 should apply the principle consistently.

## 3. Chat transcript persistence (hotfix commit 2a780a0) — MUST RETAIN

**Hotfix files:**
- `integrations/vscode/extension/src/chatHistory.ts` (NEW, 43 lines):
  ChatHistory host buffer (cap 500).
- `integrations/vscode/extension/src/extension.ts` (+52 lines):
  retainContextWhenHidden + chatReady handshake replays transcript and
  re-pushes fresh chrome. State/binding/session-resume excluded from
  persistence.
- `integrations/vscode/extension/webview/chat.js` (+4 lines): posts chatReady.
- `integrations/vscode/extension/webview/chat.html` (1 line): script wiring.
- `integrations/vscode/extension/test/chatHistory.js` (NEW, 54 lines, 8 tests)
  wired into package.json test script.
- `integrations/vscode/extension/test/streaming.js` (+6 lines): chatReady
  handshake coverage.

**v0.6 status:** ENTIRELY MISSING.
- src/chatHistory.ts: does not exist.
- extension.ts: has `retainContextWhenHidden: true` (line 2600) but ZERO
  chatReady references.
- webview/chat.js: zero chatReady references.
- test/chatHistory.js: does not exist.
- package.json test script: does not include test/chatHistory.js.

**Conflict:** extension.ts was heavily modified by v0.6 (sidecar streaming,
approval cards). The chatReady handshake hunks will not apply cleanly; they
need manual porting around v0.6's webview message handling.
**Integration:** Port chatHistory.ts, the chatReady handler in extension.ts,
the webview chatReady post, and the tests. This fixes tab-switch wiping the
visible session.

## 4. Header model picker (v0.5.4, commits ab19b6f + b9da1a1) — MUST RETAIN

**Hotfix files:**
- `integrations/vscode/extension/src/extension.ts` (+80 lines):
  `pickModelFromHeader()` — provider pill opens model QuickPick (discovered
  models, current checked, manual entry, Models & Providers shortcut).
  Writing awino.model marks settings dirty → existing Spec 3.1 reconnect flow.
- `integrations/vscode/extension/webview/chat.html` (2 lines), `webview/chat.js`
  (+13 lines): pill click wiring.
- `integrations/vscode/extension/test/chatSetup.js` (+8), `test/streaming.js`
  (+10): updated for new pill behavior.

**v0.6 status:** ENTIRELY MISSING. Zero references to pickModelFromHeader.
**Conflict:** extension.ts header area was touched by v0.6; manual port needed.
**Integration:** Port pickModelFromHeader and the pill wiring. The v0.5.4
release (vsix-v0.5.4) is already shipped; v0.6 must not regress it.

## 5. Non-conflicting hotfix items (already safe or trivial)

- `package.json` version bumps (0.5.3, 0.5.4): v0.6 will set its own version.
- `CHANGELOG.md` entries: merge textually.
- All other prototype files: v0.6 is a superset (recursive loop, new tools).

## Integration order recommendation

1. Port backend error detail (1-line, zero risk).
2. Port set_mission tool (1a-1g) — needs v0.6 dispatch design decision.
3. Port chat transcript persistence (extension).
4. Port header model picker (extension).
5. Run full prototype suite + npm test + tsc.
