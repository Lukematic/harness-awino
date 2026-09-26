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

- **T4 done.** Session state lives in `<project>/.awino/projects/<slug>/` for the sidecar and
  `awino chat` (which also stops putting every project in one "inbox" session). Old
  `~/.awino-loop` state is copied in once, never deleted; `.awino/.gitignore` keeps journals
  out of git. `tests/test_project_home.py` runs the real sidecar and reads its mission from a
  CLI loop.
- **T10 done (VS Code).** Sidecar `stories` / `story_start` / `story_focus` / `story_close`;
  a Stories panel (in progress, open, blocked, parked ideas, brag board with date, time
  and outcome); commands to start, resume, and close stories; the session-start card lists
  open stories and due parked ideas; the extension asks once to close a story the verifier
  marked ready. Also: "Sidecar ready" shows once per project (part of T6).
  `tests/test_sidecar_stories.py`, `test/storiesView.js`. Not yet clicked through in a live
  VS Code window (T25).

- **T14 done.** The turn may declare `stance` + `stance_why`; the harness accepts only the
  phase's allowed stances, always runs the phase floor rubric too (PLAN/BUILD
  first-principles, VERIFY devil's-advocate, REVIEW/SHIP premortem), journals the model's
  reason, and falls back to the router when nothing is declared. The contract lists the
  menu outside the 600-word core budget. `tests/test_model_stance.py`.

- **T17 done.** `stretch_goal` tool (observe, plan, ship; workers refused): Need,
  Approach, Benefits, Competition plus 3–5 steps with success and failure criteria;
  filed as a parked spike with a revisit date (default 30 days), shown in the Stories
  panel and on the session-start card when due. The planner role is told to use it.
  `tests/test_story.py::StretchGoalToolTest`.

- **T6 done.** Chat shows the reply as prose: the contract header and `STANCE ->` line are
  dropped (the chips already show them), questions and assumptions become lists, and the
  `Floor | Next action | Blocked on` footer becomes one "Next" line. The thinking row is
  hidden when the provider sent none. `test/harnessReply.js`, `test/streaming.js`.
- **T3 done.** A failed model call names the cause and the fix (401/403 key, 404
  endpoint/model, 429, 5xx, timeout, unreachable, non-JSON) instead of asking "What should I
  do next?"; still a safe no-tools turn. `tests/test_ollama_backend.py::ExplainFailureTest`.


- **T25 partial.** The branch VSIX installs and passes the GUI proof in real VS Code on
  Windows (`windows-gui-test` runs 27 and 28, green; VSIX published as the `awino-vsix`
  artifact). A sandbox mission (agentic-learning literature review, 7 approaches) ran
  through the real sidecar with a scripted model and passed all 14 concept checks
  (`proof/demo-agentic-learning/report.md`, video via `record_video.js`). Still open: a
  mission against a live model with the user's key, and a per-mode walk in VS Code.
- **T26 done** for README, CHANGELOG, ARCHITECTURE and CAPABILITY_REGISTRY. Seen in the
  recorded run and still open: role names shown as "mode:" chips (T8), and noisy
  "approval recorded; 1 still pending" lines.

Everything else below is still open.

**Build order (agreed 09-26):** T4 one memory → T10 stories + brag board in VS Code →
T14 model-chosen stances → T17 stretch goals → rest of P0 (T3, T5, T6) → T25 live beta
run → T26 docs. Items marked **needs you** in the audit wait on the user.

---

## Instruction audit (Sep 22–25, all 227 messages)

Every distinct request from the three days of instructions, checked against the code on
this branch. **Done** = code + tests exist; *(engine)* = works in the Python harness but
the VS Code extension doesn't expose it yet. Nothing below has been proven in a live VS
Code window with a real model — that is the publish gate (T25).

### Setup, environment, safety

| When | Request | Status | Evidence / task |
|---|---|---|---|
| 09-23 05:31, 10:18, 10:24, 10:48 | Plug-and-play init: detect/create/activate `.venv`, just/make, ruff/uv, project yaml, `.seeds`, startup checklist, runs without being asked | Done *(engine)* | `bootstrap.py`, `awino init`, `project-bootstrap` skill; live VSIX run unverified → T25 |
| 09-23 05:50 | Housekeeping on compaction with user check; README in every folder; archive old scripts | Partial | `housekeeping` command + `archive/` exist; README-per-folder check missing → **T12** |
| 09-23 10:18 | Skills can't leak data; guard against prompt injection / poisoning | Done | SHA-pinned skill manifest, injection scan (`tests/test_skill_security.py`) |
| 09-25 05:58 | PII never goes out; agent never runs destructive commands | Partial | Secret redaction + `run_command` approval exist; no destructive-command guard, no PII scrub of outbound prompts → **T13** |
| 09-23 11:50, 09-24 08:07–08:10 | Windows hardening, bundle Python, Windows CI | Done | Bundled CPython (0.5.0), `.github/workflows/windows-gui-test.yml` |

