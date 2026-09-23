"""Plug-and-play project bootstrap (Track A).

On project open / mission start, the harness runs a startup checklist in the
*doctor pattern* — every item reports ok/warn/fail — and fixes what's missing
from the get-go, with no user thought required:

  venv:        .venv exists -> use it; else create via `uv venv` (if uv is
               present) or `python3 -m venv .venv`.
  task runner: justfile/Makefile exists -> use it; neither -> scaffold a
               justfile (test/lint/format recipes). A missing `just` binary
               gets a best-effort install; otherwise a breadcrumb is recorded
               and the bootstrap continues (never fails).
  detect+report: python version, uv, ruff, git repo, .awino/ presence.
  seeds:       .awino/seeds/*.md checklists (`- [ ] task`) are parsed and
               returned as the starting task list for the task tracker.
  project.yaml: .awino/project.yaml source of truth — created if missing,
               NEVER clobbered.
  scaffold:    spec/, docs/research/, docs/archive/, lessons.md (append-only),
               README skeleton — only what's missing.

run_startup_checklist() never raises: unexpected errors become a "fail"
check item, and the mission start continues.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

CHECK_OK = "ok"
CHECK_WARN = "warn"
CHECK_FAIL = "fail"

PRINCIPLES = ["spec-before-code", "contract-first", "evidence-for-every-change"]

JUSTFILE_TEMPLATE = """# A.W.I.N.O. project task recipes (scaffolded by project bootstrap).
# Install just: https://just.systems — or keep using make.

set dotenv-load := false

# run the test suite
test:
    python3 -m unittest discover -s tests -t . 2>/dev/null || pytest -q

# lint everything
lint:
    ruff check . 2>/dev/null || echo "ruff not installed"

# format everything
format:
    ruff format . 2>/dev/null || echo "ruff not installed"
"""

README_TEMPLATE = """# {name}

Scaffolded by the A.W.I.N.O. project bootstrap.

## Layout

- `spec/` — specifications (spec-before-code: no BUILD turn without one)
- `docs/research/` — living research documents, refined across turns
- `docs/archive/` — superseded docs, dated; the journal records what changed
- `lessons.md` — append-only lessons from the reflection layer
- `.awino/project.yaml` — project source of truth (mission, env, docs, principles)
- `.awino/seeds/` — reusable mission templates
- `.awino/registry/` — milestones, breadcrumbs, task tracker (auto-created)

## Quick start

```sh
just test     # or: make test
just lint
```
"""

CHECKLIST_RE = re.compile(r"^\s*-\s+\[([ xX])\]\s+(.+?)\s*$")


def _check(name: str, status: str, detail: str, fixed: bool = False) -> dict:
    return {"name": name, "status": status, "detail": detail, "fixed": fixed}


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """Run a command; return (exit_code, combined_output_tail). Never raises."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout + proc.stderr)[-1500:]
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as e:
        return 127, str(e)


# ---------------------------------------------------------------------------
# individual checks (each returns a check dict; fixers return (check, extra))
# ---------------------------------------------------------------------------

def check_python(root: Path) -> dict:
    v = sys.version_info
    detail = f"python {v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if v < (3, 9):
        return _check("python", CHECK_WARN, detail + " — 3.9+ recommended")
    return _check("python", CHECK_OK, detail)


def check_uv(root: Path) -> dict:
    uv = shutil.which("uv")
    if uv:
        return _check("uv", CHECK_OK, f"found: {uv}")
    return _check("uv", CHECK_WARN, "not on PATH — venv creation falls back "
                 "to `python3 -m venv`")


