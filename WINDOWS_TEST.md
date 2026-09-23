# Windows test — the one command that proves it

The harness is developed on Linux. This is the check **you** run on your
Windows machine, because mocks on Linux can only prove the code *selects*
the right paths — not that Windows actually behaves.

## The one command

Open PowerShell (or cmd) in the repo root and run:

```powershell
python -m pytest prototype/tests -q
```

Don't have pytest? Use the unittest runner instead (no install needed):

```powershell
python -m unittest discover -s prototype/tests -t prototype
```

## What success looks like

- The last line reads `OK` (unittest) or `N passed` (pytest), with **zero
  failures and zero errors**.
- `prototype/tests/test_windows_compat.py` (12 tests) passes — these are the
  Windows-specific ones: venv resolution picks `Scripts\` + `python.exe`,
  the scaffolded justfile has no Unix-only shell syntax, and `run_command`
  prepends the venv `Scripts` dir to `PATH`.

## 30-second smoke test

In a fresh empty folder, run the harness CLI (from the repo's `prototype/`
directory):

```powershell
cd C:\path\to\a\fresh\test\folder
python C:\path\to\harness-awino\prototype\cli.py init
```

You should see a plain-language "Set up this project" summary and a `.venv`
folder containing `Scripts\python.exe` (not `bin\`). Then:

```powershell
python C:\path\to\harness-awino\prototype\cli.py status
```

`environment:` should report `virtual env: ready (.venv)`. `awino chat`
starts the full loop-owner session and runs the same auto-init on start —
`init` is only ever the manual override.

## Known remaining risks (honest)

- **No real-Windows run has happened yet.** The Linux suite fakes Windows
  with mocks; subtle differences (path case-insensitivity, file locking,
  antivirus holding `.venv` files, `MAX_PATH`) can only be found on your
  machine. If anything fails, paste the traceback — that's the next fix.
- **`just` on Windows is best-effort.** The bootstrap tries `cargo install
  just`, then falls back to a breadcrumb and continues. The scaffolded
  justfile recipes are plain single commands (`python -m pytest -q`,
  `ruff check .`) so they work under PowerShell, cmd, and sh — but without
  the `just` binary you run them by hand or via `awino`'s task runner.
- **PowerShell vs cmd:** `run_command` uses `shell=True`, which is cmd.exe
  on Windows. Keep commands portable: `python -m ...` invocations, no
  `rm`/`ls`/`touch`, no `2>/dev/null`, no `&&`/`||` chains (PowerShell 5.1
  doesn't support them). The harness's own scaffolded recipes follow this;
  your own commands should too.
- **Python on PATH:** Windows installers don't always add Python to PATH.
  The bootstrap degrades to a warning and continues with whatever python
  started the harness — check `awino status` ("environment:") if the venv
  step reports a warning.