### Modes, stances, personas

| When | Request | Status | Evidence / task |
|---|---|---|---|
| 09-23 05:37 | Modes: debug engineer, storyboard, architect, planner, tutor; drop a skill in as a temporary persona | Done | `BUILTIN_MODES` (architect, code, debug-engineer, interview, planner, release, review, storyboard, test, tutor); `persona_assume` |
| 09-23 10:49 | Modes switch on their own | Done, trigger-word based | Router + phase elevator; move to model-chosen stances with phase floors → **T14** |
| 09-23 05:37, 12:25 | Plan mode understands the user **and challenges** them | Done (this branch) | Challenge routing + steel-man rubric; `tests/test_e2e_mission.py` |
| 09-23 09:51 | Persona from the LinkedIn posts (steel-man, Feynman tutor, writing, presentations MIT/Jobs, "come let us reason") | Partial | steel-man, feynman, storyboard exist; writing-style and motto not audited → **T15** |
| 09-23 09:51 | Astra planning frame: GOAL, CONTEXT, PRIORITY, AUTONOMY, TOOLS, OUTPUT, VERIFICATION, STOP CONDITION | Done | `contract.py` sections |
| 09-23 10:32–10:36 | Role packs: AI researcher, SWE, FDE, AI architect, cyber; verification at the end | Done | `mode-*` skills; verifier worker |

### Mission, stories, brag board

| When | Request | Status | Evidence / task |
|---|---|---|---|
| 09-23 12:07, 12:23 | One `STORY.md`; branch per story; session start lists open stories; ask to close; story owns many seeds; time dedicated; brag board | Done *(engine)* | `story.py`, `tests/test_story.py`; not in VS Code → **T10** |
| 09-23 12:23 | Year-end: "what did I do, completed, summarized, with dates" | Partial | Brag board shows last 5 done; no dated yearly summary → **T16** |
| 09-23 12:25, 12:33 | First principles, surveyed approaches, user guidance, innovative pitch, Honda/Bugatti, steps with success **and** failure criteria, fail fast | Done | `plan_story` six-part spine; `story_plan` tool (this branch) |
| 09-23 12:33 | Brief NABC pitch; parked ideas revisited monthly; white paper on request | Partial | Parked + 30-day revisit + `expand_bugatti` exist; no NABC shape, no step breakdown with benefits → **T17** |
| 09-23 09:51 | Memory registry, milestones, breadcrumbs, reflection, lessons log | Done | `registry.py`, learnings, SHIP reflection pass |
| 09-23 12:23 | Session tied to an issue; warn about stale/long-running ones | Done *(engine)* | `stale_stories`, `stories_nudge` → surface in **T10** |
| 09-23 12:23 | Git: branch per issue, push, merge request on close | Partial | Branch on `story_start`; best-effort push + PR on close; no push-cadence rule → **T10** |
| 09-26 | CLI and VS Code share one memory per project | Not done | VSIX uses `~/.awino-loop/projects/…` → **T4** |

### Providers and setup

| When | Request | Status | Evidence / task |
|---|---|---|---|
| 09-23 14:19 | OpenAI-compatible: fetch the model list; key alias field | Done | Discovery + labels (0.5.4) |
| 09-23 05:37 | Keys per project and per environment | Done | `env_switch` |
| 09-23 12:37–12:57, 09-25 08:08 | Bedrock by ARN / AWS profile; reuse Claude `settings.json` / `settings.local.json` and Kilo configs with permission | Done | `bedrock.ts` (SigV4), `connection_importer.ts` |
| 09-25 10:14 | Pick the model in chat after entering a key | Done | Header model picker (0.5.4) |
| 09-25 (screens) | Tool turns 401 on keyed endpoints | Fixed, not shipped | 0.6.1 on `main`; VSIX + tag pending → **T7** |
| 09-23 12:44, 09-23 05:32 | Use the harness inside Claude Code (Bedrock) | Done | `integrations/claude` (hooks, agents, commands) |

### Chat UI

