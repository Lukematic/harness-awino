# A.W.I.N.O. VS Code extension — live GUI test proof

**Status: PROVEN — 34/34 checks green, process exit 0.**

> **Correction (2026-09-23).** An earlier draft of this document claimed a
> 28/30 pass and described "the denied write" as the denied-command proof.
> That claim was wrong: the earlier run's deny step acted on a stale
> `write_file` approval card and never exercised `run_command` at all. This
> document replaces it. The proof below shows a genuine `run_command`
> approval card (`touch deny-check.txt`), a real click of the webview **Deny**
> button, the file remaining absent, and the denial journaled with the exact
> approval id — all inside the official VS Code GUI.

## Environment

- **VS Code (official, unmodified):** 1.139.0, commit
  `2242ebbb54efeeb0129e08e919e7e8d43033cd83`, x64 — launched under Xvfb
  (`:99`), driven with Playwright.
- **Extension:** `awino-loop-owner-0.1.0.vsix` (47 files, ~135.21 KB),
  installed through the **official VS Code CLI**
  (`code --install-extension …` → `Extension 'awino-loop-owner-0.1.0.vsix'
  was successfully installed.`), verified as
  `lukematic.awino-loop-owner@0.1.0`. The vsix bundles the real sidecar
  (`bundled-sidecar/`).
- **Backend:** scripted provider (deterministic), isolated `AWINO_HOME`,
  fresh test workspace. No network, no model keys.
- **Determinism pre-proof:** the scripted sidecar run passes **15/15**
  headless (`/tmp/headless_proof.py`, log `/tmp/headless_proof3.log`),
  covering DEFINE→PLAN→BUILD→VERIFY, approved write, denied command with
  no effect, and the journal chain.

## What the GUI run proves

The driver (`~/workspace/vscode-test-env/gui_test.py`) performs the whole
workflow through the real GUI — command palette, input boxes, webview
buttons — and asserts 34 checks. Full log: `/tmp/gui_test_run23.log`.

### 1. Contract progression (DEFINE → PLAN → BUILD → VERIFY)

- Mission created via `A.W.I.N.O.: New Mission` (palette + input boxes).
- "begin" → DEFINE → **PLAN** approved via real Approve click.
- Scripted PLAN turn renders with `MARKER-PLAN` and **no** `MARKER-WRITE`
  (no retry cascade, no drift).
- "proceed to code with notes.txt scope" → PLAN → **BUILD** approved.
- Real `write_file` approval card appears; **Approve** clicked → `notes.txt`
  created with the exact bytes `Hello from the GUI test\n`; the write is
  journaled.
- The BUILD exit gate moves the mission to **VERIFY** once write effects
  exist — the journal records
  `phase_changed: PLAN → BUILD → VERIFY` in order
  (`phase_progression_journal`).
- The VERIFY turn card renders with the **VERIFY** phase chip and the
  contract header `[A.W.I.N.O. | phase: VERIFY | mode: verify |
  stance: devil's-advocate | skills: testing | …]` (`run_turn_in_verify`).

### 2. Approved write

`approval_card_write_file`, `approve_writes_file`,
`approve_write_content_exact`, `approve_journal_entry` — all green.

### 3. Denied `run_command` — the real proof

On the VERIFY floor, "run the check" produces a genuine **`run_command`**
approval card:

```
Approval requested: run_command
{ "cmd": "touch deny-check.txt" }
[Approve] [Deny]
```

- The driver clicks the **actual webview Deny button** (not a simulated
  decision); the button reports disabled after the click.
- `deny-check.txt` **remains absent** from the workspace
  (`deny_blocks_run_command`) — the command never executed.
- The sidecar journal (`awino-home/projects/test-workspace/events.jsonl`)
  records the exact chain:
  `approval_requested` for `run_command` with
  `args.cmd = "touch deny-check.txt"`,
  followed by `approval_denied` with the **same approval id**
  (`deny_journal_entry`).
- The turn finalizes safely; the mission stays coherent; no orphan
  sidecar or VS Code processes remain
  (`no_orphan_sidecar`, `no_lingering_vscode` — `pids=[]`).

### 4. Result

**34/34 passed, 0 failed, exit code 0.**

