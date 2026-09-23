# src

TypeScript sources for the A.W.I.N.O. VS Code extension host. Compiled with
`tsc -p ./` into `out/` (strict mode, must be clean).

- `sidecar.ts` — vscode-free JSON-lines client for `prototype/awino_sidecar.py`.
  Kept free of the `vscode` API on purpose so `test/harness.js` can import the
  compiled module in plain node and exercise the spawn path end to end.
- `views.ts` — TreeDataProviders for the Contract, Journal, Learnings, Skills,
  Context, and Modes activity-bar views. Each pulls from the sidecar via the
  injected `query` (the `command` verb); refreshed on every turn_result.
- `extension.ts` — activation: spawns the sidecar, routes §4 events to the
  chat webview / views / status bar, registers all commands (missions, seeds,
  skills, context, modes, persona, environments, doctor, housekeeping), the
  Models & Providers panel, and the approval/compaction modals.
