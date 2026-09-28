# workflows

GitHub Actions for this repo.

| Workflow | Runs on | What it does |
|---|---|---|
| `ci.yml` | Every push and pull request; manual | Engine: `ruff check --exclude tests .` (zero findings), `pip install` of `prototype/` plus an import check, and the full unittest suite. Extension: `npm ci`, compile, `npm test`. |
| `release-vsix.yml` | Tags `vsix-v*` | Checks the tag matches `package.json`, bundles the sidecar and Python runtimes, runs the tests, packages the `.vsix`, checks its contents, and publishes a GitHub pre-release. See [RELEASING.md](../../integrations/vscode/extension/RELEASING.md). |
| `windows-gui-test.yml` | Pushes to `main`, `vsix-v*` tags, pull requests; manual | Builds the `.vsix` on Windows, installs it in real VS Code with no system Python, and proves the sidecar starts from the bundled runtime. Uploads screenshots. |
| `live-mission.yml` | Manual | Runs `proof/live_mission/run_live.py` against a real model. Needs an `AWINO_API_KEY` or `ANTHROPIC_API_KEY` repo secret. |

`scripts/` holds the Windows helpers: `win_gui_proof.py` (the GUI proof),
`win_sidecar_smoke.py` (starts the sidecar and waits for `ready`) and
`win_sidecar_wrap.py` (dumps stack traces if the sidecar hangs).

The same engine and extension checks run locally with `just test` and
`just lint` from the repo root.
