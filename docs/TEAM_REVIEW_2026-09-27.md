# Team review — 2026-09-27

Seven reviewers tested the harness end to end: a UI tester, a session tester,
a bug fixer, a documentation specialist, a technical user, a Cline/Cursor
builder and a software engineer. No model API key or VS Code GUI was
available, so every run used the echo or scripted providers and a headless
browser against the real chat webview. A live model has still not been run
(see "Still open").

## Fixed in this round

| # | Finding (who) | Severity | Fix | Proof |
|---|---|---|---|---|
| 1 | The model never saw tool output: `read_file` → "N chars read", commands → the last 200 chars of stdout (builder) | P0 | Full result up to 12k chars per call; 12-entry history within a 60k budget | `test_model_io` |
| 2 | Output capped at 1,024 tokens: writes over ~3 KB truncated (builder) | P0 | 8,192 default; `AWINO_MAX_TOKENS` overrides | `test_model_io` |
| 3 | Anthropic tools rounds hit `/v1/chat/completions` without a key: 401 on every round (builder) | P0 | Native tools on `/v1/messages` with `x-api-key` | `test_model_io` (wire test) |
| 4 | Two threads broke the journal hash chain with no tampering (session) | P0 | Lock in `ProjectState.record` | `test_recovery` (fails 5/5 without the lock) |
| 5 | kill -9 mid-turn left the session stuck for good (session) | P0 | Reads re-run automatically; writes ask "applied / not applied" | `test_recovery` |
| 6 | A `.venv` made the SHIP scan 11 MB and SHIP unpassable; no-commit repos skipped the scan (SE) | P0 | Vendor dirs excluded; empty-tree diff | `test_floor_scan` |
| 7 | VERIFY trusted any command (`echo ok`) (SE) | P0 | Harness runs the project's `test` and `lint` recipes | `test_verify_recipe` |
| 8 | Extension wrote README/lessons/spec/docs into the repo unasked (SE, technical user) | P0 | Only `.awino/` without consent; setup autopilot proposes chores | `test_setup_autopilot` |
| 9 | Wizard showed Bedrock/AWS fields for every provider (UI) | P0 | `[hidden]` wins | UI probe |
| 10 | `chat.py --help` created a `--help/` project (technical user) | P0 | Prints usage, writes nothing | `test_packaging` |
| 11 | `pip install` shipped 13 of 38 modules, so `awino` crashed (docs, SE) | P1 | All modules packaged; CI installs and imports | `test_packaging`, `ci.yml` |
| 12 | Three approval paths (modal + card + slash text); cards stayed live; View diff blocked by the modal (UI) | P1 | The card is the one path; it updates to "Approved ✓ / Denied"; no modal | extension tests, screenshots |
| 13 | Header overflowed at 320–400 px; Savanna theme broken in the wizard and buttons; Stop always on (UI) | P1 | Wrapping header, theme tokens, Stop only while a turn runs, Esc stops, draft while streaming | screenshots, probe |
| 14 | Receipts said "chain intact" without checking the hash chain (session) | P1 | Receipts check `verify_chain` + pairing | — |
| 15 | A corrupt journal line crashed startup (session) | P1 | Keep the valid prefix, set the file aside, tell the user | `test_session_start` |
| 16 | Session-start messages were lost (session) | P1 | Notices ride on the `ready` event: lessons, open stories, setup, repairs | `test_session_start` |
| 17 | "plan the fix" in PLAN routed build mode and was rejected 4× (technical user) | P1 | The phase caps the mode before BUILD | `test_stances` |
| 18 | BUILD gate pointed at a nonexistent `story_update` (technical user) | P0 | Points at `story_plan` | — |
| 19 | No CI for the engine; 29 lint findings; duplicate state keys (SE) | P1 | `ci.yml` (ruff clean enforced, engine + extension tests, install check), repo `justfile`, 0 findings | CI |
| 20 | Clean-tree checkpoints pointed at a missing stash, so revert failed | P1 | "clean" checkpoint | `delegatedApply.js` |
| 21 | No first-run guide; "echo is a mock" undocumented; stale claims (docs) | P0 | `docs/GETTING_STARTED.md`, README fixes | — |
| 22 | Receipts, lessons, story close edge cases (bug fixer: 12 bugs) | P1–P2 | See commits 60d08cb…5c92a39 | 19 new tests |
| 23 | Jargon in chat: `mode:`/`persona:` chips, raw event JSON, "MCP: []" (UI) | P2 | Phase chip plus plain status; internal events not shown | `toolSummary.js` |

## Still open, ranked

1. **Live-model run (step 0).** Nothing here has been tested with a real
   model. It needs `ANTHROPIC_API_KEY` or `AWINO_API_KEY` as a repo secret,
   then the `live-mission` workflow.
2. **Token cost.** The contract averages about 3k words a round and peaks at
   5k. Its first line changes every round, so prompt caching can't work.
   Move the header out of the prefix and trim the fixed sections. (builder #2)
3. **Ceremony.** A 2-line fix takes about 7 clicks: contract approval, scope
   approval, per-write approvals. Merge mission, plan and scope into one
   approval and add saved auto-approve rules (by path or command prefix).
   (builder #3, #5; technical user)
4. **Story time tracking.** Work is recorded as 0s, and time keeps counting
   while the sidecar is closed. (session #7)
5. **Receipt windows overlap** when you switch between stories. Events need a
   story id. (bug fixer)
6. **Lesson false positives.** Test-first red runs become "check failed"
   lessons, and verdicts name mission criteria rather than story criteria.
   (session #8)
7. **Checkpoints** keep only the last one and are lost on restart. Make one
   per round, with a restore timeline. (builder #4)
8. **Project rules and context.** Awino doesn't read AGENTS.md, CLAUDE.md or
   .cursor/rules, has no @-mentions, and `read_file` can't read a line range.
   (builder #6)
9. **`synthesis.py` and `memory_store.py` are dead ends.** Wire them in or
   remove them. (SE, session)
10. **Large files.** `loop.py` (Loop is about 3,750 lines), `awino_sidecar.py`
    and `extension.ts` should be split by concern. (SE)
11. **UI polish.** The cancelled state still pulses, the empty-state hint is
    large and sticky, some wizard labels aren't `<label>` elements, and
    command names are unclear (Housekeep, Doctor, Rigor Report). (UI #7,
    #10, #11, #13)
12. **Docs.** Move the specs and plans into `docs/history/` and add a glossary
    to the extension README. (docs)

## What the harness now does for you

- **At session start:** it proposes a justfile for your language, missing
  `.gitignore` lines, a keys-only `.env.example` and an `.editorconfig`. It
  applies only what you approve, never overwrites a file, and remembers "never".
- **At VERIFY:** it runs your `test` and `lint` recipes itself. A failure goes
  back to BUILD with the output.
- **At SHIP:** it scans the change, not your environment, for stubs and
  secrets, including in repos with no commits yet.
- **After a crash:** it re-runs interrupted reads, asks about interrupted
  writes, repairs a corrupt journal and tells you.
- **After every story:** receipt → lessons → the next session's prompt.
