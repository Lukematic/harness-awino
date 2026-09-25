"""Sandboxed demo tools. All file effects are confined to a sandbox dir;
path traversal is rejected. write_file and patch_file are consequential
(need approval). patch_file applies a unified diff atomically and
fail-closed (named refusals for bad hunks, mismatches, ambiguity, fuzz).
run_command executes a shell command with cwd confined to the sandbox,
a hard timeout, and truncated output (Phase 0: mock/test use only).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

TOOL_DEFS = {
    "read_file": {"consequential": False, "args": ["path"]},
    "write_file": {"consequential": True, "args": ["path", "content"]},
    "patch_file": {"consequential": True, "args": ["path", "diff"]},
    "list_dir": {"consequential": False, "args": []},
    "run_command": {"consequential": False, "args": ["cmd"]},
    # v0.6 project-context tools: read-only by construction, never write.
    "search_files": {"consequential": False, "args": ["pattern"]},
    "find_symbol": {"consequential": False, "args": ["name"]},
    "git_status": {"consequential": False, "args": []},
    "git_diff": {"consequential": False, "args": []},
    "diagnostics": {"consequential": False, "args": []},
    # v0.6 harness tools: intercepted by the loop, never reach the sandbox.
    "attempt_completion": {"consequential": False, "args": ["summary"],
                           "harness": True},
    "task_add": {"consequential": False, "args": ["title"], "harness": True},
    "task_update": {"consequential": False, "args": ["id", "status"],
                    "harness": True},
    # Hotfix port (0.5.3): harness-owned interview-convergence tool.
    # Non-consequential; resolved by Loop._resolve_tool_fn to
    # _harness_set_mission (never reaches the sandbox or delegation).
    # Offered in observe/plan only via contract MODES — NOT in
    # HARNESS_TOOLS (those are offered in every mode).
    "set_mission": {"consequential": False, "args": ["text", "criteria"]},
    # Harness-owned story planner (observe/plan only, like set_mission).
    "story_plan": {"consequential": False,
                   "args": ["title", "breakdown", "surveyed", "user_guidance",
                            "proposal", "steps", "bugatti_brief"]},
}


# ---------------------------------------------------------------------------
# patch_file: strict unified-diff application. Fail-closed by design:
#
#   * every hunk must match EXACTLY at its header's line number — zero fuzz;
#   * a hunk whose old content matches nowhere is CONTEXT_MISMATCH;
#   * a hunk whose old content matches in 2+ places is AMBIGUOUS_MATCH
#     (we never guess which one the author meant);
#   * a hunk whose old content matches exactly once but NOT at the header's
#     line number is FUZZY_OFFSET (we never silently shift);
#   * malformed headers, body/count disagreements, multi-file diffs, and
#     anything unrecognized are refused with a named code.
#
# Applies are atomic: the result is written to a temp file in the target
# directory and os.replace()d over the original, so a failed apply can
# never leave a half-written file.
# ---------------------------------------------------------------------------

# Stable refusal codes (also returned as "error_code" in the result dict).
PATCH_BAD_HUNK_HEADER = "BAD_HUNK_HEADER"
PATCH_BODY_COUNT_MISMATCH = "BODY_COUNT_MISMATCH"
PATCH_CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
PATCH_AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
PATCH_FUZZY_OFFSET = "FUZZY_OFFSET"
PATCH_TARGET_MISSING = "PATCH_TARGET_MISSING"
PATCH_EMPTY = "EMPTY_PATCH"
PATCH_MULTI_FILE = "MULTI_FILE_PATCH"
PATCH_UNSUPPORTED = "UNSUPPORTED_DIFF"


class PatchRefusal(Exception):
    """A patch_file refusal with a stable machine-readable code."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


_HUNK_RX = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NO_NEWLINE_MARKER = "\\ No newline at end of file"


