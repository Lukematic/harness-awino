# proof

Evidence that the harness does what the docs claim: scripts that drive it
against hostile, scripted or local models, the transcripts and logs they
wrote, and screenshots of the VS Code UI.

These are records from the time each phase was built. The test suite in
`prototype/tests/` is what keeps the claims true today. Several scripts
below were written against older code or an author's home directory and
no longer run as-is.

## Scripts

Run from `prototype/` with `PYTHONPATH=.` (for example
`cd prototype && PYTHONPATH=. python3 ../proof/kilo_integration_proof.py`).

| Script | What it proves | Runs today? |
|---|---|---|
| `kilo_integration_proof.py` | The MCP server end to end over stdio. | Yes |
| `feature1_judges_proof.py` | The judge panel's quorum and fail-closed rules (plus a live panel if a local model is up). | Yes |
| `feature2_synthesis_proof.py` | Skill synthesis admits only verified skills. | Yes |
| `proof_session.py` | One mission against an adversarial model; writes `TRANSCRIPT.md`. | No (IndexError; the mission flow changed since) |
| `phase_b_proof.py` … `phase_e_proof.py`, `phase_a_*.py` | Phase A–E exit criteria. Wrote the `PHASE_*_TRANSCRIPT.md` files. | No (hard-coded author paths, older flow) |
| `live_local_model_test.py`, `live_test1_retry.py`, `phase_a_skills_live.py`, `phase_a_approval_kill.py` | Early runs against a local llama.cpp model. | Needs a local model server |

## Folders

| Folder | What |
|---|---|
| `live_mission/` | `run_live.py`: the literature-review mission against a real model. Used by the `live-mission` workflow. See its README. |
| `demo-agentic-learning/` | The same mission with hand-written model replies (`run_mission.py`), and `record_video.js`, which replays it in the real chat UI. Not a live run. |
| `competitor-teardown/` | UX comparison with Roo, Kilo, Cline and Copilot Chat (2026-09-23, Awino 0.3.0). |
| `vscode_gui/`, `ui-0.7/`, `chat-ui-steps-1-2/` | Screenshots of the extension in real VS Code. |

## Records

- `TRANSCRIPT.md`, `PHASE_*_TRANSCRIPT.md`, `KILO_INTEGRATION_TRANSCRIPT.md`,
  `LIVE_TRANSCRIPT.md`: transcripts written by the scripts above.
- `*.log`: test and proof runs, each repeated 3 times.
- `vscode_live_gui_test.md`: the extension driven in real VS Code under Xvfb.
- `windows_test_checklist_050.md`: the 0.5.0 Windows checklist. The current
  one is [docs/testing/windows.md](../docs/testing/windows.md).
