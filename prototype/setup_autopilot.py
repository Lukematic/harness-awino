"""Setup autopilot: the deterministic project chores, proposed once and
applied only with the user's consent.

The user should spend their attention on the problem, not on wiring a
task runner or remembering to ignore .venv. These chores are mechanical,
so the harness detects the project and proposes them. It never writes
outside .awino/ without a yes, never overwrites an existing file (the
.gitignore is appended to, nothing else is touched), and remembers every
answer in .awino/setup.json — "never" means never ask again.

Actions (each only when it applies):
  justfile       test / lint / format recipes for the detected language,
                 only when there is no justfile or Makefile. VERIFY runs
                 the test and lint recipes itself.
  gitignore      the missing lines among .venv/, node_modules/, .env, ...
  env_example    .env.example with the keys (never the values) of .env
  editorconfig   a minimal .editorconfig
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

DECISIONS = "setup.json"


def detect(root: str | Path) -> dict:
    root = Path(root)
    langs = []
    if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file() \
            or (root / "requirements.txt").is_file() \
            or any(root.glob("*.py")):
        langs.append("python")
    if (root / "package.json").is_file():
        langs.append("node")
    if (root / "go.mod").is_file():
        langs.append("go")
    if (root / "Cargo.toml").is_file():
        langs.append("rust")
    return {"languages": langs}


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _python_recipes(root: Path) -> dict:
    deps = " ".join(_read(root / f) for f in
                    ("pyproject.toml", "requirements.txt",
                     "requirements-dev.txt", "setup.cfg")).lower()
    uses_pytest = ("pytest" in deps or (root / "pytest.ini").is_file()
                   or (root / "conftest.py").is_file())
    test = ("python -m pytest -q" if uses_pytest
            else "python -m unittest discover -q")
    return {"test": test, "lint": "ruff check .", "format": "ruff format ."}


def _node_recipes(root: Path) -> dict:
    try:
        pkg = json.loads(_read(root / "package.json") or "{}")
    except json.JSONDecodeError:
        pkg = {}
    scripts = pkg.get("scripts") or {}
    deps = {**(pkg.get("dependencies") or {}),
            **(pkg.get("devDependencies") or {})}
    r = {}
    if "test" in scripts:
        r["test"] = "npm test"
    if "lint" in scripts:
        r["lint"] = "npm run lint"
    elif "eslint" in deps:
        r["lint"] = "npx eslint ."
    if "format" in scripts:
        r["format"] = "npm run format"
    elif "prettier" in deps:
        r["format"] = "npx prettier --write ."
    return r


_RECIPES = {
    "python": _python_recipes,
    "node": _node_recipes,
    "go": lambda root: {"test": "go test ./...", "lint": "go vet ./...",
                        "format": "gofmt -w ."},
    "rust": lambda root: {"test": "cargo test", "lint": "cargo clippy",
                          "format": "cargo fmt"},
}

_IGNORES = {
    "python": [".venv/", "__pycache__/", ".pytest_cache/", ".ruff_cache/"],
    "node": ["node_modules/"],
    "rust": ["target/"],
    "go": [],
}


def justfile_text(root: str | Path) -> str | None:
    """Recipes for the detected language(s); None when nothing detected."""
    root = Path(root)
    merged: dict[str, list[str]] = {}
    for lang in detect(root)["languages"]:
        for name, cmd in _RECIPES[lang](root).items():
            merged.setdefault(name, []).append(cmd)
    if not merged:
        return None
    out = ["# Project recipes (proposed by A.W.I.N.O. setup; edit freely).",
           "# The harness runs `test` and `lint` itself during VERIFY.", ""]
    for name in ("test", "lint", "format"):
        if name in merged:
            out.append(f"{name}:")
            out += [f"    {c}" for c in merged[name]]
            out.append("")
    return "\n".join(out)


def _gitignore_missing(root: Path) -> list[str]:
    have = {ln.strip() for ln in _read(root / ".gitignore").splitlines()}
    want = [".env", ".awino/projects/"]
    for lang in detect(root)["languages"]:
        want += _IGNORES[lang]
    norm = {h.rstrip("/") for h in have}
    return [w for w in dict.fromkeys(want) if w.rstrip("/") not in norm]


def _env_keys(root: Path) -> list[str]:
    keys = []
    for ln in _read(root / ".env").splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m:
            keys.append(m.group(1))
    return keys


EDITORCONFIG = """root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 4