def _parse_unified_diff(diff: str) -> tuple[list[dict], bool, bool]:
    """Parse a unified diff strictly.

    Returns (hunks, new_file, strip_trailing_newline). Each hunk is
    {old_start, old_len, new_start, new_len, old_lines, new_lines}.
    Raises PatchRefusal on anything malformed or unrecognized.
    """
    if not isinstance(diff, str):
        raise PatchRefusal(PATCH_UNSUPPORTED, "diff must be a string")
    hunks: list[dict] = []
    new_file = False
    strip_trailing_newline = False
    raw_lines = diff.split("\n")
    n = len(raw_lines)
    i = 0
    # Skip leading blank lines.
    while i < n and not raw_lines[i].strip():
        i += 1

    def _is_file_header(line: str) -> bool:
        return (line.startswith("--- ") or line.startswith("+++ ")
                or line.startswith("diff --git ") or line.startswith("index "))

    while i < n:
        line = raw_lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("@@"):
            m = _HUNK_RX.match(line)
            if not m:
                raise PatchRefusal(
                    PATCH_BAD_HUNK_HEADER,
                    f"malformed hunk header: {line!r}")
            old_start = int(m.group(1))
            old_len = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_len = int(m.group(4)) if m.group(4) is not None else 1
            if old_start == 0 and old_len > 0:
                raise PatchRefusal(
                    PATCH_BAD_HUNK_HEADER,
                    f"hunk claims old start 0 with {old_len} old lines")
            i += 1
            old_lines: list[str] = []
            new_lines: list[str] = []
            while len(old_lines) < old_len or len(new_lines) < new_len:
                if i >= n:
                    raise PatchRefusal(
                        PATCH_BODY_COUNT_MISMATCH,
                        f"hunk @@ -{old_start},{old_len} +{new_start},{new_len} @@ "
                        f"ends after {len(old_lines)} old / {len(new_lines)} "
                        f"new body lines")
                bl = raw_lines[i]
                if bl.startswith("\\"):
                    raise PatchRefusal(
                        PATCH_UNSUPPORTED,
                        f"unexpected {bl.strip()!r} inside a hunk body "
                        f"(only supported as the diff's last line)")
                if bl == "":
                    kind, text = " ", ""  # blank context line
                elif bl[0] in " +-":
                    kind, text = bl[0], bl[1:]
                else:
                    raise PatchRefusal(
                        PATCH_BAD_HUNK_HEADER,
                        f"unexpected diff body line: {bl!r}")
                if kind in (" ", "-"):
                    old_lines.append(text)
                if kind in (" ", "+"):
                    new_lines.append(text)
                i += 1
            hunks.append({"old_start": old_start, "old_len": old_len,
                          "new_start": new_start, "new_len": new_len,
                          "old_lines": old_lines, "new_lines": new_lines})
            continue
        if line.strip() == _NO_NEWLINE_MARKER:
            # Only meaningful as the diff's final line: the resulting file
            # ends without a trailing newline. Anywhere else we cannot
            # model it exactly, so we refuse.
            rest = raw_lines[i + 1:]
            if not all(not r.strip() for r in rest):
                raise PatchRefusal(
                    PATCH_UNSUPPORTED,
                    f"{_NO_NEWLINE_MARKER!r} is only supported as the "
                    f"diff's last line")
            strip_trailing_newline = True
            i = n
            continue
        if _is_file_header(line):
            if hunks:
                raise PatchRefusal(
                    PATCH_MULTI_FILE,
                    "diff touches more than one file; patch_file "
                    "applies to a single target")
            if line.startswith("--- "):
                target = line[4:].split("\t")[0].strip()
                if target == "/dev/null":
                    new_file = True
            if line.startswith("+++ "):
                target = line[4:].split("\t")[0].strip()
                if target == "/dev/null":
                    raise PatchRefusal(
                        PATCH_UNSUPPORTED,
                        "diff deletes the file; patch_file does not "
                        "delete files")
            i += 1
            continue
        raise PatchRefusal(
            PATCH_UNSUPPORTED,
            f"unrecognized diff line before any hunk: {line!r}")
    if not hunks:
        raise PatchRefusal(
            PATCH_EMPTY,
            "no hunks found; not a unified diff (or a binary diff)")
    return hunks, new_file, strip_trailing_newline