| When | Request | Status | Evidence / task |
|---|---|---|---|
| 09-23 13:47–13:58 | Wakandan theme, a lighter variant | Done | Vibranium + Savanna themes in `webview/` |
| 09-23 13:46 | Copilot smoothness, visible thinking | Partial | Thinking for Anthropic only; raw contract text in chat → **T6**, P2 |
| 09-24 06:25 | Tasks panel with check-offs | Done | `TasksView` |
| 09-25 10:15 | Changing tabs wiped the session | Done | 0.6 chat persistence |
| 09-25 10:14, 10:20 | Installed, model works, "nothing executes" | Fixed *(engine)* | Recursive loop (0.6), 401 fix, repair loop (this branch); live proof → **T25** |
| 09-23 14:15 | Nowhere obvious to enter the key | Done | Onboarding wizard + setup card (0.5.x) |
| 09-23 13:54, 09-25 06:48 | "Awino" is a proposed name; shorter logo | Not done | Name baked into IDs and strings → **T18** (needs your pick) |

### Coding-agent gaps from the 09-25 reviews

| When | Request | Status | Evidence / task |
|---|---|---|---|
| 09-25 08:44–09:13 | Recursive model→tool→result loop; patch editing; native diff + terminal + checkpoints + multi-file undo; real cancel; compaction; hooks | Done | v0.6.0 changelog; e2e test |
| 09-25 08:44 | Debug / reproduce → diagnose → fix workflow | Partial | `debug`, `rpi` skills; no dedicated debug phase test → **T19** |
| 09-24 22:41–22:44 | Codebase indexing / repo map ("build this feature") | Not done | → **T19** |
| 09-25 08:44 | Evidence card, judge panel visibility, "why was this blocked" | Not done | → **T3**, **T20** |
| 09-25 08:44 | Token + cost meter | Not done | → P2 |
| 09-25 08:44 | Detect files changed outside the harness; invalidate scope | Not done | `git_status`/`git_diff` tools only → **T21** |
| 09-25 08:44 | Command capability model (network, filesystem, timeout) | Not done | → **T13** |
| 09-25 08:44 | MCP server permissions UI; fan-out (workers) UI | Not done | Engine done → **T22** |
| 09-25 09:07–09:09 | Register as a native VS Code chat participant + `LanguageModelTool`s ("Ok let's do it") | Not done | → **T23** (spike: keep the sidecar as loop owner) |
| 09-24 22:41 | Inline autocomplete | Not done | Out of scope per spec; **needs your call** |

### Process, skills, research

| When | Request | Status | Evidence / task |
|---|---|---|---|
| 09-24 22:56 | Metrics coach for engineering practice (agent-rigor) | Done | `rigor.py`, `awino rigor`, extension rigor tests |
| 09-25 06:00–06:08 | Osmani skills, adoption guide, failure modes, scope, lifecycle | Done | `osmani-*`, `lifecycle-sequence`, `rigor-scope` |
| 09-25 06:23 | From harness-skills: incident-response, deployment-readiness, dora-metrics, debug-pipeline | Done (3 of 4) | `incident-response`, `dora-metrics`, readiness folded into `osmani-shipping`; debug-pipeline → **T19** |
| 09-25 06:29–06:31 | agent-house: verification budget, back-pressure, charter; journal→trace exporter (`awino profile`) | Not done | → **T24** |
| 09-25 06:33–06:41 | mempalace; claude-code-build-system standards | Not evaluated | Read and decide → **T24** |
| 09-25 06:18 | Audit `services/` for the retry bug in "the linked issue" | Blocked | The issue link was never provided — **needs you** |

### Added after a full read of part 3 and the repo docs (09-26)

The first pass read only half of the Sep 25 transcript and skimmed the repo docs.
These rows come from the rest.

