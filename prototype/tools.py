"""Sandboxed demo tools. All file effects are confined to a sandbox dir;
path traversal is rejected. write_file is consequential (needs approval).
run_command executes a shell command with cwd confined to the sandbox,
a hard timeout, and truncated output (Phase 0: mock/test use only).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

TOOL_DEFS = {
    "read_file": {"consequential": False, "args": ["path"]},
    "write_file": {"consequential": True, "args": ["path", "content"]},
    "list_dir": {"consequential": False, "args": []},
    "run_command": {"consequential": False, "args": ["cmd"]},
}


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
