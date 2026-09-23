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