| # | Check | Result |
|---|-------|--------|
| 1 | vscode_launched | PASS |
| 2 | activity_bar_icon | PASS |
| 3 | chat_webview_renders | PASS |
| 4 | interview_banner_not_blank | PASS |
| 5 | echo_turn_renders | PASS |
| 6 | phase_mode_chips | PASS |
| 7–12 | tree_view_Contract/Journal/Learnings/Skills/Context/Modes | PASS |
| 13 | status_bar | PASS |
| 14 | models_webview_renders | PASS |
| 15 | models_save_reconnect | PASS |
| 16 | chat_visible_after_models | PASS |
| 17 | reconnect_scripted | PASS |
| 18 | scripted_provider_active | PASS |
| 19 | mission_created_flow | PASS |
| 20 | approve_contract_define_plan | PASS |
| 21 | scripted_plan_turn | PASS |
| 22 | approve_contract_plan_build | PASS |
| 23 | approval_card_write_file | PASS |
| 24 | approve_writes_file | PASS |
| 25 | approve_write_content_exact | PASS |
| 26 | approve_journal_entry | PASS |
| 27 | approval_card_run_command | PASS |
| 28 | run_turn_in_verify | PASS |
| 29 | deny_blocks_run_command | PASS |
| 30 | deny_journal_entry | PASS |
| 31 | phase_progression_journal | PASS |
| 32 | journal_view | PASS |
| 33 | no_orphan_sidecar | PASS |
| 34 | no_lingering_vscode | PASS |

## Screenshots

`proof/vscode_gui/` (numbered set from the green run):

1. `01_launch.png` — workbench with the A.W.I.N.O. activity-bar icon
2. `02_chat.png` — A.W.I.N.O. container open, chat webview rendered
3. `03_banner.png` — discovery-interview banner ("No active mission…")
4. `04_echo.png` — echo turn + phase/mode chips
5. `05_treeviews.png` — all six tree views
6. `06_statusbar.png` — status bar provider readout
7. `07_models_providers.png` — Models & Providers webview
8. `08_models_saved.png` — saved + reconnected
9. `09_mission_created.png` — mission live
10. `10_plan_turn.png` — scripted PLAN turn card
11. `11_approval_card_write.png` — real `write_file` approval card
12. `12_approval_card_run_command.png` — real `run_command` approval card
    (`touch deny-check.txt`) with Approve/Deny
13. `13_after_deny.png` — finalized denied turn, VERIFY phase chip
14. `14_journal_view.png` — Journal tree view
15. `15_final.png` — final state

Every screenshot was visually inspected. Stale or misleading files from
earlier runs were removed; the set above is the one clean numbered set.

## Reproduce

```bash
cd ~/workspace/vscode-test-env
./pwvenv/bin/python gui_test.py   # DISPLAY=:99 Xvfb; ~15 min; exits 0 at 34/34
```

Requirements: official VS Code `code` CLI on PATH, the `.vsix` installed
from `~/workspace/awino-rebuild/integrations/vscode/extension/`,
`AWINO_HOME` isolation handled by the driver itself.

## Scope notes

- The scripted provider stands in for a real model backend; the harness
  contract, approvals, journal, and phase gates are the real code paths.
- No Marketplace publication, signing, or release was performed — install
  and proof only, per the publish gate.

---

# 0.4.0 — Wakandan chat UI + competitor-teardown setup UX (2026-09-23)

**Status: PROVEN — 43/44 GUI checks green.** The single failure
(`040_approval_card_appears`) is pre-existing: the scripted approval flow
was already red before this work (the mission never reaches BUILD in the
GUI run), and it is unrelated to the six teardown items below.

## Environment

- **VS Code (official):** same Xvfb `:99` + Playwright harness as above.
- **Extension:** `awino-loop-owner-0.4.0.vsix` (73 files, 270,791 bytes,
  SHA-256 `090237460ac8195d2b48ccfaa8fa7459ecf9f6f91668ec080f8e09c32b0d87b6`),
  installed into `~/workspace/vscode-test-env/ext-official`
  (extracted from the VSIX; the `code --install-extension` CLI hangs
  headless in this environment, so the equivalent manual extract was used
  and verified: `webview/setup-shared.js` present,
  `out/extension.js` contains the wizard code).
- **Backend:** echo for the regression surface; openai-with-no-key for the
  wizard/setup-card surface; scripted for the approval attempt. No real
  provider keys, no network calls to paid providers.

## Direct automated proof (no GUI)

