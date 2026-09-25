# A.W.I.N.O. v0.7 — Build Plan ("Honda runs")

**For:** the builder model (any tier). Controller: the user + a reviewing session.
**Rule zero:** Honda before Bugatti. Nothing in P1/P2 starts until every P0 task is green
in a real VS Code window. No visual polish until the engine works end to end.

Read first: `ARCHITECTURE.md`, the builder prompt rules below, and the evidence section.

---

## Why this plan exists (evidence, 2026-09-25, real VS Code on Windows, v0.6.0)

Field screenshots show the harness failing its first real session:

| # | Expected | Actual | Specifically why |
|---|---|---|---|
| E1 | "check `…\.awino` for mission information" reads that folder | Lists the workspace root, every turn, 9 turns in a row | The model is told `list_dir {} (takes no arguments)` (`prototype/backends.py:328`). `_apply_sidecar_tool_profile()` (`awino_sidecar.py:78`) tries to rewrite that sentence with `str.replace`, but the base text has drifted, so the replace silently no-ops. Verified: after the profile runs, `'list_dir {} (takes no arguments)' in backends._OLLAMA_SYSTEM` is `True`. |
| E2 | Tool turns answer | `Safe fallback: no valid model output (backend error: RuntimeError)`, then "What should I do next?" | Native-tools path sent no `Authorization` → HTTP 401. Fixed in 0.6.1 (on `main`), **not yet shipped as a VSIX**. The fallback also hides the real error behind a question. |
| E3 | "lets set mission" converges on a mission | Stays IDLE, asks "what objective?" and repeats | Mission lives nowhere the chat can see (E4); interview prompt has no path from "the answer is already in the project" to `set_mission`. |
| E4 | CLI and VS Code share one project memory | VSIX state in `~/.awino-loop/projects/<name>` (`awino_sidecar.py:2494`); CLI uses `<project>/.awino`. They never meet. | Two stores. |
| E5 | Chat reads like a conversation | Every reply begins `[A.W.I.N.O. \| phase: IDLE \| mode: observe \| stance: advisor \| skills: \| loop: 6 …] STANCE -> advisor (default) … Floor: IDLE \| Next action: … \| Blocked on: …` | Contract header and footer are rendered as prose. |
| E6 | One clear "what is it doing now" | Dropdown says **Interview**, header says **mode: observe**, **stance: advisor**, chip says **IDLE** | Four overlapping words (phase, permission mode, role lens, stance) shown raw. This is what reads as "decorative". |
| E7 | One status line | "Sidecar ready — project …" printed 4× | Each reconnect appends a chat message (`webview/chat.js:1089`). |
| E8 | Quiet when nothing to show | "Thinking not exposed by this provider" on every turn | Placeholder always rendered. |

---

## Status

Done on `claude/native-tools-auth-hotfix-bhsr45` (tests in `tests/test_tool_prompt.py`,
`tests/test_stances.py`, `tests/test_story.py::StoryPlanToolTest`):

- **T1 done.** The tool list in the turn prompt is generated from `TOOL_SCHEMAS`
  (`tool_schema.tool_catalog`); the sidecar string patch is gone. Also fixed: the
  sidecar `search_files` rejected the schema's `file_glob`/`top_k` (TypeError on
  native-tool calls).
- **Challenge routing (new, was missing):** "my idea…", "what about…", "is this a good
  idea", "challenge me", "should we…", "X vs Y" now fire steel-man / premortem
  instead of the generic advisor. "lets set mission" / "I want to build…" open the
  planning-grill interview. With no mission, a build/verify/ship ask routes to the
  interview first instead of a write-capable mode.
- **T10 model tool done.** `story_plan` (observe/plan, workers refused) writes the
  Honda-first spine, seeds the DAG, passes the BUILD gate, and closed stories land on
  the brag board. Planner role prompt tells the model to use it. Still to do in T10:
  the sidecar `stories`/`story_start`/`story_close` commands and the VS Code view.

- **End-to-end loop proven** (`tests/test_e2e_mission.py`): one mission through the real
  loop — grill → `set_mission` → approve → steel-man challenge (hollow answer rejected by
  the rubric) → `story_plan` → scoped approval → write → tests fail → auto back to BUILD →
  fix → tests pass → verifier worker → REVIEW → evidence-gated completion → SHIP → story
  closed onto the brag board, journal chain intact. Defects it found, now fixed: failed test
  runs never routed VERIFY → BUILD; `task_update` could not close the plan's DAG tasks and
  could not pass evidence (verifier always failed); harness tool results had no
  `tool_called`; a call paused for approval reused the next round's call id.