def ensure_venv(root: Path, timeout: int = 120) -> tuple[dict, Path | None]:
    """Use .venv if present, else create it. Returns (check, venv_bin|None).

    A half-created .venv (directory without pyvenv.cfg) is moved aside to
    .venv.broken-<ts> and recreated — never deleted outright, never built
    on top of. If nothing works, this degrades to a warning and the
    mission continues with the system python.
    """
    venv = root / ".venv"
    cfg = venv / "pyvenv.cfg"
    if cfg.is_file():
        return (_check("venv", CHECK_OK, f"using existing {venv}"),
                venv / "bin")
    if venv.exists() and not cfg.is_file():
        # half-created venv: move aside, don't delete, don't build on top
        import time as _t
        sidecar = root / f".venv.broken-{int(_t.time())}"
        try:
            venv.rename(sidecar)
        except OSError as e:
            return (_check("venv", CHECK_WARN,
                           f"half-created .venv is not a venv and cannot be "
                           f"moved aside ({e}) — continuing with system "
                           f"python; remove {venv} manually to retry"), None)
    uv = shutil.which("uv")
    tried = []
    if uv:
        code, out = _run([uv, "venv", ".venv"], root, timeout)
        how = "uv venv"
        tried.append((how, code, out))
        if code == 0 and cfg.is_file():
            return (_check("venv", CHECK_OK, f"created via {how}",
                           fixed=True), venv / "bin")
    # python3 -m venv: ensurepip may try to reach the network (sandbox or
    # offline hosts); fall back to --without-pip rather than failing.
    code, out = _run([sys.executable, "-m", "venv", ".venv"], root, timeout)
    how = "python3 -m venv"
    tried.append((how, code, out))
    if code == 0 and cfg.is_file():
        return (_check("venv", CHECK_OK, f"created via {how}",
                       fixed=True), venv / "bin")
    code, out = _run([sys.executable, "-m", "venv", "--without-pip",
                      ".venv"], root, timeout)
    how = "python3 -m venv --without-pip"
    if code == 0 and cfg.is_file():
        return (_check("venv", CHECK_WARN,
                       f"created via {how} (ensurepip failed — no network "
                       f"or pip missing; pip can be added later)",
                       fixed=True), venv / "bin")
    detail = "; ".join(f"{h} -> exit {c}: {o[-200:]}" for h, c, o in tried)
    return (_check("venv", CHECK_FAIL,
                   f"venv creation failed: {detail} — continuing with "
                   f"system python"), None)


def _best_effort_install_just(timeout: int = 90) -> tuple[bool, str]:
    """Try to install the `just` binary. Returns (installed, detail)."""
    if shutil.which("cargo"):
        code, out = _run(["cargo", "install", "just", "--locked"],
                         Path.cwd(), timeout)
        if code == 0 and shutil.which("just"):
            return True, "installed via cargo"
        return False, f"cargo install failed (exit {code})"
    return False, "no cargo on PATH — install from https://just.systems"


def ensure_task_runner(root: Path) -> tuple[dict, str | None]:
    """justfile/Makefile detection, justfile scaffold, just breadcrumb."""
    justfile = root / "justfile"
    makefile = root / "Makefile"
    breadcrumb = None
    if justfile.is_file():
        detail = "using existing justfile"
    elif makefile.is_file():
        detail = "using existing Makefile"
    else:
        try:
            justfile.write_text(JUSTFILE_TEMPLATE)
            detail = "scaffolded justfile (test/lint/format recipes)"
        except OSError as e:
            return (_check("task_runner", CHECK_WARN,
                           f"cannot scaffold justfile ({e}) — continuing "
                           f"without a task runner"), None)
    if not shutil.which("just"):
        installed, how = _best_effort_install_just()
        if installed:
            detail += "; just installed via cargo"
        else:
            breadcrumb = ("just binary not installed — recipes work with "
                          "`make`-style reading, or install just: "
                          "https://just.systems "
                          f"({how})")
    status = CHECK_OK if breadcrumb is None else CHECK_WARN
    return _check("task_runner", status, detail), breadcrumb


def check_ruff(root: Path, venv_bin: Path | None,
               timeout: int = 90) -> dict:
    """Detect ruff; best-effort install into the venv. Never fails."""
    candidates = []
    if venv_bin:
        candidates.append(venv_bin / "ruff")
    which = shutil.which("ruff")
    if which:
        candidates.append(Path(which))
    for c in candidates:
        if c.is_file():
            return _check("ruff", CHECK_OK, f"found: {c}")
    # best-effort install into the venv
    if venv_bin:
        pip = venv_bin / "pip"
        uv = shutil.which("uv")
        if uv:
            code, _ = _run([uv, "pip", "install", "--python",
                            str(venv_bin / "python"), "ruff"],
                           root, timeout)
        elif pip.is_file():
            code, _ = _run([str(pip), "install", "ruff"], root, timeout)
        else:
            code = 127
        if code == 0 and (venv_bin / "ruff").is_file():
            return _check("ruff", CHECK_OK, "installed into .venv",
                          fixed=True)
    return _check("ruff", CHECK_WARN,
                  "not found — install with `pip install ruff` or "
                  "`uv pip install ruff`")


