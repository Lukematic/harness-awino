#!/usr/bin/env python3
"""A.W.I.N.O. MCP server (stdio, standard library only).

Exposes the loop-owner harness to MCP clients (e.g. Kilo Code) as tools.
Every tool is backed by the REAL prototype code — the same contract
compiler, schema + semantic validators, judge panel, and skill-synthesis
pipeline that `awino chat` uses. Nothing is reimplemented here.

Transport: newline-delimited JSON-RPC 2.0 on stdin/stdout.
Logging goes to stderr only (stdout is the protocol channel).

Fail-closed: every tool error becomes a JSON-RPC error or an
`isError` tool result. The server never crashes on bad input.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backends import EchoBackend
from contract import compile_contract, validate_schema, MODES
from contract_loop import compile_turn_contract, check_pre_execute
from judges import build_judge_panel
from loop import Loop
from stances import STANCES
from synthesis import synthesize_learning
from tools import Sandbox

SERVER_INFO = {"name": "awino-mcp", "version": "0.1.0"}
PROTOCOL_VERSION = "2024-11-05"


class ToolError(Exception):
    """A tool refused to run: fail-closed with a clear message."""


# ---------------------------------------------------------------------------
# Mission registry: mission_id -> real Loop (EchoBackend: no model calls).
# State is event-sourced on disk under a temp home dir per mission.
# ---------------------------------------------------------------------------

_missions: dict[str, Loop] = {}


def _new_mission_id() -> str:
    return "mcp-" + secrets.token_hex(4)


def _get_loop(mission_id) -> Loop:
    loop = _missions.get(mission_id)
    if loop is None:
        raise ToolError(f"unknown mission_id: {mission_id!r}")
    return loop


def tool_new_mission(args: dict) -> dict:
    objective = args.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ToolError("objective is required (non-empty string)")
    criteria = args.get("criteria")
    if criteria is not None and (
        not isinstance(criteria, list)
        or not all(isinstance(c, str) for c in criteria)
    ):
        raise ToolError("criteria must be a list of strings")

    mid = _new_mission_id()
    home = tempfile.mkdtemp(prefix="awino-mcp-")
    loop = Loop(home, mid, EchoBackend(), build_judge_panel())
    loop.state.record("objective_set", {"objective": objective.strip()})

    mission = None
    if criteria is not None:
        # Real mission path: kind detection, skill-requirement check, criteria
        # parsing — the same code `awino chat /mission` runs.
        mission = loop.set_mission(objective.strip(), criteria)

    _missions[mid] = loop
    out: dict = {
        "mission_id": mid,
        "contract_block": compile_contract(loop.state),
    }
    if mission is None:
        # Discovery-interview framing: no mission until the interview resolves.
        out["interview"] = {
            "open": True,
            "procedure": STANCES["planning-grill"]["procedure"],
            "frontier": ["mission", "primary user", "goals", "tenets",
                         "expectations", "success metric"],
            "instruction": (
                "Discovery interview is OPEN. Ask ONE question at a time "
                "(no tool calls, no plan) until the frontier is resolved; "
                "never present a spec until it is. Then call awino_new_mission "
                "again with the refined objective and a criteria list to set "
                "the mission."
            ),
        }
    else:
        out["mission"] = {"id": mission["id"], "kind": mission["kind"],
                          "revision": mission["revision"]}
    return out


def tool_compile_contract(args: dict) -> dict:
    loop = _get_loop(args.get("mission_id"))
    return {"mission_id": args.get("mission_id"),
            "contract_block": compile_contract(loop.state)}


def tool_validate_turn(args: dict) -> dict:
    """The REAL turn pipeline validators: schema, then semantics, then the
    per-turn contract's pre-execute breaks. Mirrors loop.py's turn gate."""
    loop = _get_loop(args.get("mission_id"))
    turn = args.get("turn")
    reasons = validate_schema(turn)
    if not reasons:
        # Same header the agent was shown: first line of the fresh contract.
        expected_header = compile_contract(loop.state).splitlines()[0]
        mode = loop.state.snapshot.get("mode", "observe")
        offered = list(MODES.get(mode, {}).get("tools", []))
        reasons = loop.validate_semantics(turn, expected_header, offered)
    if not reasons:
        tcontract = compile_turn_contract(loop.state)
        breaks = check_pre_execute(
            loop.state, tcontract, turn,
            has_valid_approval=loop._has_valid_approval,
            search_dirs=[loop.state.dir / "artifacts", loop.sandbox.root],
        )
        reasons = [f"{b.reason}: {b.detail}" for b in breaks]
    return {"ok": not reasons, "reasons": reasons}


def tool_judge_turn(args: dict) -> dict:
    loop = _get_loop(args.get("mission_id"))
    turn = args.get("turn")
    block = args.get("contract_block")
    if not isinstance(turn, dict):
        raise ToolError("turn must be a JSON object")
    if not isinstance(block, str) or not block:
        raise ToolError("contract_block is required (non-empty string)")
    panel = build_judge_panel()  # deterministic judges: fail-closed default
    s = loop.state.snapshot
    summary = {"turn_count": s.get("turn_count", 0),
               "phase": s.get("phase"),
               "results_this_session": 0,
               "open_questions": list(s.get("open_questions", []))}
    v = panel.judge(turn, block, summary)
    return {"verdict": v["verdict"], "reason": v["reason"],
            "votes": [{"judge": x["judge"], "verdict": x["verdict"],
                       "reason": x["reason"]} for x in v["votes"]]}


def tool_synthesize_learning(args: dict) -> dict:
    learning = args.get("learning")
    if not isinstance(learning, dict):
        raise ToolError("learning must be a JSON object {id, text}")
    sb = Sandbox(tempfile.mkdtemp(prefix="awino-mcp-synth-sb-"))
    reg = tempfile.mkdtemp(prefix="awino-mcp-synth-reg-")
    # Real pipeline: screen -> draft -> sandbox-verify -> hash-pin -> admit.
    return synthesize_learning(learning, sb, reg)


_HANDLERS = {
    "awino_new_mission": tool_new_mission,
    "awino_compile_contract": tool_compile_contract,
    "awino_validate_turn": tool_validate_turn,
    "awino_judge_turn": tool_judge_turn,
    "awino_synthesize_learning": tool_synthesize_learning,
}

_TOOL_SPECS = [
    {"name": "awino_new_mission",
     "description": "Start a new A.W.I.N.O. mission. Records the objective and "
                    "returns the mission_id plus the opening contract block. "
                    "Without criteria, the discovery interview is open: ask "
                    "one question at a time, then call again with criteria.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "objective": {"type": "string"},
                         "criteria": {"type": "array", "items": {"type": "string"}}},
                     "required": ["objective"]}},
    {"name": "awino_compile_contract",
     "description": "Compile the current turn contract block from code-owned "
                    "state. Call EVERY turn before acting; echo the header "
                    "line back exactly in the turn's header field.",
     "inputSchema": {"type": "object",
                     "properties": {"mission_id": {"type": "string"}},
                     "required": ["mission_id"]}},
    {"name": "awino_validate_turn",
     "description": "Run the harness's real schema + semantic validators on "
                    "a proposed turn BEFORE acting. Returns {ok, reasons[]}. "
                    "Do not act when ok is false.",
     "inputSchema": {"type": "object",
                     "properties": {"mission_id": {"type": "string"},
                                    "turn": {"type": "object"}},
                     "required": ["mission_id", "turn"]}},
    {"name": "awino_judge_turn",
     "description": "Run the deterministic judge panel on a validated turn. "
                    "Returns {verdict, reason, votes}. Refuse to proceed on "
                    "a FAIL verdict.",
     "inputSchema": {"type": "object",
                     "properties": {"mission_id": {"type": "string"},
                                    "turn": {"type": "object"},
                                    "contract_block": {"type": "string"}},
                     "required": ["mission_id", "turn", "contract_block"]}},
    {"name": "awino_synthesize_learning",
     "description": "Bank a learning as a verified skill. The learning must "
                    "carry VERIFY: checks; unverified prose is refused.",
     "inputSchema": {"type": "object",
                     "properties": {"learning": {"type": "object"}},
                     "required": ["learning"]}},
]


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------

def _err(code: int, message: str, req_id=None) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _ok(result, req_id) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _handle(req) -> dict | None:
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" \
            or not isinstance(req.get("method"), str):
        return _err(-32600, "invalid request", req.get("id")
                    if isinstance(req, dict) else None)
    method, params = req["method"], req.get("params") or {}
    req_id = req.get("id")

    if method == "initialize":
        return _ok({"protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO}, req_id)
    if method == "notifications/initialized":
        return None  # notification: no response
    if method == "tools/list":
        return _ok({"tools": _TOOL_SPECS}, req_id)
    if method == "tools/call":
        name = params.get("name")
        handler = _HANDLERS.get(name)
        if handler is None:
            return _ok({"content": [{"type": "text",
                                     "text": f"unknown tool: {name!r}"}],
                         "isError": True}, req_id)
        try:
            result = handler(params.get("arguments") or {})
        except ToolError as e:
            return _ok({"content": [{"type": "text", "text": f"refused: {e}"}],
                         "isError": True}, req_id)
        except Exception as e:  # fail-closed: never crash the server
            traceback.print_exc(file=sys.stderr)
            return _ok({"content": [{"type": "text",
                                     "text": f"internal error: "
                                             f"{type(e).__name__}: {e}"}],
                         "isError": True}, req_id)
        return _ok({"content": [{"type": "text",
                                 "text": json.dumps(result)}]}, req_id)
    return _err(-32601, f"method not found: {method}", req_id)


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            resp = _err(-32700, "parse error")
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue
        try:
            resp = _handle(req)
        except Exception:  # last-resort: never die on a request
            traceback.print_exc(file=sys.stderr)
            rid = req.get("id") if isinstance(req, dict) else None
            resp = _err(-32603, "internal error", rid)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