| When / source | Request | Status | Evidence / task |
|---|---|---|---|
| 09-23 05:31; 09-25 08:46 review | Web fetch tool | Not done | → **T27** |
| 09-25 08:46, 09:10 reviews | Tool suite: create/delete file, git log/show/branch, VS Code problems, read command output | Partial | `write_file` creates; `diagnostics` is a Python syntax check only → **T27** |
| 09-25 08:46, 09:10 | Model-callable `switch_mode`, `spawn_agent`, `ask_user`, `update_plan` | Partial | Phases move by gates; fan-out exists but only the harness calls it; questions/plan are turn fields → **T28** |
| 09-25 08:44, 09:10 | Approval bound to file hash: file changed since approval → approval invalid | Not done | Approvals bind args + mission revision + scope epoch → **T21** |
| 09-25 08:44 | Stop that says what already ran vs what was prevented | Partial | Cooperative cancel (0.6); UI says "effects already executed remain" without listing them → **T29** |
| 09-25 09:10 | Read project instructions (AGENTS.md, CLAUDE.md, copilot-instructions) and a repo profile at bootstrap | Not done | → **T19** |
| 09-25 09:10 | A written run contract: what every run guarantees (goal, scope, mode, tools, approval policy, budget, evidence, completion, recovery, audit) | Not done | → **T24** (charter) |
| 09-25 09:10 | Study Cline SDK, OpenHarness, HarnessOS | Not evaluated | Reference reading, record adopt/skip → **T24** |
| 09-25 06:18 | "Audit services/ for the retry bug in the linked issue" | Likely pasted example text | Came with the dynamic-workflows blog link; no services/ in this repo |
| `prototype/docs/STORY_PLANNING.md` | "No separate forward-thinking mechanism — the mandatory Bugatti in every plan is it" | **Conflict** | T17 added `stretch_goal` as a second channel. Reconcile: keep it only for ideas outside a story plan (e.g. at SHIP), or fold it into the Bugatti/parked flow → **needs your call** |
| `BUILD_SPEC.md` §3, `ARCHITECTURE.md` | Stances "code-routed, never model-chosen" | **Changed on request** | T14 (09-26) lets the model choose within phase limits; modes stay code-routed. Docs to update → **T26** |

### Testing and distribution

| When | Request | Status | Evidence / task |
|---|---|---|---|
| 09-24 08:13–13:27, 09-25 03:14 | Beta tester per function and mode, UI tester, reviewers; beta test then retest | Partial | Scripted e2e + GUI assertions A1–A3; no per-mode run in live VS Code → **T25** |
| 09-23 14:20 | Benchmark against the competitors with live keys | Not done | Head-to-head in the spec doc → after T25 |
| 09-23 14:01, 09-25 06:46–06:52 | Install from a GitHub link | Done | Releases page + one-line install in README |
| 09-25 06:48 | Scan then make the repo public | **Needs you** | Your call to flip visibility |
| 09-23 13:20–13:45 | Marketplace publisher account | **Needs you** | Account sign-in is yours |
| 09-25 06:54, 07:46 | Docs are outdated (README says 745 tests; CAPABILITY_REGISTRY stale) | Not done | → **T26** |
| 09-24 06:28–06:32 | OpenCode surface | Deferred | VS Code first (builder prompt) |

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


### T12. Housekeeping that keeps folders documented
- **Change:** `housekeeping` checks every tracked folder for a README (skip `.git`, `node_modules`, build output), lists missing ones, and asks before archiving stale scripts to `archive/<date>_<name>`. Runs on compaction with a user prompt, never silently.
- **Accept:** fixture tree with 3 undocumented folders → report names all 3; archive only after approval.

### T13. Command safety and outbound PII
- **Change:** `run_command` guard: refuse or force explicit approval for destructive patterns (`rm -rf` outside workspace, `git push --force`, `git reset --hard`, `drop table`, `mkfs`, `curl … | sh`) with a plain reason. Outbound prompt scrub: emails, phone numbers, keys, tokens replaced by placeholders before the provider call; journal records the count, never the value.
- **Accept:** each pattern refused with its reason; a prompt containing an email and a key reaches the stub server scrubbed.

## P1 additions — the harness as partner (0.7.x)