def check_git(root: Path) -> dict:
    if (root / ".git").exists():
        code, out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                         root, 10)
        branch = out.strip().splitlines()[-1] if code == 0 and out else "?"
        return _check("git", CHECK_OK, f"repo present (branch: {branch})")
    return _check("git", CHECK_WARN, "not a git repo")


def ensure_awino_dir(root: Path) -> tuple[dict, Path]:
    d = root / ".awino"
    if d.is_dir():
        return _check("awino_dir", CHECK_OK, "using existing .awino/"), d
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # read-only project: clear error, never a crash; the caller decides
        return _check("awino_dir", CHECK_FAIL,
                      f"cannot create .awino/ ({e}) — is the directory "
                      f"writable?"), d
    return _check("awino_dir", CHECK_OK, "created .awino/", fixed=True), d


# --- Track E: role environment profiles --------------------------------------
# A project declares its role lens default in .awino/project.yaml:
#   profile: software-engineer   (default when the key is absent)
# Bootstrap scaffolds only what's missing for that profile — existing
# repos/files are never clobbered.

PROFILE_IDS = (
    "software-engineer",
    "ai-researcher",
    "ai-architect",
    "forward-deployed-engineer",
    "cybersecurity-engineer",
)
DEFAULT_PROFILE = "software-engineer"

TOOLCHAINS: dict[str, list[str]] = {
    "software-engineer": ["python", "ruff", "pytest", "just", "git"],
    "ai-researcher": ["python (pinned requirements.txt)", "run logs"],
    "ai-architect": ["docs/adr/", "decision matrices"],
    "forward-deployed-engineer": ["runbooks/", "deployment scripts"],
    "cybersecurity-engineer": ["threat-model template", "secret scanners"],
}

_ADR_TEMPLATE = """# ADR 0000: <title>

Date: <yyyy-mm-dd>
Status: proposed

## Context
<what forces this decision, and the constraints ranked>

## Options
<option A> / <option B> / build vs buy

## Decision
<what we chose>

## Consequences
<what becomes easier / harder because of this>
"""

_RUNBOOK_TEMPLATE = """# Runbook: <service/slice>

## Deploy
1. <step>

## Operate
<how to run it day to day>

## Roll back
1. <step>
"""

_THREAT_MODEL_TEMPLATE = """# Threat model: <scope>

## Assets
- <asset> (trust boundary: <boundary>)

## STRIDE-lite
| Threat | Type | Impact | Mitigation | Verified by |
|---|---|---|---|---|
| <e.g. token logged> | Info disclosure | high | <redact at source> | <adversarial test> |

## Residual risks
- <tracked as task>
"""

_RUNLOG_TEMPLATE = """# Run log

Record every experiment: date, command, seed, result.
"""

_REQUIREMENTS_TEMPLATE = """# Pinned dependencies — exact versions for reproducibility.
# Written by awino bootstrap; pin real versions before first use.
"""


def ensure_profile_scaffold(root: Path, profile: str) -> dict:
    """Create the missing scaffold for a role profile. Never clobbers.

    Returns a checklist dict {profile, created: [...], kept: [...]}.
    Unknown profiles degrade to a breadcrumb, never an error.
    """
    root = Path(root)
    if profile not in PROFILE_IDS:
        return {"profile": profile, "created": [], "kept": [],
                "check": _check("profile_scaffold", CHECK_WARN,
                                f"unknown profile {profile!r} — no scaffold "
                                f"applied (valid: {', '.join(PROFILE_IDS)})")}
    created: list[str] = []
    kept: list[str] = []

    def _dir(rel: str) -> None:
        p = root / rel
        if p.is_dir():
            kept.append(rel + "/")
        else:
            try:
                p.mkdir(parents=True, exist_ok=True)
                created.append(rel + "/")
            except OSError:
                kept.append(rel + "/ (unwritable — skipped)")

    def _file(rel: str, template: str) -> None:
        p = root / rel
        if p.is_file():
            kept.append(rel)
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(template)
            created.append(rel)
        except OSError:
            kept.append(rel + " (unwritable — skipped)")

    if profile == "ai-researcher":
        _dir("experiments")
        _file("experiments/RUNLOG.md", _RUNLOG_TEMPLATE)
        _file("requirements.txt", _REQUIREMENTS_TEMPLATE)
    elif profile == "ai-architect":
        _dir("docs/adr")
        _file("docs/adr/0000-template.md", _ADR_TEMPLATE)
    elif profile == "forward-deployed-engineer":
        _dir("runbooks")
        _dir("stakeholder-notes")
        _file("runbooks/TEMPLATE.md", _RUNBOOK_TEMPLATE)
    elif profile == "cybersecurity-engineer":
        _dir("docs/security")
        _file("docs/security/THREAT-MODEL.md", _THREAT_MODEL_TEMPLATE)
    # software-engineer: the standard scaffold (spec/, docs/, README) is
    # already handled by scaffold_project_dirs.

    detail = (f"profile {profile}: created {len(created)}, "
              f"kept {len(kept)}")
    return {"profile": profile, "created": created, "kept": kept,
            "check": _check("profile_scaffold",
                            CHECK_OK if profile in PROFILE_IDS else CHECK_WARN,
                            detail, fixed=bool(created))}