def _apply_hunks(orig_lines: list[str], hunks: list[dict]) -> list[str]:
    """Apply parsed hunks to a line list. Zero fuzz: exact match required.

    Each hunk is first tried at its header's line number (adjusted for the
    net line delta of earlier hunks). Anything else is a named refusal.
    """
    lines = list(orig_lines)
    delta = 0  # net lines added by hunks applied so far
    for idx, h in enumerate(hunks, 1):
        old = h["old_lines"]
        pos = 0 if h["old_start"] == 0 else h["old_start"] - 1 + delta
        if not old:
            # Pure insertion: no content to match, so the header position
            # is authoritative; anything outside the file is refused.
            if 0 <= pos <= len(lines):
                lines[pos:pos] = h["new_lines"]
                delta += len(h["new_lines"])
                continue
            raise PatchRefusal(
                PATCH_FUZZY_OFFSET,
                f"hunk {idx}: insertion at line {h['old_start']} is "
                f"outside the {len(lines)}-line file")
        if 0 <= pos <= len(lines) - len(old) \
                and lines[pos:pos + len(old)] == old:
            pass  # exact match at the header's stated position
        else:
            cands = [p for p in range(len(lines) - len(old) + 1)
                     if lines[p:p + len(old)] == old]
            if not cands:
                raise PatchRefusal(
                    PATCH_CONTEXT_MISMATCH,
                    f"hunk {idx}: old content matches nowhere in the file")
            if len(cands) > 1:
                raise PatchRefusal(
                    PATCH_AMBIGUOUS_MATCH,
                    f"hunk {idx}: old content matches "
                    f"{len(cands)} locations "
                    f"(lines {[c + 1 for c in cands]}); refusing to guess")
            raise PatchRefusal(
                PATCH_FUZZY_OFFSET,
                f"hunk {idx}: header says line {h['old_start']} but the "
                f"content matches at line {cands[0] + 1}; refusing to "
                f"apply at an offset")
        lines[pos:pos + len(old)] = h["new_lines"]
        delta += len(h["new_lines"]) - len(old)
    return lines


