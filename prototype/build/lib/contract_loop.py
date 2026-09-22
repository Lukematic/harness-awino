"""The per-turn contract loop: compile -> check -> refuse.

Two nested loops (BUILD_SPEC section 4, "The loop"):

  OUTER LOOP = the mission elevator: DEFINE -> PLAN -> BUILD -> VERIFY
               -> REVIEW -> SHIP. Enforced by the phase gates in
               Loop._finalize_turn and Loop.approve_contract. A turn cannot
               skip a floor because the floor IS the path.

  INNER LOOP = this module, running every turn: compile -> validate -> execute.
               compile:  build the typed turn contract from code-owned state
                         (objective, mission, plan revision, the tools the
                         current mode grants, progress vs criteria/evidence).
               validate: check the contract BEFORE the model acts (pre-turn)
                         and check the model's proposed turn against it
                         BEFORE anything executes (pre-execute).
               execute:  reached only when no break is found.

A broken contract refuses the turn with a NAMED break reason — never
silently. Refusal is recorded as a `contract_refused` event in the event
log. The model never compiles the contract and never clears a break:
breaks are detected in code, from state plus the proposed turn.

Break reasons:
  MODE_UNKNOWN             pre-turn     mode is not a known permission profile
  NO_PLAN                  pre-turn     active phase (past PLAN) with no plan
  CONTRACT_STALE           pre-turn     approved contract bound to an old
                                       mission revision
  SCOPE_INVALIDATED        pre-turn /   BUILD without an approved SCOPE, or a
                           pre-execute  write outside the approved SCOPE
  TOOL_NOT_GRANTED         pre-execute  proposed tool not granted by the mode
  WRITE_WITHOUT_APPROVAL   pre-execute  consequential tool without a matching
                                       approval (exact args + current revision)
  COMPLETION_WITHOUT_EVIDENCE pre-execute done_claim without verified criteria
                                       and REVIEW-floor sign-off
"""
from __future__ import annotations

from dataclasses import dataclass

from contract import MODES, criterion_status, verify_done_criteria
from tools import TOOL_DEFS


# Break reasons (stable strings; surfaced in events and refusal messages).
BREAK_MODE_UNKNOWN = "MODE_UNKNOWN"
BREAK_NO_PLAN = "NO_PLAN"
BREAK_CONTRACT_STALE = "CONTRACT_STALE"
BREAK_SCOPE_INVALIDATED = "SCOPE_INVALIDATED"
BREAK_TOOL_NOT_GRANTED = "TOOL_NOT_GRANTED"
BREAK_WRITE_WITHOUT_APPROVAL = "WRITE_WITHOUT_APPROVAL"
BREAK_COMPLETION_WITHOUT_EVIDENCE = "COMPLETION_WITHOUT_EVIDENCE"

# Phases past the planning floors: a plan is required before any tool action.
ACTIVE_PHASES = ("BUILD", "VERIFY", "REVIEW", "SHIP")


@dataclass(frozen=True)
class ContractBreak:
    """One named contract break. Refusals carry these, never bare prose."""
    reason: str
    detail: str


def compile_turn_contract(state) -> dict:
    """Compile the objective->mission->tools->progress contract from
    code-owned state. Called fresh before every turn; the model never sees
    this function, only its rendered form in the injected block."""
    s = state.snapshot
    search_dirs = [state.dir / "artifacts", state.dir / "sandbox"]
    mode = s.get("mode", "observe")
    offered = list(MODES.get(mode, {}).get("tools", []))
    consequential = [t for t in offered
                     if TOOL_DEFS.get(t, {}).get("consequential")]
    mission = s.get("mission")
    criteria = []
    if mission:
        for c in mission.get("done_criteria", []):
            ok, label = criterion_status(c, s, state.events, search_dirs,
                                         manual_ok=False)
            criteria.append({"label": label, "verified": ok})
    approval_revision = None
    for e in reversed(state.events):
        if e["type"] == "contract_approved":
            approval_revision = e["data"].get("revision")
            break
    return {
        "objective": s.get("objective"),
        "mission": ({"id": mission["id"], "text": mission["text"],
                     "kind": mission.get("kind"),
                     "revision": mission.get("revision")}
                    if mission else None),
        "criteria": criteria,
        "criteria_satisfied": (all(c["verified"] for c in criteria)
                               if criteria else True),
        "plan": {"present": bool(s.get("plan")),
                 "items": list(s.get("plan") or [])},
        "mode": mode,
        "phase": s.get("phase"),
        "offered_tools": offered,
        "consequential_tools": consequential,
        "scope": s.get("scope"),
        "scope_epoch": s.get("scope_epoch", 0),
        "contract_approved": bool(s.get("contract_approved")),
        "approval_revision": approval_revision,
        "approvals": [{"id": a["id"], "tool": a["tool"],
                       "status": a["status"],
                       "revision": a.get("revision"),
                       "scope_epoch": a.get("scope_epoch", 0)}
                      for a in s.get("approvals", [])],
        "progress": {"deltas": len(s.get("progress", [])),
                     "last": (s["progress"][-1]["delta"]
                              if s.get("progress") else None)},
    }