# --- project.yaml: hand-written YAML subset (stdlib only) -------------------

def write_project_yaml(awino_dir: Path, project_name: str,
                       mission_text: str = "",
                       criteria: list[str] | None = None) -> Path:
    """Write .awino/project.yaml. Caller guarantees it does not exist."""
    crit = criteria or []
    lines = ["# A.W.I.N.O. project source of truth — written by project "
             "bootstrap.",
             "# Edit mission/done_criteria/profile freely; env/docs/principles are "
             "managed by the harness.",
             f"project: {project_name}",
             f"profile: {DEFAULT_PROFILE}"]
    if mission_text:
        lines.append(f"mission: {mission_text}")
    lines.append("done_criteria:")
    for c in crit:
        lines.append(f"  - {c}")
    if not crit:
        lines.append("  - manual")
    lines.append("toolchains:")
    for role in PROFILE_IDS:
        lines.append(f"  {role}:")
        for tool in TOOLCHAINS[role]:
            lines.append(f"    - {tool}")
    v = sys.version_info
    lines += ["env:",
              f"  python: {v.major}.{v.minor}.{v.micro}",
              "  venv: .venv",
              "  linters:",
              "    - ruff",
              "docs:",
              "  spec: spec/",
              "  research: docs/research/",
              "  archive: docs/archive/",
              "principles:"]
    for p in PRINCIPLES:
        lines.append(f"  - {p}")
    p = awino_dir / "project.yaml"
    p.write_text("\n".join(lines) + "\n")
    return p


def read_project_yaml(path: Path) -> dict:
    """Parse the bootstrap-written subset of YAML. Never raises."""
    out: dict = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    section = None
    subsection = None  # (section, key) for nested lists
    for raw in lines:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", stripped)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            section, subsection = key, None
            if val:
                out[key] = val
            else:
                out[key] = {}
        elif indent == 2 and stripped.startswith("- "):
            item = stripped[2:].strip()
            if subsection:
                sec, key = subsection
                out[sec].setdefault(key, []).append(item)
            elif section:
                if not isinstance(out.get(section), list):
                    out[section] = []
                out[section].append(item)
        elif indent >= 2 and stripped.startswith("- ") and subsection:
            # nested list under a subsection (e.g. env: -> linters: -> - ruff)
            sec, key = subsection
            out[sec].setdefault(key, []).append(stripped[2:].strip())
        elif indent == 2:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", stripped)
            if m and isinstance(out.get(section), dict):
                key, val = m.group(1), m.group(2)
                subsection = None  # a new scalar key closes any open sublist
                if val:
                    out[section][key] = val
                else:
                    out[section][key] = []
                    subsection = (section, key)
    return out


def ensure_project_yaml(awino_dir: Path, project_name: str,
                        mission_text: str = "",
                        criteria: list[str] | None = None
                        ) -> tuple[dict, Path]:
    p = awino_dir / "project.yaml"
    if p.is_file():
        return (_check("project_yaml", CHECK_OK,
                        "using existing .awino/project.yaml (never "
                        "clobbered)"), p)
    write_project_yaml(awino_dir, project_name, mission_text, criteria)
    return (_check("project_yaml", CHECK_OK,
                   "created .awino/project.yaml", fixed=True), p)