class Sandbox:
    def __init__(self, root: str | Path, venv_bin: str | Path | None = None,
                 project_root: str | Path | None = None):
        # Resolve the root: on Windows, Temp paths may use 8.3 short names
        # (e.g. RUNNER~1) while Path.resolve() returns the long form. If
        # root is unresolved, _resolve()'s parent check compares short vs
        # long and falsely rejects every path as "escapes sandbox".
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # v0.6: the project root for git tools. Defaults to the sandbox
        # root (the prototype's sandbox IS the project); the extension
        # passes the workspace folder.
        self.project_root = Path(project_root).resolve() if project_root \
            else self.root
        self._manifest: dict[str, str] = {}  # Phase B: path -> sha256 of writes
        # Track A (project bootstrap): when a project .venv exists, every
        # command resolves the venv's bin/ first so the venv python is used
        # automatically — the operator never thinks about activation.
        self.venv_bin = Path(venv_bin) if venv_bin else None

    def _resolve(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"path escapes sandbox: {path!r}")
        return p

    def read_file(self, path: str) -> dict:
        p = self._resolve(path)
        if not p.is_file():
            return {"error": f"not found: {path}"}
        return {"path": path, "content": p.read_text()}

    def write_file(self, path: str, content: str) -> dict:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        digest = hashlib.sha256(content.encode()).hexdigest()[:16]
        # Phase B: record the write in the manifest for tamper detection.
        self._manifest[path] = hashlib.sha256(content.encode()).hexdigest()
        return {"path": path, "digest": digest, "bytes": len(content)}

    def patch_file(self, path: str, diff: str) -> dict:
        """Apply a unified diff to a sandbox file. Fail-closed and atomic.

        Refusals (returned as {"error", "error_code"}, never raised, so
        they journal like any other tool result):
          BAD_HUNK_HEADER / BODY_COUNT_MISMATCH — malformed diff;
          CONTEXT_MISMATCH — hunk matches nowhere;
          AMBIGUOUS_MATCH — hunk matches 2+ locations;
          FUZZY_OFFSET — hunk matches once but not at the header's line;
          PATCH_TARGET_MISSING — target absent and the diff is not a
            pure-addition new-file patch;
          EMPTY_PATCH / MULTI_FILE_PATCH / UNSUPPORTED_DIFF — as named.

        On success the result is written to a temp file in the target
        directory and os.replace()d over the original: a failed apply can
        never leave a half-written file. The write is recorded in the
        sandbox manifest like write_file.
        """
        p = self._resolve(path)  # ValueError on traversal, like write_file
        try:
            hunks, new_file, strip_nl = _parse_unified_diff(diff)
            existed = p.is_file()
            if not existed:
                if not (new_file and all(not h["old_lines"] for h in hunks)):
                    raise PatchRefusal(
                        PATCH_TARGET_MISSING,
                        f"no such file: {path!r} (new-file patches must "
                        f"come from --- /dev/null with only additions)")
                orig_text = ""
            else:
                orig_text = p.read_text()
            if orig_text:
                trailing = orig_text.endswith("\n")
                lines = orig_text.split("\n")
                if trailing:
                    lines = lines[:-1]  # drop the phantom post-newline ""
            else:
                trailing, lines = True, []
            new_lines = _apply_hunks(lines, hunks)
            new_trailing = False if strip_nl else trailing
            new_text = "" if not new_lines else \
                "\n".join(new_lines) + ("\n" if new_trailing else "")
        except PatchRefusal as r:
            return {"error": str(r), "error_code": r.code}
        # Atomic: temp file in the same directory, then os.replace().
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / f".{p.name}.awino-patch-{os.getpid()}.tmp"
        try:
            tmp.write_text(new_text)
            if existed:
                # Keep the original's permission bits (e.g. executable).
                os.chmod(tmp, stat.S_IMODE(p.stat().st_mode))
            os.replace(tmp, p)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        digest = hashlib.sha256(new_text.encode()).hexdigest()
        self._manifest[path] = digest
        return {"path": path, "hunks_applied": len(hunks),
                "digest": digest[:16], "bytes": len(new_text),
                "new_file": (not existed)}

    def manifest(self) -> dict[str, str]:
        """Phase B: {path: sha256} for every file this sandbox wrote."""
        return dict(self._manifest)

    def note_external_write(self, path: str, digest: str) -> None:
        """Native tool application: the extension wrote these bytes, not
        this process. Record the sha256 in the tamper manifest so
        verify_manifest stays honest about delegated writes."""
        self._manifest[path] = digest

    def verify_manifest(self) -> tuple[bool, list[str]]:
        """Phase B: re-hash files on disk; report external tampering.

        Returns (ok, problems). A file the sandbox wrote whose on-disk
        hash differs was modified outside the sandbox.
        """
        problems = []
        for path, expected in self._manifest.items():
            p = self._resolve(path)
            if not p.is_file():
                problems.append(f"{path}: missing on disk")
                continue
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            if actual != expected:
                problems.append(f"{path}: hash mismatch (modified externally)")
        return (not problems, problems)

    def run_command(self, cmd: str, timeout: int = 30,
                    cancel=None) -> dict:
        """Run a shell command with cwd confined to the sandbox.

        Track A: when venv_bin is set (project bootstrap found/created a
        .venv), the venv's executables dir (bin/ on POSIX, Scripts/ on
        Windows) is prepended to PATH and VIRTUAL_ENV is set, so `python`
        resolves to the venv python automatically. Commands run with
        shell=True, i.e. sh on POSIX and cmd.exe on Windows — keep
        commands portable (no Unix-only builtins, no sh-only syntax).

        v0.6: subprocess.run is replaced by Popen + a 50ms poll loop so a
        CancelToken can pre-empt a long-running command: terminate, 2s
        grace, then kill. Result shapes are unchanged otherwise:
        {"cmd", "exit_code", "stdout", "stderr"}, plus "cancelled": True
        (exit 130) on pre-emption. Timeout path unchanged (exit 124).
        Output truncated.
        """
        env = None
        if self.venv_bin and self.venv_bin.is_dir():
            env = dict(os.environ)
            env["PATH"] = (str(self.venv_bin) + os.pathsep
                           + env.get("PATH", ""))
            env["VIRTUAL_ENV"] = str(self.venv_bin.parent)
        try:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=self.root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )
        except OSError as exc:
            return {"cmd": cmd, "exit_code": 127,
                    "stdout": "", "stderr": str(exc)}
        start = time.monotonic()
        cancelled = False
        while True:
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            if time.monotonic() - start >= timeout:
                break
        if cancelled:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            out, err = proc.communicate()
            return {"cmd": cmd, "exit_code": 130, "cancelled": True,
                    "stdout": out[-2000:], "stderr": err[-2000:]}
        if proc.poll() is None:
            # Timeout: kill and reap. stdout is discarded (historical
            # shape); the stderr carries the timeout note.
            proc.kill()
            proc.wait()
            return {"cmd": cmd, "exit_code": 124,
                    "stdout": "", "stderr": f"timed out after {timeout}s"}
        out, err = proc.communicate()
        return {"cmd": cmd, "exit_code": proc.returncode,
                "stdout": out[-2000:], "stderr": err[-2000:]}

    def list_dir(self, path: str = "") -> dict:
        p = self._resolve(path)
        if not p.is_dir():
            return {"error": f"not a directory: {path}"}
        return {"path": path or ".", "entries": sorted(x.name for x in p.iterdir())}
        p = self._resolve(path)
        if not p.is_dir():
            return {"error": f"not a directory: {path}"}
        return {"path": path or ".", "entries": sorted(x.name for x in p.iterdir())}

    # ------------------------------------------------- v0.6 project-context
    # tools. Read-only by construction: they never write. Timeouts are
    # hard (15s) — a slow search returns {"error": "timed out"}, never a
    # hang. Args arrive as JSON scalars; top_k is coerced defensively.

    @staticmethod
    def _coerce_top_k(top_k, default: int = 50) -> int:
        try:
            n = int(top_k)
        except (TypeError, ValueError):
            return default
        return max(1, min(200, n))

    def search_files(self, pattern: str, path: str = "",
                     file_glob: str = "", top_k=50) -> dict:
        """Regex search over file contents. ripgrep when available, else
        stdlib os.walk + re. Returns {matches: [{file, line, text}],
        truncated: bool}."""
        try:
            base = self._resolve(path or "")
        except ValueError as ex:
            return {"error": str(ex)}
        if not base.is_dir():
            return {"error": f"not a directory: {path or '.'}"}
        limit = self._coerce_top_k(top_k)
        try:
            rx = re.compile(pattern)
        except re.error as ex:
            return {"error": f"invalid regex: {ex}"}
        matches: list[dict] = []
        truncated = False
        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "-n", "--no-heading", "-m", str(limit)]
            if file_glob:
                cmd += ["-g", file_glob]
            cmd += ["-e", pattern, str(base)]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=15)
            except subprocess.TimeoutExpired:
                return {"error": "timed out"}
            except OSError as ex:
                return {"error": str(ex)}
            for line in p.stdout.splitlines():
                # rg -n format: file:line:text
                parts = line.split(":", 2)
                if len(parts) != 3:
                    continue
                f, ln, text = parts
                try:
                    ln_no = int(ln)
                except ValueError:
                    continue
                try:
                    f = str(Path(f).resolve().relative_to(self.root))
                except ValueError:
                    pass  # keep the absolute path if it escapes the root
                matches.append({"file": f, "line": ln_no,
                                "text": text[:300]})
                if len(matches) >= limit:
                    break
            truncated = len(matches) >= limit and bool(p.stdout.strip())
        else:
            deadline = time.monotonic() + 15
            for root, _dirs, files in os.walk(base):
                if time.monotonic() > deadline:
                    return {"error": "timed out"}
                for fn in files:
                    if len(matches) >= limit:
                        truncated = True
                        break
                    fp = Path(root) / fn
                    try:
                        text = fp.read_text(errors="strict")
                    except (OSError, UnicodeError, ValueError):
                        continue
                    for i, ln in enumerate(text.splitlines(), 1):
                        if rx.search(ln):
                            matches.append({
                                "file": str(fp.relative_to(self.root)),
                                "line": i, "text": ln[:300]})
                            if len(matches) >= limit:
                                truncated = True
                                break
                    if truncated:
                        break
                if truncated:
                    break
        return {"pattern": pattern, "matches": matches,
                "truncated": truncated}

    def find_symbol(self, name: str, path: str = "") -> dict:
        """Find function/class definitions by name. Python files are parsed
        with ast (exact match, then substring); other files fall back to a
        `^(def|class)\\s+name` ripgrep scan. Returns {symbols: [...]}."""
        try:
            base = self._resolve(path or "")
        except ValueError as ex:
            return {"error": str(ex)}
        if not base.is_dir():
            return {"error": f"not a directory: {path or '.'}"}
        symbols: list[dict] = []
        deadline = time.monotonic() + 15
        import ast as _ast
        for root, _dirs, files in os.walk(base):
            if time.monotonic() > deadline:
                return {"error": "timed out", "symbols": symbols}
            for fn in files:
                fp = Path(root) / fn
                rel = str(fp.relative_to(self.root))
                if fp.suffix == ".py":
                    try:
                        tree = _ast.parse(fp.read_text(errors="strict"))
                    except (OSError, SyntaxError, ValueError):
                        continue
                    for node in _ast.walk(tree):
                        if isinstance(
                                node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                       _ast.ClassDef)):
                            kind = ("class" if isinstance(node, _ast.ClassDef)
                                    else "function")
                            if node.name == name or name in node.name:
                                symbols.append({
                                    "file": rel, "line": node.lineno,
                                    "kind": kind, "name": node.name,
                                    "exact": node.name == name})
                if len(symbols) >= 200:
                    return {"symbols": symbols, "truncated": True}
        # Non-Python fallback via ripgrep when nothing found and rg exists.
        if not symbols and shutil.which("rg"):
            try:
                p = subprocess.run(
                    [shutil.which("rg"), "-n", "--no-heading",
                     rf"^\s*(def|class)\s+{re.escape(name)}\b", str(base)],
                    capture_output=True, text=True, timeout=15)
            except (subprocess.TimeoutExpired, OSError):
                p = None
            if p:
                for line in p.stdout.splitlines()[:200]:
                    parts = line.split(":", 2)
                    if len(parts) != 3:
                        continue
                    f, ln, text = parts
                    try:
                        ln_no = int(ln)
                    except ValueError:
                        continue
                    try:
                        f = str(Path(f).resolve().relative_to(self.root))
                    except ValueError:
                        pass
                    kind = "class" if "class" in text.split()[:2] else "function"
                    symbols.append({"file": f, "line": ln_no, "kind": kind,
                                    "name": name, "exact": True})
        # Exact matches first.
        symbols.sort(key=lambda s: (not s.get("exact"), s["file"], s["line"]))
        return {"name": name, "symbols": symbols[:200],
                "truncated": len(symbols) > 200}

    def _git(self, *args, timeout: int = 15):
        """Run git in the project root. Returns (ok, output)."""
        try:
            p = subprocess.run(["git", "-C", str(self.project_root), *args],
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "timed out"
        except OSError as ex:
            return False, str(ex)
        return p.returncode == 0, p.stdout

    def git_status(self) -> dict:
        """git status --porcelain, capped at 200 lines. Non-git dir is an
        informational {"error": "not a git checkout"}, not a refusal."""
        ok, out = self._git("status", "--porcelain=v1", "-uall")
        if not ok:
            return {"error": "not a git checkout"}
        lines = out.splitlines()
        return {"status": lines[:200], "truncated": len(lines) > 200}

    def git_diff(self, path: str = "") -> dict:
        """git diff HEAD, capped at 400 lines. Binary-safe: falls back to
        --numstat when the output contains NULs."""
        args = ["diff", "HEAD", "--"]
        if path:
            args.append(path)
        ok, out = self._git(*args)
        if not ok:
            return {"error": "not a git checkout"}
        if "\x00" in out:
            ok2, out2 = self._git("diff", "HEAD", "--numstat", "--",
                                  *( [path] if path else []))
            out = out2 if ok2 else ""
            return {"numstat": out.splitlines()[:400],
                    "truncated": len(out.splitlines()) > 400,
                    "binary": True}
        lines = out.splitlines()
        return {"diff": "\n".join(lines[:400]),
                "truncated": len(lines) > 400}

    def diagnostics(self, path: str = "") -> dict:
        """v0.6 = Python only: py_compile every .py file under path and
        report syntax errors. Returns {diagnostics: [{file, line, message}]}.
        Extension-supplied language diagnostics arrive in v0.7; the tool
        shape is forward-compatible."""
        try:
            base = self._resolve(path or "")
        except ValueError as ex:
            return {"error": str(ex)}
        if base.is_file():
            files = [base]
        elif base.is_dir():
            files = sorted(base.rglob("*.py"))
        else:
            return {"error": f"not found: {path or '.'}"}
        import py_compile
        diags: list[dict] = []
        for fp in files[:500]:
            try:
                py_compile.compile(str(fp), doraise=True)
            except py_compile.PyCompileError as ex:
                msg = str(ex)
                line = None
                m = re.search(r"line (\d+)", msg)
                if m:
                    line = int(m.group(1))
                diags.append({"file": str(fp.relative_to(self.root)),
                              "line": line, "message": msg[:300]})
            except (OSError, ValueError):
                continue
        return {"diagnostics": diags, "truncated": len(files) > 500}