[*.{js,ts,json,yml,yaml}]
indent_size = 2

[{Makefile,*.mk}]
indent_style = tab
"""


def _decisions(root: Path) -> dict:
    try:
        d = json.loads((root / ".awino" / DECISIONS).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_decisions(root: Path, d: dict) -> None:
    p = root / ".awino" / DECISIONS
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, sort_keys=True))


def plan(root: str | Path) -> list[dict]:
    """Proposed actions not yet applied and not declined for good:
    [{id, path, kind, why, content}]."""
    root = Path(root)
    decided = _decisions(root)
    acts = []
    if not any((root / f).is_file() for f in
               ("justfile", "Justfile", ".justfile", "Makefile", "makefile")):
        text = justfile_text(root)
        if text:
            acts.append({"id": "justfile", "path": "justfile",
                         "kind": "create", "content": text,
                         "why": ("test/lint/format recipes for "
                                 + ", ".join(detect(root)["languages"])
                                 + "; VERIFY runs test and lint itself")})
    missing = _gitignore_missing(root)
    if missing:
        acts.append({"id": "gitignore", "path": ".gitignore",
                     "kind": "append", "content": "\n".join(missing) + "\n",
                     "why": "keep " + ", ".join(missing) + " out of git"})
    if (root / ".env").is_file() and not (root / ".env.example").is_file():
        keys = _env_keys(root)
        if keys:
            acts.append({"id": "env_example", "path": ".env.example",
                         "kind": "create",
                         "content": "".join(f"{k}=\n" for k in keys),
                         "why": (f"document the {len(keys)} setting(s) in "
                                 f".env without their values")})
    if not (root / ".editorconfig").is_file() and detect(root)["languages"]:
        acts.append({"id": "editorconfig", "path": ".editorconfig",
                     "kind": "create", "content": EDITORCONFIG,
                     "why": "consistent indentation and line endings"})
    return [a for a in acts
            if decided.get(a["id"], {}).get("answer") not in ("applied",
                                                                "never")]


def apply(root: str | Path, ids: list[str]) -> dict:
    """Apply the chosen actions. Never overwrites; appends to .gitignore."""
    root = Path(root)
    want = set(ids or [])
    done, skipped = [], []
    decided = _decisions(root)
    for a in plan(root):
        if a["id"] not in want:
            continue
        p = root / a["path"]
        try:
            if a["kind"] == "create":
                if p.exists():
                    skipped.append(f"{a['path']} (already exists)")
                    continue
                p.write_text(a["content"], encoding="utf-8")
            else:
                prev = _read(p)
                sep = "" if not prev or prev.endswith("\n") else "\n"
                with open(p, "a", encoding="utf-8") as f:
                    f.write(sep + a["content"])
        except OSError as e:
            skipped.append(f"{a['path']} ({e})")
            continue
        decided[a["id"]] = {"answer": "applied", "ts": time.time()}
        done.append(a["path"])
    _save_decisions(root, decided)
    return {"applied": done, "skipped": skipped}


def decline(root: str | Path, ids: list[str], never: bool = False) -> None:
    """Skip for now (asked again next session) or never (not asked again)."""
    root = Path(root)
    decided = _decisions(root)
    for i in ids or []:
        decided[i] = {"answer": "never" if never else "skipped",
                      "ts": time.time()}
    _save_decisions(root, decided)