def scaffold_project_dirs(root: Path) -> dict:
    """Create spec/, docs/research/, docs/archive/, lessons.md, README —
    only what is missing. Never clobbers. Unwritable paths become a
    warning, never a crash."""
    made = []
    skipped = []
    for d in ("spec", "docs/research", "docs/archive"):
        p = root / d
        if not p.is_dir():
            try:
                p.mkdir(parents=True, exist_ok=True)
                made.append(d + "/")
            except OSError:
                skipped.append(d + "/")
    lessons = root / "lessons.md"
    if not lessons.is_file():
        try:
            lessons.write_text("# Lessons\n\nAppend-only. The reflection layer "
                               "adds entries after each phase and mission.\n")
            made.append("lessons.md")
        except OSError:
            skipped.append("lessons.md")
    readme = root / "README.md"
    if not readme.is_file():
        try:
            readme.write_text(README_TEMPLATE.format(name=root.name))
            made.append("README.md")
        except OSError:
            skipped.append("README.md")
    if skipped:
        return _check("scaffold", CHECK_WARN,
                      "could not create (read-only?): " + ", ".join(skipped)
                      + ("; created: " + ", ".join(made) if made else ""))
    if made:
        return _check("scaffold", CHECK_OK,
                      "created: " + ", ".join(made), fixed=True)
    return _check("scaffold", CHECK_OK, "all project dirs/files present")


def parse_seed_checklist(seed_body: str) -> list[dict]:
    """Extract `- [ ]` / `- [x]` checklist tasks from a seed file body."""
    tasks = []
    for line in seed_body.splitlines():
        m = CHECKLIST_RE.match(line)
        if m:
            tasks.append({"text": m.group(2),
                          "done": m.group(1).lower() == "x"})
    return tasks


def collect_seed_tasks(seeds_dir: Path) -> tuple[dict, list[dict]]:
    """Parse .awino/seeds/*.md checklists into task dicts."""
    tasks: list[dict] = []
    files = 0
    if seeds_dir.is_dir():
        for p in sorted(seeds_dir.glob("*.md")):
            files += 1
            try:
                body = p.read_text().split("---")
                text = body[-1] if len(body) >= 3 else p.read_text()
            except OSError:
                continue
            for t in parse_seed_checklist(text):
                t["source"] = f"seed:{p.stem}"
                tasks.append(t)
    detail = (f"{len(tasks)} checklist tasks from {files} seed file(s)"
              if files else "no seed files — registry starts empty")
    return _check("seeds", CHECK_OK, detail), tasks


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def run_startup_checklist(project_root: str | Path,
                          mission_text: str = "",
                          criteria: list[str] | None = None) -> dict:
    """Run the full startup checklist; fix what's missing. Never raises.

    Returns {"checks": [...], "venv_bin": str|None,
             "seed_tasks": [...], "project_yaml": str|None,
             "breadcrumbs": [...], "ok": bool}.
    """
    root = Path(project_root)
    checks: list[dict] = []
    breadcrumbs: list[str] = []
    venv_bin: Path | None = None
    seed_tasks: list[dict] = []
    project_yaml: Path | None = None
    try:
        checks.append(check_python(root))
        checks.append(check_uv(root))

        venv_check, venv_bin = ensure_venv(root)
        checks.append(venv_check)

        runner_check, crumb = ensure_task_runner(root)
        checks.append(runner_check)
        if crumb:
            breadcrumbs.append(crumb)

        checks.append(check_ruff(root, venv_bin))
        checks.append(check_git(root))

        awino_check, awino_dir = ensure_awino_dir(root)
        checks.append(awino_check)

        yaml_check, project_yaml = ensure_project_yaml(
            awino_dir, root.name, mission_text, criteria)
        checks.append(yaml_check)

        checks.append(scaffold_project_dirs(root))

        # Track E: apply only the missing scaffold for the configured
        # profile. Existing project.yaml wins; default is software-engineer.
        prof = DEFAULT_PROFILE
        try:
            if project_yaml is not None:
                prof = read_project_yaml(project_yaml).get("profile") \
                    or DEFAULT_PROFILE
        except Exception:
            prof = DEFAULT_PROFILE
        prof_report = ensure_profile_scaffold(root, prof)
        checks.append(prof_report["check"])

        seeds_check, seed_tasks = collect_seed_tasks(awino_dir / "seeds")
        checks.append(seeds_check)
    except Exception as e:  # noqa: BLE001 — bootstrap never breaks the mission
        checks.append(_check("bootstrap", CHECK_FAIL,
                             f"unexpected error (continuing): "
                             f"{type(e).__name__}: {e}"))
    ok = all(c["status"] != CHECK_FAIL for c in checks)
    return {"checks": checks,
            "venv_bin": str(venv_bin) if venv_bin else None,
            "seed_tasks": seed_tasks,
            "project_yaml": str(project_yaml) if project_yaml else None,
            "breadcrumbs": breadcrumbs,
            "ok": ok,
            "ts": time.time()}