Everything else below is still open.

---

## Builder rules (from the A.W.I.N.O. builder prompt — non-negotiable)

1. Keep going. Don't stop on routine decisions; pick the option that advances fastest.
2. Proof, not claims. A task is done only when its acceptance test passes **and** you
   ran it. Paste the command + result into the commit body.
3. Report every failure as **Expected / Actual / Specifically why**. Evidence before repair.
4. Build, test, commit locally. The controller pushes and ships.
5. Stop only for: needs the user, or destructive (delete data, force-push, outside repo).
6. Never weaken a test or a gate to get green. Never skip hooks.
7. Credentials are the user's. Never configure keys.

Fast checks before every commit:

```sh
cd prototype && python -m unittest discover -s tests -t . -q      # engine
cd integrations/vscode/extension && npm test && npx tsc --noEmit    # extension
```

Publish gate (controller): install the built VSIX in real VS Code (Windows + Linux),
run the 30-second smoke test in `README.md`, plus the P0 acceptance script below.

---

## P0 — Make it actually work (ship as 0.7.0-beta.1)

### T1. Generate the tool list; never string-patch prompts
- **Files:** `prototype/backends.py` (~L328), `prototype/awino_sidecar.py` (`_apply_sidecar_tool_profile`, ~L78–120), `prototype/tool_schema.py`.
- **Change:** build the "Available tools: …" sentence from `tool_schema.TOOL_SCHEMAS` (name + required/optional params + one-line description) for the tools actually offered this turn. Delete the `str.replace` patch. `tools.py` `"list_dir": {"args": []}` → `["path"]` (optional).
- **Accept:** new `tests/test_tool_prompt.py`: (a) for every offered tool, every schema parameter name appears in the rendered prompt; (b) `list_dir` is described with `path`; (c) no string `takes no arguments` for a tool that has parameters; (d) scripted backend asked "list the .awino folder" receives a turn whose `list_dir` call carries `path=".awino"` and the result lists `.awino` contents.

### T2. Absolute and Windows paths resolve inside the workspace
- **Files:** `prototype/tools.py` `Sandbox._resolve`.
- **Change:** accept absolute paths (incl. `C:\…` and forward-slash forms) that fall inside the workspace; convert to relative. Outside the workspace → `{"error": "outside workspace: <path>"}` (never silently the root).
- **Accept:** tests for `C:\\proj\\.awino`, `C:/proj/.awino`, `/abs/proj/.awino`, `..\\x` (refused), empty → root.

### T3. Real errors, not a polite question
- **Files:** `prototype/loop.py` safe-fallback path; `webview/chat.js`.
- **Change:** on backend error, emit an `error` event with `{kind, detail, fix}` (e.g. `endpoint HTTP 401` → "Key rejected by <host>. Open Models & Providers."). Render as an error card with a button. Do not append "What should I do next?".
- **Accept:** stub server returning 401/404/500/timeout → one card each, correct text; no key material in the event (reuse `test_native_tools_auth` stub).

### T4. One project memory
- **Files:** `prototype/awino_sidecar.py` (~L2494 state root), `prototype/state.py`.
- **Change:** VSIX state lives in `<workspace>/.awino` (same as CLI). On first run, if `~/.awino-loop/projects/<name>` exists and `<workspace>/.awino` has no snapshot, migrate it once and journal `state_migrated`. Keep the home dir only for user-global settings.
- **Accept:** test: mission created via `cli.py` is shown by the sidecar `session_resume`; mission set in the sidecar is listed by `awino status`.

### T5. Interview that converges
- **Files:** `awino_sidecar.py` `BUILTIN_MODES` interview/planner prompts; `bootstrap.py` session start.
- **Change:** at session start in IDLE, if `STORY.md`, `.awino/seeds/*.md`, or an open story exists, the resume card lists them with "Resume this" buttons (→ `mission_from_seed` / set mission). Interview prompt: "If the user points at files, read them first. If they contain an objective and done criteria, propose `set_mission` with them in the same turn."
- **Accept:** scripted: project with a seed file; user says "lets set mission" → the turn calls `read_file` on the seed, then `set_mission`; phase leaves IDLE within 2 turns.