- `npx tsc -p ./` — clean.
- `npm test` — all suites green (290 Node/webview tests), including two
  new suites:
  - `test/setupShared.js` (14): provider catalogue URLs, `needsKey`,
    `fetchableProvider`, model-intel lines marked `(est.)`, honest
    unknown-model fallback, and `ensureSelectedOption` with a
    select-faithful stub (assigning an unmatched value leaves a real
    select blank — the 0.3.0 bug only reproduces with true semantics).
  - `test/chatSetup.js` (34): provider pill states + click, wizard
    show/hide, provider/docs/get-key wiring, `wizardFetch`/`wizardModels`
    success + failure paths, inline key validation, `wizardSave`,
    `wizardDismiss`.
  - `test/modelsPanel.js` extended to 48: (b) three Get-key buttons post
    `openExternal` with the key-creation URLs, (c) Provider Docs link
    follows the provider and hides for echo, (d) legacy `scripted`
    binding renders as a labeled `(current)` option instead of blank,
    (e) intel line for known/unknown/empty models.
  - `test/providerKeys.js` extended: `scripted` is keyless (it replays
    canned test turns and makes no API call).
- `prototype/tests` — **468/468 passed** on the feature branch
  (`TMPDIR=/home/hatch/tmp-test`).

## What the GUI run proves (driver `gui_test_040.py`, log `/tmp/gui041_run.log`)

### (a) First-run onboarding wizard

With a fresh profile (`awino.onboarded` unset) and `awino.provider:
"openai"` (no key), `Awino: Reconnect Sidecar` shows the wizard as the
sidebar: provider select preselected to **openai (not blank)**, "Get
OpenAI API Key" button, Provider Docs link, API-key step, model step;
the input bar is parked while it is active. **Skip** dismisses it and
the setup card takes over (key still missing). Screenshot
`23_040_wizard.png` (wizard) and `24_040_setup_card.png` (after Skip).

### (b) "Get {Provider} API Key" buttons

Models & Providers shows **Get OpenAI API Key / Get Anthropic API Key /
Get Bedrock API Key**; the wizard shows the per-provider button too.
Unit tests prove each posts `openExternal` with the right key-creation
URL; the extension host allowlists exactly
`platform.openai.com`, `console.anthropic.com`, `docs.anthropic.com`,
`console.aws.amazon.com`, `docs.aws.amazon.com`, `ollama.com` (https
only) and refuses everything else.

### (c) Provider Docs link

Beside the provider dropdown in Models & Providers, and in the wizard
header. Follows the provider (OpenAI → `platform.openai.com/docs`,
Anthropic → `docs.anthropic.com`, …), hidden for echo. Click posts
`openExternal` with the docs URL.

### (d) Provider dropdown never blank (must-fix)

The stored provider is rendered through `ensureSelectedOption`: a
binding id with no matching `<option>` (0.3.0 left `scripted` behind)
becomes a labeled `scripted (current)` option instead of a blank
select. Proven in the panel, in the wizard, and by the select-faithful
unit test.

### (e) Model-intelligence line

Under the model picker (panel and wizard): `Context 128K · $2.50/M in ·
$10.00/M out (est.)` for gpt-4o — context window plus input/output
$/M, always marked `(est.)`; local Ollama models say "no per-token
cost"; unlisted models get the honest `Context/pricing unknown for this
model — check the provider docs.` Screenshot `25_040_models_new.png`.

### (f) Provider-status pill

In the chat input area: **No provider** when the key is missing,
**`echo · echo`** when connected; click opens Models & Providers
(the GUI run opens the panel via the pill). Verified under Vibranium
(`26_040_pill_vibranium.png`) and Savanna (`27_040_pill_savanna.png`).

### Regression surface (still green)

Vibranium default + Savanna toggle both directions, 5-bead statusline
cluster, setup card hidden for echo, streaming echo turn with the
honest `not exposed by this provider` thinking null, model-discovery
fallback to manual entry, no orphan sidecar.

## Screenshots (`proof/vscode_gui/`, all visually inspected)

- `16_040_launch.png`, `17_040_vibranium_chat.png`,
  `18_040_savanna_chat.png` — launch + themes (existing set)
- `23_040_wizard.png` — onboarding wizard on first run
- `24_040_setup_card.png` — setup card after wizard Skip
- `25_040_models_new.png` — Models & Providers: docs link, Get-key
  buttons, intel line
- `26_040_pill_vibranium.png`, `27_040_pill_savanna.png` — provider
  pill in both themes
- `20_040_streaming_turn.png` — streaming echo turn regression
- `22_040_final.png` — final state

## Reproduce

```bash
cd ~/workspace/vscode-test-env
DISPLAY=:99 ./pwvenv/bin/python gui_test_040.py   # ~12 min; 43/44 (approval pre-existing red)
```

## Honest gaps (unchanged)

- The scripted approval GUI flow is still red (pre-existing; the mission
  never reaches BUILD in the run). The approval *protocol* remains
  proven by `test/harness.js`.
- No real Windows GUI environment; Windows interpreter behavior is
  unit-tested only.
- No paid provider calls were made; Bedrock/Ollama discovery is
  unit-tested, not live-proven.