# ---------------------------------------------------------------------------
# Track A/H: the one full init flow + session-start auto-init
# ---------------------------------------------------------------------------

def full_init_flow(target: str | Path) -> dict:
    """The complete one-command init flow.

    `run_startup_checklist` + registry ensure + seed-task import — exactly
    what `awino init` runs, and what a chat session runs automatically on
    start when the directory is not a project yet. Idempotent, never
    clobbers, never raises. Returns the checklist report with
    `seeds_imported` added.
    """
    from registry import Registry  # local: keep bootstrap import-light
    root = Path(target)
    report = run_startup_checklist(root)
    seeds_imported = 0
    try:
        reg = Registry(root / ".awino")
        reg.ensure()
        seeds_imported = reg.import_seed_tasks(report.get("seed_tasks", []))
    except Exception:  # noqa: BLE001 — init degrades, never crashes
        seeds_imported = 0
    report["seeds_imported"] = seeds_imported
    return report


# check name -> plain words for "what it set up" (only listed when fixed)
_INIT_WORDS = {
    "venv": "a Python virtual environment (.venv)",
    "task_runner": "a task-runner file (justfile) with test/lint/format recipes",
    "awino_dir": "the project state folder (.awino/)",
    "project_yaml": "the project file (.awino/project.yaml)",
    "scaffold": "the project folders (spec/, docs/, lessons.md, README.md)",
    "profile_scaffold": "the role workspace folders for this project's profile",
}


def _init_summary(report: dict) -> list[str]:
    """One brief plain-language summary of what init set up.

    Only lists what was actually created; never claims success when a
    check failed — then it names the failure and the one next action.
    """
    made = [_INIT_WORDS[c["name"]]
            for c in report.get("checks", [])
            if c.get("fixed") and c["name"] in _INIT_WORDS]
    seeds = report.get("seeds_imported", 0)
    reg_line = "the task registry (.awino/registry/)"
    if seeds:
        reg_line += f" ({seeds} seed task{'s' if seeds != 1 else ''} imported)"
    made.append(reg_line)
    if report.get("ok"):
        lines = [f"Set up this project for you: created {', '.join(made)}. "
                 f"Nothing that already existed was changed."]
    else:
        fails = [c for c in report.get("checks", [])
                 if c["status"] == CHECK_FAIL]
        detail = fails[0]["detail"] if fails else "an unknown check failed"
        lines = [f"Tried to set up this project (created {', '.join(made)}), "
                 f"but one check needs attention: {detail}.",
                 "Next action: fix that, then keep chatting — the harness "
                 "fills in the rest automatically."]
    for b in report.get("breadcrumbs", [])[:2]:
        lines.append(f"Note: {b}")
    return lines


def session_start_auto_init(project_dir: str | Path) -> dict | None:
    """Auto-init on chat session start (Track A/H).

    The user never has to type `awino init` by hand: when a session starts
    in a directory, the harness checks for `.awino/project.yaml`. Present
    -> return None (already a project; nothing to do, nothing to report).
    Absent -> run the full init flow automatically and return
    {"ok": bool, "summary": [brief plain-language lines], "report": report}.

    Never raises — failures degrade to a summary carrying one next action,
    so session start always proceeds.
    """
    try:
        root = Path(project_dir).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False,
                    "summary": [f"Couldn't set up this project: {e}.",
                                "Next action: pick a writable directory and "
                                "start the chat again — nothing was changed."],
                    "report": None}
        proj_yaml = root / ".awino" / "project.yaml"
        try:
            if proj_yaml.is_file() and proj_yaml.stat().st_size > 0:
                return None  # already a project: silent, nothing to report
            if proj_yaml.is_file():
                # 0-byte stub left by a failed init (e.g. full disk): the
                # harness wrote it, it holds no user data, so remove it and
                # let init run cleanly instead of treating the project as
                # healthy.
                proj_yaml.unlink()
        except OSError:
            return None  # cannot inspect/repair: don't loop, don't crash
        report = full_init_flow(root)
        return {"ok": report["ok"],
                "summary": _init_summary(report),
                "report": report}
    except Exception as e:  # noqa: BLE001 — absolute last resort
        return {"ok": False,
                "summary": [f"Couldn't finish setting up this project "
                            f"({type(e).__name__}: {e}).",
                            "Next action: run `awino init` here and follow "
                            "its guidance — nothing was left half-written."],
                "report": None}
