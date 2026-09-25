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
import stat
import subprocess
from pathlib import Path

TOOL_DEFS = {
    "read_file": {"consequential": False, "args": ["path"]},
    "write_file": {"consequential": True, "args": ["path", "content"]},
    "patch_file": {"consequential": True, "args": ["path", "diff"]},
    "list_dir": {"consequential": False, "args": []},
    "run_command": {"consequential": False, "args": ["cmd"]},
    # Harness-owned (not a Sandbox method): the model sets the mission from
    # the discovery interview. Non-consequential — the user is driving the
    # interview, the mission is revision-tracked and visible, and the
    # skill-kind gate still applies. Dispatched by Loop._execute_single.
    "set_mission": {"consequential": False, "args": ["text", "criteria"]},
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
    def __init__(self, root: str | Path, venv_bin: str | Path | None = None):
        # Resolve the root: on Windows, Temp paths may use 8.3 short names
        # (e.g. RUNNER~1) while Path.resolve() returns the long form. If
        # root is unresolved, _resolve()'s parent check compares short vs
        # long and falsely rejects every path as "escapes sandbox".
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
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

    def run_command(self, cmd: str, timeout: int = 30) -> dict:
        """Run a shell command with cwd confined to the sandbox.

        Track A: when venv_bin is set (project bootstrap found/created a
        .venv), the venv's executables dir (bin/ on POSIX, Scripts/ on
        Windows) is prepended to PATH and VIRTUAL_ENV is set, so `python`
        resolves to the venv python automatically. Commands run with
        shell=True, i.e. sh on POSIX and cmd.exe on Windows — keep
        commands portable (no Unix-only builtins, no sh-only syntax).

        Returns {"cmd", "exit_code", "stdout", "stderr"}. Output truncated.
        """
        env = None
        if self.venv_bin and self.venv_bin.is_dir():
            env = dict(os.environ)
            env["PATH"] = (str(self.venv_bin) + os.pathsep
                           + env.get("PATH", ""))
            env["VIRTUAL_ENV"] = str(self.venv_bin.parent)
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=self.root,
                capture_output=True, text=True, timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {"cmd": cmd, "exit_code": 124,
                    "stdout": "", "stderr": f"timed out after {timeout}s"}
        except OSError as exc:
            return {"cmd": cmd, "exit_code": 127,
                    "stdout": "", "stderr": str(exc)}
        return {"cmd": cmd, "exit_code": proc.returncode,
                "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}

    def list_dir(self, path: str = "") -> dict:
        p = self._resolve(path)
        if not p.is_dir():
            return {"error": f"not a directory: {path}"}
        return {"path": path or ".", "entries": sorted(x.name for x in p.iterdir())}
        p = self._resolve(path)
        if not p.is_dir():
            return {"error": f"not a directory: {path}"}
        return {"path": path or ".", "entries": sorted(x.name for x in p.iterdir())}