### T6. Chat reads like a chat
- **Files:** `webview/chat.js`, `webview/chat.html`, `src/extension.ts`.
- **Change:** parse the contract header `[A.W.I.N.O. | k: v | …]`, the `STANCE -> …` line and the `Floor: … | Next action: … | Blocked on: …` footer out of the reply text. Header → the status chips (already exist). Footer → a one-line "Next:" hint under the message. Body = the prose only. Confidence tags (`[Certain]`) stay inline. "Sidecar ready" → status-bar dot only; chat shows it once per session. Hide the thinking disclosure when there is no thinking.
- **Accept:** `test/` unit test feeding the E5 sample text renders body without header/footer; reconnect ×4 yields 1 chat line; GUI assertion A4 screenshot.

### T7. Ship 0.6.1 now (controller, parallel to T1–T6)
- Build VSIX from `main`, tag `vsix-v0.6.1`, attach to a GitHub pre-release. (The tag push from the cloud session got HTTP 403 — push the tag from a machine with repo rights.)

**P0 acceptance script (real VS Code, Windows):** fresh project with a seed → open sidebar → "lets set mission" → mission set from the seed → approve → it writes, tests, repairs, completes with an evidence card. Zero raw header text, zero repeated "Sidecar ready", every error readable.

---

## P1 — Make modes, stances and stories real, not decorative (0.7.0)

### T8. One vocabulary on screen
- **Change:** keep the engine's four concepts but show them as one "Now" line: **Phase** (IDLE → DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP) · **Role** (Interview, Planner, Architect, …) · **Stance** (advisor, planning-grill, …). The permission mode is implied by phase and shown only in the tooltip. Rename the header dropdown label to "Role".
- **Accept:** no UI string says "mode:" next to a role name; tooltip lists allowed tools for the phase.

### T9. Mission bar with the phase stepper
- **Change:** pinned bar above chat: objective, stepper for phases with the current one lit, done criteria with ✓/✗, and on a refused transition the gate's plain reason (`VERIFY -> REVIEW needs a passing verification…`).
- **Accept:** scripted mission passes through every phase; bar updates each round; a forced illegal jump shows the refusal text.

### T10. Stories + brag board in VS Code (the ledger already exists in `story.py`)
- **Sidecar commands:** `stories` (counts, needs-attention, parked, brag board = last N done with outcome + time dedicated), `story_start {title, type}`, `story_close {id, outcome}`, `story_plan` (same args as below). Register in `_do_command` handlers (`awino_sidecar.py` ~L4025).
- **Model tool `story_plan`** (harness-owned like `set_mission`; offered in observe/plan only; workers refused): string args `title, story_id?, problem, done_criteria (;-separated), breakdown, surveyed, user_guidance, proposal (must contain [Certain]/[Likely]/[Guessing]), steps (one per line: "title | success | failure"), bugatti_brief`. Resolves to `Loop.plan_story` (`loop.py` ~L479). Add to `contract.MODES` observe/plan, `tools.TOOLS`, `tool_schema.TOOL_SCHEMAS`, `Loop._resolve_tool_fn`, and the worker exclusion next to `set_mission` in fan-out.
- **Planner role prompt:** "Plan with the user: Honda first (the committed scope), Bugatti pitched in brief, never built unasked. When the user agrees, call story_plan."
- **UI:** "Stories" view replaces nothing — new tree view + "Brag board" section; palette commands *Awino: Stories*, *Start Story*, *Close Story*. Close authority stays with the user (the harness only asks on `story_ready_to_close`).
- **Accept:** scripted plan conversation → `story_plan` writes the six-part spine, DAG seeded, BUILD gate passes; closing a story puts it on the brag board with outcome; tests mirror `tests/test_story.py`.

### T11. Stance and role switching the user can drive
- **Change:** clicking the Role/Stance chip opens a QuickPick; choice calls existing `mode` / `mode_invoke` commands; a `/stance <id>` and `/role <id>` slash command in chat. Auto-routing continues when the user hasn't pinned one; pinned shows a pin icon.
- **Accept:** pin stance → next 3 turns use it (journal shows `stance` per round); unpin → router resumes.

---

## P2 — Copilot-grade chat (0.8.0)

Take items from the spec doc "A.W.I.N.O. — Spec: Beat Cline, Roo & Copilot" (release 0.7 row there): stream native-tools turns (`"stream": false` today in `_chat_tools`), thinking for every provider that supplies it, @-mentions, retry/edit/stop, token + cost meter. Same format: files → change → acceptance test.

---

## Hand-off format (per task)

Commit title `T<n>: <what>`; body:

```
Expected: …
Actual (before): …
Specifically why: …
Test: <command>  →  <result line>
```
