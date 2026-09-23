#!/usr/bin/env python3
"""A.W.I.N.O. gate hook for Claude Code (PreToolUse).

Opt-in: completely inert unless <project>/.awino/contract.json exists, so it
never bricks normal Claude Code usage. When a project IS opted in, every
proposed tool call is checked against the turn contract compiled by the
harness: a tool the contract does not offer is BLOCKED (exit 2) before it
executes. This is code-level enforcement of the tool-execution boundary —
the closest Claude Code gets to the harness owning the loop.

contract.json schema (written by the loop-owner; see integrations/claude/):
    {
      "mission_id": "m-9f3a",
      "stage": "BUILD",
      "tools_offered": ["Read", "Bash", "Glob"],   # Claude Code tool names
      "turn": 7,            # turn number the contract was compiled for
      "expires_turn": 9     # contract valid through this turn (inclusive)
    }

Hook input (stdin): Claude Code PreToolUse JSON with at least
`tool_name` and `cwd`. Exit 0 = allow, exit 2 = block with a reason.

Fail-closed: any internal error blocks with a clear reason. A security gate
must never crash open.
"""
from __future__ import annotations

import json
import os
import sys

CONTRACT_DIR = ".awino"
CONTRACT_FILE = "contract.json"


def _block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 2


def gate(payload: dict, project_dir: str) -> int:
    """Return 0 to allow the tool call, 2 to block it."""
    contract_path = os.path.join(project_dir, CONTRACT_DIR, CONTRACT_FILE)
    if not os.path.isfile(contract_path):
        return 0  # not opted in: hook is inert

    try:
        with open(contract_path, encoding="utf-8") as f:
            contract = json.load(f)
    except Exception as e:
        return _block(
            "A.W.I.N.O.: .awino/contract.json is unreadable "
            f"({type(e).__name__}); fail-closed. Recompile the contract "
            "with awino_compile_contract."
        )
    if not isinstance(contract, dict):
        return _block(
            "A.W.I.N.O.: .awino/contract.json is malformed (not an object); "
            "fail-closed. Recompile the contract with awino_compile_contract."
        )

    offered = contract.get("tools_offered")
    if (not isinstance(offered, list) or not offered
            or not all(isinstance(t, str) for t in offered)):
        return _block(
            "A.W.I.N.O.: contract has no usable tools_offered list; "
            "fail-closed. Recompile the contract with awino_compile_contract."
        )

    turn = contract.get("turn")
    expires = contract.get("expires_turn")
    if (isinstance(turn, int) and isinstance(expires, int)
            and turn > expires):
        return _block(
            f"A.W.I.N.O.: turn contract expired (turn {turn} > expires_turn "
            f"{expires}); fail-closed. Recompile with awino_compile_contract."
        )

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return _block(
            "A.W.I.N.O.: hook payload has no tool_name; fail-closed."
        )

    if tool_name not in offered:
        stage = contract.get("stage", "?")
        return _block(
            f"A.W.I.N.O.: tool '{tool_name}' is not offered by the turn "
            f"contract (stage {stage}; offered: {', '.join(offered)}). "
            "Draft a new turn and recompile the contract — do not act."
        )
    return 0


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception as e:
        return _block(
            f"A.W.I.N.O.: could not read hook input ({e}); fail-closed."
        )
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return _block(
            "A.W.I.N.O.: hook input is not valid JSON; fail-closed "
            "(a security gate never crashes open)."
        )
    if not isinstance(payload, dict):
        return _block(
            "A.W.I.N.O.: hook input is not a JSON object; fail-closed."
        )
    project_dir = payload.get("cwd") or os.getcwd()
    try:
        return gate(payload, project_dir)
    except Exception as e:  # fail-closed: the gate must never crash open
        return _block(
            f"A.W.I.N.O.: gate error ({type(e).__name__}: {e}); fail-closed."
        )


if __name__ == "__main__":
    sys.exit(main())
