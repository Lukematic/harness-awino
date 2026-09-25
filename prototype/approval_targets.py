"""Approval-target visibility for shell commands.

The harness has no dangerous-command blacklist by design: the user is the
authority and approved shell commands may address external/absolute
resources. This module makes that boundary VISIBLE at the approval point
so the human decides with full information.

Given a shell command string and the workspace root, resolve_shell_targets
returns the command's file targets as absolute paths and flags any target
(or the effective cwd) that falls OUTSIDE the workspace root.

Command shapes RESOLVED:
  - leading ``cd <dir>`` segments (including chains: ``cd a && cd b && ...``)
    adjust the effective cwd that later targets resolve against
  - output/input redirects: ``>``, ``>>``, ``<``, ``2>``, ``&>``, ... (also
    glued forms like ``2>err.log``)
  - file operands of: rm, mv, cp, ln, touch, mkdir, rmdir, cat, tee
    (every non-flag operand is a target)
  - file operands of chmod / chown (every non-flag operand AFTER the
    mode/owner argument is a target)
  - a leading ``sudo``/``doas``/``command`` prefix is skipped

Command shapes conservatively marked UNRESOLVED (never silently skipped):
  - any segment containing shell expansion (``$VAR``, ``$(...)``,
    backticks, ``${...}``) — the value is unknowable without a shell
  - glob patterns (``*.log``) — expanded by the shell at run time
  - unparseable quoting (shlex failure)
  - pipes/process substitution/command shapes for any command NOT in the
    operand list above (e.g. ``ffmpeg -i in.mp4 out.mp4``) — the operands
    are recorded verbatim as unresolved; redirects in the same segment
    are still resolved
  - ``cd`` with an unresolvable operand (expansion/glob) — the cwd is left
    unchanged and the segment is recorded as unresolved

Resolution is lexical plus symlink-aware: paths are absolutized against
the effective cwd, then run through os.path.realpath so a symlink inside
the workspace pointing outside still flags. ``..`` escapes are normalized
before the containment check, so ``../../etc/passwd`` flags.

Nothing here executes anything and nothing is blocked: the result is
pure data attached to the approval card. STDLIB ONLY.
"""
from __future__ import annotations

import os
import shlex

__all__ = ["resolve_shell_targets", "in_workspace"]

# command -> operand rule:
#   "all":         every non-flag operand is a file target
#   "after_first": first non-flag operand is not a path (mode/owner/...),
#                 the rest are file targets
_OPERAND_PATH_COMMANDS = {
    "rm": "all", "mv": "all", "cp": "all", "ln": "all",
    "touch": "all", "mkdir": "all", "rmdir": "all", "cat": "all",
    "tee": "all",
    "chmod": "after_first", "chown": "after_first",
}

# wrapper prefixes that are skipped before reading the real command
_WRAPPER_PREFIXES = {"sudo", "doas", "command"}

# redirect operators, longest first for prefix matching
_REDIRECT_OPS = ("&>>", "&>", "<<<", "<<", "2>>", "1>>", ">>", ">|", ">",
                 "2>", "1>", "<>", "<")


def in_workspace(abs_path: str, workspace_root: str) -> bool:
    """True when abs_path is inside (or equal to) workspace_root."""
    try:
        common = os.path.commonpath(
            [os.path.realpath(abs_path), os.path.realpath(workspace_root)])
    except (ValueError, OSError):
        # different drives (Windows) or unparseable path: not inside
        return False
    return common == os.path.realpath(workspace_root)