### T14. Model-chosen stances with phase floors (agreed 09-26)
- **Change:** every turn declares `stance`, `skills`, and a one-line `why`. The harness accepts any stance allowed in the phase, enforces floors (VERIFY includes devil's-advocate, PLAN includes first-principles), runs that stance's rubric, and logs the reason. Trigger words become the fallback when the model declares nothing.
- **Accept:** scripted model picks steel-man without a trigger word → rubric runs; picks a stance outside the phase → refused; declares nothing → router fallback.

### T15. Persona pass
- **Change:** audit the steel-man, Feynman, writing and presentation skills against the source posts; add the "come let us reason" partner persona to the base contract.
- **Accept:** each persona skill has a rubric-backed stance or is documented as prompt-only.

### T16. Year-end brag report
- **Change:** `awino brag [--since YYYY-MM-DD]` and a VS Code "Brag board" view: done stories grouped by month with closed date, outcome, time dedicated, and linked commits. Export to Markdown.
- **Accept:** fixture ledger across two years → correct grouping and totals.

### T17. Stretch goals the harness proposes (NABC)
- **Change:** after a plan is agreed, the model pitches one stretch goal in NABC form (Need, Approach, Benefits, Competition). If the user wants it, it's broken into 3–5 steps, each with success and failure criteria. Parked by default with a revisit date; surfaced at session start when due.
- **Accept:** pitch refused without all four parts; accepted pitch creates a parked story; revisit date arrival shows in the session-start review.

## P2 additions — coding-agent depth (0.8.x)

### T19. Repo map + debug workflow
- **Change:** on init, write `.awino/repo-map.json` (languages, frameworks, test runner, entry points, generated folders, commands) from deterministic scans; rank relevant files for a mission from names, imports, symbols and git history. Add a reproduce → diagnose → fix → retest test covering the `debug` skill and a CI-failure (`debug-pipeline`) variant.
- **Accept:** fixture repos (Python, TS) produce correct maps; a seeded bug mission reproduces before patching.

### T20. Evidence card, judge panel, refusal explanations
- **Change:** chat renders per-criterion evidence at completion, the judge panel's per-check verdicts, and each refusal as "what was asked / why blocked / what unblocks it".
- **Accept:** GUI assertion screenshots for each.

### T21. External edits invalidate scope
- **Change:** file watcher on the approved scope; a change not made by the harness journals `external_change` and requires re-approval before the next write.
- **Accept:** edit a scoped file mid-mission → next write paused with the reason.

### T22. MCP permissions and worker view
- **Change:** per-server READ / WRITE / DENY in settings, enforced at the tool gate; a view of fan-out workers with status and budget.
- **Accept:** DENY server's tools never offered; worker view matches journal.

### T23. Native chat participant spike
- **Change:** prototype `@awino` via `vscode.chat.createChatParticipant` and file/terminal tools as `LanguageModelTool`s, with the Python sidecar still owning the loop. Decide keep-or-drop from the spike.
- **Accept:** one mission runs through `@awino` with the same journal as the sidebar.

### T24. Process tooling from agent-house and others
- **Change:** verification budget (cap verify rounds per mission), back-pressure (pause intake when open stories exceed N), a one-page `CHARTER.md`, and `awino profile --mission <id>` exporting the journal as trace JSON. Read mempalace and claude-code-build-system; record adopt/skip.
- **Accept:** budget and back-pressure tests; profile output validates against the trace schema.

## Release gate and docs

### T25. Per-mode beta run in real VS Code
- **Change:** a scripted GUI run (Windows + Linux) that walks every mode and the e2e mission from `tests/test_e2e_mission.py` against a real provider key supplied by the user at run time. Expected / Actual / Why per step.
- **Accept:** all steps pass, screenshots in `proof/`.

### T26. Docs match the code
- **Change:** README (test counts, install, what works), CHANGELOG 0.7 entry, CAPABILITY_REGISTRY statuses from this audit, ARCHITECTURE for the new loop behavior.
- **Accept:** every number and status in the docs traces to a test or file.

### T27. Missing everyday tools
- **Change:** `web_fetch` (read-only, network-declared, text extracted, size-capped, approval in plan/observe), `delete_file` (consequential, scoped), `git_log`/`git_show`, and VS Code Problems via the extension (`vscode.languages.getDiagnostics`) replacing the Python-only `diagnostics` when the extension is attached.
- **Accept:** each tool has a schema, a mode entry, a gate test, and appears in the generated catalog.

### T28. Model-callable control tools
- **Change:** `request_phase` (asks for a transition; gates still decide), `spawn_research` (read-only fan-out worker with its own context, result synthesized back), `ask_user` (structured question that pauses the loop).
- **Accept:** `request_phase` to BUILD without approval is refused with the gate's reason; research worker cannot write; ask_user pauses and resumes.

### T29. Stop that reports what happened
- **Change:** on cancel, the chat lists tool calls completed, the one interrupted, and those prevented.
- **Accept:** cancel mid-round → three lists match the journal.

### T30. Receipts (the architect's proposal, Honda steps 1–3) — DONE
- **Change:** `prototype/receipt.py` builds promise → proof → lesson from the journal on `story_close`; `story_plan` steps carry an optional forecast; the extension shows a receipt card with *Copy as PR description*.
- **Accept:** `tests/test_receipt.py` (12), `test/receiptCard.js` (8), sidecar `receipt` command in `tests/test_sidecar_stories.py`. A story closed without a passing verdict reads UNVERIFIED; a criterion the verdict didn't name reads unproven.
- **Open:** step 0 (live mission with the user's key) still gates everything; receipts from a live run are the real test.

### T18. Name (needs the user)
- **Change:** once a name is chosen, centralize display name and IDs so a rename is one change.

---

## Hand-off format (per task)

Commit title `T<n>: <what>`; body:

```
Expected: …
Actual (before): …
Specifically why: …
Test: <command>  →  <result line>
```