def check_pre_turn(state, contract: dict) -> list[ContractBreak]:
    """Breaks detectable from state alone, before the model acts.

    Any break here refuses the turn outright: the backend is never called
    and no tool executes."""
    s = state.snapshot
    breaks: list[ContractBreak] = []
    if contract["mode"] not in MODES:
        breaks.append(ContractBreak(
            BREAK_MODE_UNKNOWN,
            f"mode {contract['mode']!r} is not a known permission profile; "
            f"the tool set cannot be computed"))
    if (s.get("mission") and contract["phase"] in ACTIVE_PHASES
            and not contract["plan"]["present"]):
        breaks.append(ContractBreak(
            BREAK_NO_PLAN,
            f"phase {contract['phase']} is past PLAN but state holds no plan; "
            f"a plan is required before any tool action on a mission"))
    if contract["contract_approved"]:
        rev = contract["mission"]["revision"] if contract["mission"] else None
        if contract["approval_revision"] != rev:
            breaks.append(ContractBreak(
                BREAK_CONTRACT_STALE,
                f"contract approved at revision {contract['approval_revision']}, "
                f"mission is at revision {rev}; the contract was invalidated "
                f"and must be re-approved"))
    if (contract["phase"] == "BUILD"
            and (not contract["contract_approved"]
                 or contract["scope"] is None)):
        breaks.append(ContractBreak(
            BREAK_SCOPE_INVALIDATED,
            "BUILD requires an approved contract with a SCOPE file list; "
            "the contract was invalidated (scope change or never approved)"))
    return breaks


def check_pre_execute(state, contract: dict, turn: dict, *,
                      has_valid_approval, search_dirs) -> list[ContractBreak]:
    """Breaks in the model's proposed turn, checked before anything executes.

    Defense in depth behind the semantic validators: these are the contract's
    own authoritative checks, with named reasons. Any break means no tool
    executes for this turn."""
    breaks: list[ContractBreak] = []
    offered = set(contract["offered_tools"])
    consequential = set(contract["consequential_tools"])
    for c in turn.get("tool_calls", []) or []:
        name = c.get("name")
        args = c.get("args") or {}
        if name not in offered:
            breaks.append(ContractBreak(
                BREAK_TOOL_NOT_GRANTED,
                f"tool {name!r} is not granted by mode {contract['mode']!r} "
                f"(offered: {sorted(offered)})"))
            continue
        if name in consequential and not has_valid_approval(c):
            breaks.append(ContractBreak(
                BREAK_WRITE_WITHOUT_APPROVAL,
                f"consequential tool {name!r} has no matching approval "
                f"(exact arguments + current mission revision); execution "
                f"refused"))
        if name == "write_file":
            scope = contract["scope"]
            path = args.get("path", "")
            if scope is not None and path not in scope:
                breaks.append(ContractBreak(
                    BREAK_SCOPE_INVALIDATED,
                    f"write target {path!r} is outside the approved SCOPE "
                    f"{scope}"))
    if turn.get("done_claim"):
        ok, gaps = verify_done_criteria(state.snapshot, state.events,
                                        search_dirs, manual_ok=False)
        if not ok:
            breaks.append(ContractBreak(
                BREAK_COMPLETION_WITHOUT_EVIDENCE,
                "done_claim with unverified criteria: " + "; ".join(gaps)))
        elif contract["phase"] != "REVIEW":
            breaks.append(ContractBreak(
                BREAK_COMPLETION_WITHOUT_EVIDENCE,
                f"done_claim is honored only on the REVIEW floor (current: "
                f"{contract['phase']}); operator sign-off required"))
    return breaks