def _split_segments(cmd: str) -> list[str]:
    """Split a command line on &&, ||, ;, |, & respecting quotes/escapes."""
    segments, buf = [], []
    quote: str | None = None
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(cmd[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(cmd[i + 1])
            i += 1
        elif cmd.startswith("&&", i) or cmd.startswith("||", i):
            segments.append("".join(buf))
            buf = []
            i += 1
        elif ch in (";", "|", "&"):
            segments.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s for s in segments if s.strip()]


def _has_expansion(segment: str) -> bool:
    # quoted dollars still expand in double quotes; be conservative and
    # treat any $ or backtick as dynamic.
    return "$" in segment or "`" in segment


def _has_glob(token: str) -> bool:
    return any(c in token for c in "*?[")


def _resolve_path(token: str, eff_cwd: str) -> str:
    expanded = os.path.expanduser(token)
    if not os.path.isabs(expanded):
        expanded = os.path.join(eff_cwd, expanded)
    return os.path.realpath(os.path.normpath(expanded))


def resolve_shell_targets(cmd: str, workspace_root: str) -> dict:
    """Resolve a shell command's file targets against the workspace root.

    Returns a dict with:
      command, workspace_root, effective_cwd (absolute, after cd segments),
      targets: [{raw, path (absolute), in_workspace, kind}],
      unresolved: [{raw, reason, unresolved: True}],
      outside_workspace: True when any resolved target or the effective
        cwd falls outside the workspace root.
    """
    root = os.path.realpath(os.path.abspath(workspace_root))
    eff_cwd = root
    targets: list[dict] = []
    unresolved: list[dict] = []

    def add_target(raw: str, token: str, kind: str) -> None:
        path = _resolve_path(token, eff_cwd)
        targets.append({"raw": raw, "path": path,
                        "in_workspace": in_workspace(path, root),
                        "kind": kind})

    def add_unresolved(raw: str, reason: str) -> None:
        unresolved.append({"raw": raw, "reason": reason, "unresolved": True})

    for segment in _split_segments(cmd or ""):
        seg = segment.strip()
        if not seg:
            continue
        if _has_expansion(seg):
            add_unresolved(seg, "contains shell expansion ($VAR, $(...), "
                                "backticks) — value unknowable without a shell")
            continue
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            add_unresolved(seg, "unparseable quoting")
            continue
        if not tokens:
            continue
        idx = 0
        while idx < len(tokens) and tokens[idx] in _WRAPPER_PREFIXES:
            idx += 1
        if idx >= len(tokens):
            continue
        name = os.path.basename(tokens[idx])
        rest = tokens[idx + 1:]

        if name == "cd":
            operand = None
            for t in rest:
                if t == "--":
                    continue
                if t.startswith("-") and t != "-":
                    continue  # flag
                operand = t
                break
            if operand is None:
                eff_cwd = os.path.realpath(os.path.expanduser("~"))
            elif operand == "-":
                add_unresolved(seg, "cd - (previous directory) — "
                                    "unknowable without shell state")
            elif _has_glob(operand):
                add_unresolved(seg, "cd with glob operand — cwd unchanged")
            else:
                eff_cwd = _resolve_path(operand, eff_cwd)
            continue

        rule = _OPERAND_PATH_COMMANDS.get(name)
        seen_non_flag = 0
        flags_done = False
        operand_recorded_unresolved = False
        i = 0
        while i < len(rest):
            tok = rest[i]
            # redirect: glued ("2>err.log") or split ("> out.txt")
            red_target = None
            red_op_only = False
            for op in _REDIRECT_OPS:
                if tok == op:
                    if i + 1 < len(rest):
                        red_target = rest[i + 1]
                        i += 1
                    else:
                        red_op_only = True
                    break
                if tok.startswith(op) and len(tok) > len(op):
                    red_target = tok[len(op):]
                    break
            if red_op_only:
                add_unresolved(seg, f"redirect operator {tok!r} with no "
                                    "target — malformed command")
                i += 1
                continue
            if red_target is not None:
                if _has_glob(red_target):
                    add_unresolved(seg, "redirect target is a glob — "
                                        "expanded by the shell at run time")
                else:
                    add_target(red_target, red_target, "redirect")
                i += 1
                continue
            if tok == "--":
                flags_done = True
            elif not flags_done and tok.startswith("-") and tok != "-":
                pass  # flag
            else:
                flags_done = True
                if rule == "all" or (rule == "after_first"
                                     and seen_non_flag >= 1):
                    if _has_glob(tok):
                        add_unresolved(tok, "glob pattern — expanded by the "
                                            "shell at run time")
                    else:
                        add_target(tok, tok, "operand")
                elif rule is None and not operand_recorded_unresolved:
                    add_unresolved(seg, f"unsupported command shape {name!r} "
                                        "— operands not resolved")
                    operand_recorded_unresolved = True
                seen_non_flag += 1
            i += 1

    outside = (not in_workspace(eff_cwd, root)
               or any(not t["in_workspace"] for t in targets))
    return {"command": cmd or "", "workspace_root": root,
            "effective_cwd": eff_cwd, "targets": targets,
            "unresolved": unresolved, "outside_workspace": outside}
