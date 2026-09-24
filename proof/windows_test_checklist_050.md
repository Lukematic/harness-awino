# Awino 0.5.0 — Windows zero-setup test checklist

**Status: CI workflow in place, awaiting first green run.**

Proves on real Windows (`windows-latest`) what Linux cannot: the bundled
`python/win32-x64/python.exe` actually executes. Driver:
`.github/workflows/scripts/win_gui_proof.py`, run by
`.github/workflows/windows-gui-test.yml` (push to
`feature/bundled-python-050`, or **Run workflow** manually via
`workflow_dispatch`).

## What the CI run asserts (in order)

1. `tsc --noEmit` clean.
2. Full `npm test` green (includes the new `connectGuard.js`,
   `vsixContents.js`, and the extended python/pythonRecovery/tasksView
   suites).
3. `vsce package` → `awino-loop-owner-0.5.0.vsix` contains
   `extension/python/win32-x64/python.exe` (≥100 win32-x64 entries),
   the bundled sidecar, and **no** `.pbs-cache/` tarball cache.
4. VS Code stable installs the VSIX via the real CLI
   (`lukematic.awino-loop-owner` in `--list-extensions`).
5. VS Code launches with **no system Python on its PATH**
   (the driver scrubs every `*python*` PATH entry and all `PYTHON*`
   variables from the child process environment).
6. After `Awino: Reconnect Sidecar`, an `awino_sidecar` process exists
   whose **command line names `python\win32-x64\python.exe`**
   — the hard zero-setup proof.
7. The chat statusline reads `connected`.
8. A scripted mission is created (`Awino: New Mission`), its contract
   approved, and one canned turn executes end to end.
9. The mission header renders with the mission name.
10. `Awino: Save Current Mission as Seed` → the Tasks panel lists the
    registry task `win-task-seed`.
11. No orphan `awino_sidecar` processes after VS Code exits.

## Artifacts (uploaded as `windows-gui-proof`)

- `proof/win_gui/01_win_launch.png` … `06_win_tasks_panel.png`
- `proof/win_gui/proof.log` — the check-by-check log
- `proof/win_gui/vscode.log` — VS Code stdout/stderr (extension host log)

## Known platform behavior

- **Windows Defender real-time scan.** The bundled `python.exe` is
  unsigned, so Defender scans it on first spawn — expect several
  seconds of first-connect latency (not a hang). The driver allows up
  to 240 s for the sidecar process to appear and 120 s for the
  statusline to read `connected`. If a run times out here anyway, check
  `vscode.log` for `spawn`/`ENOENT` errors before blaming Defender.
- The driver keeps its own Python (from `actions/setup-python`) — only
  the VS Code child process gets the scrubbed environment.

## Manual re-run

Actions → `windows-gui-test` → **Run workflow** → branch
`feature/bundled-python-050`. Watch with
`gh run watch` / `gh run view --log-failed`.
