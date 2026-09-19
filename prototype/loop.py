"""The owned turn loop.

The harness runs the `while` (here: per user-turn), compiles the contract
fresh every turn from code-owned state, validates schema + semantics in code,
runs an always-on code-scheduled judge, executes tools behind mode permission
profiles + approval gates, and persists everything to the event log.

Nothing in this file is optional for the model: the model produces a turn
dict; everything else is code.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from state import ProjectState, _uid
from contract import (
    MODES, compile_contract, criterion_status, detect_mission_kind,
    knowledge_counts, next_action_line, parse_criteria, render_header,
    route_mode, validate_header, validate_schema, verify_done_criteria,
)
from stances import evaluate_chain, route_triple, FLOORS
from tools import Sandbox, TOOL_DEFS
from backends import ScriptedJudge
from contract_loop import (
    BREAK_WRITE_WITHOUT_APPROVAL, check_pre_execute, check_pre_turn,
    compile_turn_contract,
)


SCOPE_CHANGE_RE = re.compile(
    r"\bnow let'?s add\b|\bcan we also\b|\blet'?s also\b|\badditionally\b"
    r"|\bscope change\b|\bnew requirement\b|\bactually,?\s+let'?s\b")


def classify_user_input(text: str) -> tuple[str, str]:
    """Parse a user message into a typed event kind."""
    t = text.strip()
    low = t.lower()
    if low.startswith("new objective:"):
        return ("new_objective", t[len("new objective:"):].strip())
    if low in ("approve", "approved", "yes", "ok", "do it", "go ahead"):
        return ("approval", t)
    if SCOPE_CHANGE_RE.search(low):
        return ("scope_change", t)
    if t.endswith("?"):
        return ("question", t)
    return ("info", t)


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


class Loop:
    def __init__(self, home, project_id: str, backend, judge=None,
                 sandbox_dir=None, config: dict | None = None,
                 conversation_id: str | None = None):
        self.home = Path(home)
        self.state = ProjectState(home, project_id, conversation_id)
        self.backend = backend
        self.judge = judge or ScriptedJudge()
        self.sandbox = Sandbox(sandbox_dir or (self.state.dir / "sandbox"))
        cfg = {"max_retries": 3, "max_turns": 50, "stall_limit": 5,
               "token_budget": 200_000}
        cfg.update(config or {})
        self.config = cfg
        self.history: list[dict] = []
        self.setup_checks = [self._check_sandbox_writable]
        self._rebuild_history()

    # ------------------------------------------------------------------ setup
    def _check_sandbox_writable(self) -> str | None:
        try:
            probe = self.sandbox.root / ".writetest"
            probe.write_text("ok")
            probe.unlink()
            return None
        except OSError as e:
            return f"sandbox not writable: {e}"

    def check_setup(self) -> list[str]:
        errs = []
        for check in self.setup_checks:
            err = check()
            if err:
                errs.append(err)
        return errs

    # ---------------------------------------------------------------- history
    def _rebuild_history(self) -> None:
        self.history = []
        for e in self.state.events[-40:]:
            t, d = e["type"], e["data"]
            if t == "user_message":
                self.history.append({"role": "user", "text": d["text"][:500]})
            elif t == "progress_recorded":
                self.history.append({"role": "assistant",
                                     "text": f"[{d['turn_id']}] {d['delta'][:300]}"})
            elif t == "tool_result" and not d.get("reused"):
                self.history.append({"role": "system",
                                     "text": f"tool {d['tool']} -> {str(d['result'])[:200]}"})
        self.history = self.history[-20:]

    def _hist(self, role: str, text: str) -> None:
        self.history.append({"role": role, "text": text})
        self.history = self.history[-20:]

    # ---------------------------------------------------------------- mission
    def set_mission(self, text: str, criteria: list) -> dict:
        snap = self.state.snapshot
        revision = (snap["mission"]["revision"] + 1) if snap["mission"] else 1
        mission = {"id": f"m-{_uid()[:8]}", "text": text,
                   "kind": detect_mission_kind(text),
                   "done_criteria": [parse_criteria(c) for c in criteria],
                   "revision": revision}
        self.state.record("mission_set", {"mission": mission})
        return mission

    def _revision(self) -> int:
        m = self.state.snapshot["mission"]
        return m["revision"] if m else 0

    def _search_dirs(self) -> list:
        return [self.state.dir / "artifacts", self.sandbox.root]

    # -------------------------------------------------------------- user turn
    def run_user_turn(self, text: str) -> dict:
        s = self.state.snapshot
        if s["done"]:
            return {"status": "closed", "said": "Mission already complete (SHIP)."}
        if s["terminal"]:
            return {"status": "closed",
                    "said": f"Terminal state: {s['terminal_reason']}. Start a new project to continue."}
        kind, payload = classify_user_input(text)
        self.state.record("user_message", {"kind": kind, "text": text})
        self._hist("user", text)
        s = self.state.snapshot

        if s["awaiting_inspection"]:
            cid = s["awaiting_inspection"]
            return {"status": "awaiting_inspection",
                    "said": f"Paused: effect {cid} is unknown (crash mid-effect). "
                            f"Inspect, then resolve_inspection('{cid}', "
                            "'already_applied'|'not_applied')."}

        if s["awaiting_approval"]:
            if kind == "approval":
                return self.approve()
            pend = [a["id"] for a in s["approvals"] if a["status"] == "pending"]
            return {"status": "awaiting_approval",
                    "said": f"Still waiting on approval {pend}. /approve <id> or /deny <id>.",
                    "approvals": pend}

        if s["awaiting_operator"]:
            self.state.record("operator_resumed", {})
            s = self.state.snapshot

        if kind == "new_objective":
            self.state.record("objective_set", {"objective": payload})
        elif kind == "scope_change":
            # Trigger rule: scope change drops the elevator, invalidates the
            # prior approval, and requires a revised contract. Approvals never
            # survive a scope change — even a paused turn's (the reducer
            # clears active_turn).
            self.state.record("scope_changed", {"text": text})
        elif s["open_questions"] and kind in ("info", "question"):
            # Heuristic: user text while questions are open resolves them.
            self.state.record("questions_resolved",
                              {"resolved": list(s["open_questions"]), "answer": text})
        return self._run_turn(user_text=text, input_kind=kind)

    # ------------------------------------------------------------------ loop
    # The per-turn pipeline (BUILD_SPEC section 3), executed in order, in code:
    #   0. contract loop      — compile the typed turn contract from code-owned
    #                          state; refuse the turn on a named break BEFORE
    #                          the backend acts (contract_loop.py)
    #   1. contract ingestion — compile+inject the contract from code-owned state
    #   2. elevator sensor   — classify intent; route the (mode, stance, skills)
    #                          triple; scope changes drop the elevator here
    #   3. autonomy & permission gate — compute the permitted tool set for the
    #                          floor BEFORE the backend acts
    #   4. header emission & structured response — the backend echoes the
    #                          harness-rendered header; the harness validates
    #                          it, executes, and composes the response
    #   4b. pre-execute check — the validated turn is re-checked against the
    #                          compiled contract before anything executes;
    #                          hard breaks refuse the turn, a missing approval
    #                          refuses execution and routes to the approval gate
    # ------------------------------------------------------------------
    def _run_turn(self, user_text: str = "", input_kind: str = "info") -> dict:
        s = self.state.snapshot
        cfg = self.config
        if s["turn_count"] >= cfg["max_turns"]:
            self.state.record("budget_exhausted", {"reason": f"max_turns={cfg['max_turns']}"})
            self.state.persist_snapshot()
            return {"status": "budget_exhausted",
                    "said": f"Turn budget exhausted ({cfg['max_turns']}). Terminal."}

        setup_errs = self.check_setup()
        if setup_errs:
            self.state.record("setup_blocked", {"errors": setup_errs})
            return {"status": "setup_blocked",
                    "said": "Setup blocked: " + "; ".join(setup_errs)}

        return self._pipeline(user_text, input_kind)

    def _pipeline(self, user_text: str, input_kind: str) -> dict:
        """Stages 0-4b of the per-turn pipeline."""
        cfg = self.config
        s = self.state.snapshot
        turn_no = s["turn_count"] + 1
        turn_id = f"t{turn_no}"

        # ---- Stage 0: per-turn contract loop (compile -> check -> refuse) ----
        # The contract is compiled from code-owned state BEFORE the backend
        # acts. A broken contract refuses the turn here: the backend is never
        # called and no tool executes.
        tcontract = compile_turn_contract(self.state)
        breaks = check_pre_turn(self.state, tcontract)
        if breaks:
            return self._refuse_turn(breaks, stage="pre_turn")

        # ---- Stage 1: contract ingestion ----
        # The contract block is compiled from code-owned state. (It is
        # compiled after the sensor records its routing, so the injected
        # block already carries the routed triple.)
        # ---- Stage 2: elevator sensor ----
        routing = self._sensor_route(user_text, input_kind)
        contract_block = compile_contract(self.state, turn_no=turn_no)
        # The expected header is the contract's first line: rendered by the
        # harness from the sensor's routing BEFORE the backend acts.
        expected_header = contract_block.split("\n", 1)[0]

        # ---- Stage 3: autonomy & permission gate ----
        # The permitted tool set is computed from the routed mode BEFORE the
        # backend acts. Anything outside it is rejected at validation, even
        # if the backend proposes it.
        offered = self._permission_gate()
        # Recompile the typed contract with the routed triple: the
        # pre-execute check (stage 4b) authoritatively uses the mode the
        # sensor routed for THIS turn.
        tcontract = compile_turn_contract(self.state)

        # ---- Stage 4: header emission & structured response ----
        feedback = None
        turn = None
        for attempt in range(cfg["max_retries"] + 1):
            raw = self.backend.generate(contract_block, self.history, feedback=feedback)
            self._charge_tokens(contract_block, raw)
            errs = validate_schema(raw)
            if not errs and ("mode_hint" in raw or "phase_hint" in raw):
                self.state.record("turn_hint_ignored",
                                  {"turn_id": turn_id,
                                   "hints": {k: raw[k] for k in ("mode_hint", "phase_hint")
                                             if k in raw}})
            if not errs:
                errs = self.validate_semantics(raw, expected_header, offered)
            if not errs:
                self.detect_drift(raw)
                jv = self.judge.judge(raw, contract_block, self._judge_summary())
                if jv["verdict"] == "PASS":
                    self.state.record("judge_passed", {"turn_id": turn_id})
                else:
                    self.state.record("judge_failed",
                                      {"turn_id": turn_id, "reason": jv["reason"]})
                    errs = [f"judge FAIL: {jv['reason']}"]
            if not errs and routing["chain"] != ["advisor"]:
                # Stance fired: the procedure was loaded into the contract
                # block; the output is rubric-evaluated in code.
                ok, failures = evaluate_chain(routing["chain"], raw, user_text)
                if ok:
                    self.state.record("stance_rubric_passed",
                                      {"turn_id": turn_id,
                                       "stance": "->".join(routing["chain"])})
                    if "premortem" in routing["chain"]:
                        self.state.record("premortem_completed",
                                          {"turn_id": turn_id})
                else:
                    self.state.record("stance_rubric_failed",
                                      {"turn_id": turn_id,
                                       "stance": "->".join(routing["chain"]),
                                       "failures": failures})
                    errs = [f"stance rubric FAIL ({'->'.join(routing['chain'])}): "
                            + "; ".join(failures)]
            if not errs:
                turn = raw
                self.state.record("turn_validated", {"turn_id": turn_id, "attempt": attempt})
                break
            self.state.record("turn_rejected",
                              {"turn_id": turn_id, "attempt": attempt, "errors": errs})
            if attempt >= cfg["max_retries"]:
                self.state.record("turn_escalated", {"turn_id": turn_id, "errors": errs})
                self.state.persist_snapshot()
                return {"status": "escalated",
                        "said": "Turn escalated to operator: " + "; ".join(errs),
                        "errors": errs}
            feedback = (f"HARNESS REJECTION (attempt {attempt + 1}): "
                        f"{'; '.join(errs)}. Fix and resubmit a valid TurnContract.")

        assert turn is not None
        # ---- Stage 4b: contract pre-execute check ----
        # The validated turn is checked against the compiled contract BEFORE
        # anything executes. Hard breaks refuse the turn outright (no tool
        # execution). A missing approval refuses execution and routes to the
        # approval gate — the turn pauses, the write does not run.
        xbreaks = check_pre_execute(
            self.state, tcontract, turn,
            has_valid_approval=self._has_valid_approval,
            search_dirs=self._search_dirs())
        hard = [b for b in xbreaks
                if b.reason != BREAK_WRITE_WITHOUT_APPROVAL]
        if hard:
            return self._refuse_turn(hard, stage="pre_execute")
        if xbreaks:
            self.state.record(
                "contract_refused",
                {"stage": "pre_execute",
                 "breaks": [{"reason": b.reason, "detail": b.detail}
                            for b in xbreaks],
                 "recourse": "approval_gate"})
        calls = turn.get("tool_calls", [])
        # Partition: non-consequential (or already approved) calls run now;
        # only consequential calls without approval pause the turn.
        need_idx = {i for i, c in enumerate(calls)
                    if TOOL_DEFS[c["name"]]["consequential"]
                    and not self._has_valid_approval(c)}
        immediate = [c for i, c in enumerate(calls) if i not in need_idx]
        results = self._execute_calls(turn_id, immediate, offset=0)
        need = [(i, calls[i]) for i in sorted(need_idx)]
        if need:
            return self._pause_for_approval(turn_id, turn, results, need,
                                            routing, expected_header)
        return self._finalize_turn(turn_id, turn, results, routing,
                                   expected_header)

    def _sensor_route(self, user_text: str, input_kind: str) -> dict:
        """Stage 2: route the (mode, stance chain, skills) triple in code.

        Intent patterns override the floor defaults. Records routing events;
        returns the live routing for this turn.
        """
        s = self.state.snapshot
        intent, mode, chain, skills, trigger = route_triple(s, user_text,
                                                           input_kind)
        if chain != s["stance_chain"]:
            self.state.record("stance_routed",
                              {"stance": chain[0], "chain": chain,
                               "trigger": trigger})
        if mode != s["mode"]:
            self.state.record("mode_routed",
                              {"mode": mode, "offered": MODES[mode]["tools"],
                               "reason": trigger})
        if skills != s["skills"]:
            self.state.record("skills_routed",
                              {"skills": skills, "trigger": trigger})
        if intent == "ship" and not self.state.snapshot["ship_requested"]:
            self.state.record("ship_requested", {"text": user_text})
        s = self.state.snapshot
        return {"intent": intent, "mode": s["mode"],
                "chain": list(s["stance_chain"]),
                "skills": list(s["skills"]),
                "trigger": s["stance_trigger"]}

    def _permission_gate(self) -> list[str]:
        """Stage 3: the permitted tool set for the current floor/mode.

        Computed BEFORE the backend acts; the backend's tool calls are
        validated against exactly this set.
        """
        return list(MODES[self.state.snapshot["mode"]]["tools"])

    def _refuse_turn(self, breaks: list, stage: str) -> dict:
        """Structured contract refusal: named breaks, recorded in the event
        log, snapshot persisted. The turn does not proceed and no tool ran."""
        self.state.record(
            "contract_refused",
            {"stage": stage,
             "breaks": [{"reason": b.reason, "detail": b.detail}
                        for b in breaks]})
        self.state.persist_snapshot()
        reasons = "; ".join(f"{b.reason}: {b.detail}" for b in breaks)
        return {"status": "contract_refused",
                "breaks": [b.reason for b in breaks],
                "said": (f"Contract refused ({stage}) — the turn did not "
                         f"proceed and no tools ran: {reasons}")}

    # ------------------------------------------------------------- validation
    def validate_semantics(self, turn: dict, expected_header: str,
                           offered: list[str]) -> list[str]:
        errs: list[str] = []
        s = self.state.snapshot
        # Position sensor (pipeline stage 4): the turn must echo the
        # harness-rendered header EXACTLY. A header for a phase the sensor did
        # not select is forgery and rejects the turn.
        errs.extend(validate_header(turn, expected_header))
        # Permission gate (pipeline stage 3): tool calls must stay inside the
        # permitted set computed before the backend acted.
        for c in turn.get("tool_calls", []):
            name = c.get("name")
            if name not in TOOL_DEFS:
                errs.append(f"unknown tool: {name}")
            elif name not in offered:
                errs.append(f"tool '{name}' not offered in mode '{s['mode']}' "
                            f"(offered: {offered})")
        # Floor binding: writes are BUILD-floor only, inside the approved SCOPE.
        for c in turn.get("tool_calls", []):
            if c.get("name") == "write_file":
                path = c.get("args", {}).get("path", "")
                if s["phase"] != "BUILD":
                    errs.append(f"write_file only permitted on the BUILD floor "
                                f"(current: {s['phase']})")
                elif not s["contract_approved"] or s["scope"] is None:
                    errs.append("write_file requires an approved contract with "
                                "SCOPE (/approve-contract <files>)")
                elif path not in (s["scope"] or []):
                    errs.append(f"edit outside approved SCOPE {s['scope']}: "
                                f"{path!r}")
        if s["mission"] and turn.get("tool_calls") and not turn.get("plan") \
                and not s["plan"]:
            errs.append("plan required before any tool action on a mission")
        if s["open_questions"] and turn.get("tool_calls"):
            errs.append("must resolve open questions before acting: "
                        + "; ".join(s["open_questions"][:3]))
        if turn.get("done_claim"):
            ok, gaps = verify_done_criteria(s, self.state.events,
                                            self._search_dirs(),
                                            manual_ok=False)
            if not ok:
                errs.append("done_claim with unverified criteria (forgery): "
                            + "; ".join(gaps))
            elif s["phase"] != "REVIEW":
                errs.append(f"done_claim only honored on the REVIEW floor "
                            f"(current: {s['phase']})")
        return errs

    def detect_drift(self, turn: dict) -> None:
        s = self.state.snapshot
        old, new = s.get("objective"), (turn.get("objective") or "").strip()
        if not old or not new:
            return
        if _overlap(old, new) < 0.25 and (turn.get("tool_calls") or turn.get("plan")):
            q = (f"Objective seems to have shifted from '{old}' to '{new}'. "
                 f"Confirm the new objective or restate the original.")
            self.state.record("drift_flagged", {"old": old, "new": new, "question": q})

    # ------------------------------------------------------------------ judge
    def _judge_summary(self) -> dict:
        s = self.state.snapshot
        return {
            "turn_count": s["turn_count"],
            "phase": s["phase"],
            "results_this_session": sum(1 for e in self.state.events
                                        if e["type"] == "tool_result"),
            "open_questions": list(s["open_questions"]),
        }

    # ------------------------------------------------------------------ tools
    def _idem(self, call: dict) -> str:
        blob = call["name"] + ":" + json.dumps(call["args"], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def _has_valid_approval(self, call: dict) -> bool:
        idem = self._idem(call)
        rev = self._revision()
        return any(a["status"] == "granted" and a["idem_key"] == idem
                   and a["revision"] == rev for a in self.state.snapshot["approvals"])

    def _execute_single(self, call_id: str, tool_name: str, args: dict,
                        idem_key: str) -> dict:
        # Idempotency: never re-execute an effect we already have a result for.
        for e in reversed(self.state.events):
            if e["type"] == "tool_result" and e["data"].get("idem_key") == idem_key:
                res = e["data"]["result"]
                self.state.record("tool_result",
                                  {"call_id": call_id, "tool": tool_name, "args": args,
                                   "idem_key": idem_key, "result": res, "reused": True,
                                   "mission_rev": self.state.snapshot["mission_revision"]})
                return {"tool": tool_name, "result": res, "reused": True}
        self.state.record("tool_called",
                          {"call_id": call_id, "tool": tool_name, "args": args,
                           "idem_key": idem_key,
                           "mission_rev": self.state.snapshot["mission_revision"]})
        fn = getattr(self.sandbox, tool_name)
        try:
            result = fn(**args)
        except Exception as ex:  # noqa: BLE001 - tool errors are data
            result = {"error": f"{type(ex).__name__}: {ex}"}
        self.state.record("tool_result",
                          {"call_id": call_id, "tool": tool_name, "args": args,
                           "idem_key": idem_key, "result": result,
                           "mission_rev": self.state.snapshot["mission_revision"]})
        return {"tool": tool_name, "result": result}

    def _execute_calls(self, turn_id: str, calls: list[dict], offset: int = 0) -> list[dict]:
        results = []
        for i, c in enumerate(calls):
            call_id = f"{turn_id}.{offset + i}"
            results.append(self._execute_single(call_id, c["name"], c["args"],
                                                self._idem(c)))
        return results

    # --------------------------------------------------------------- approvals
    def _pause_for_approval(self, turn_id: str, turn: dict, results_so_far: list,
                            need: list[tuple[int, dict]], routing: dict,
                            expected_header: str) -> dict:
        approvals, pending = [], []
        for i, c in need:
            call_id = f"{turn_id}.{i}"
            ap = {"id": "ap-" + _uid()[:8], "call_id": call_id, "tool": c["name"],
                  "args": c["args"], "idem_key": self._idem(c),
                  "revision": self._revision(), "status": "pending"}
            approvals.append(ap)
            pending.append({"call_id": call_id, "tool": c["name"], "args": c["args"],
                            "idem_key": ap["idem_key"], "approval_id": ap["id"]})
        self.state.record("approval_requested",
                          {"turn_id": turn_id, "approvals": approvals,
                           "pending_calls": pending})
        self.state.record("turn_paused_for_approval",
                          {"turn_id": turn_id, "turn": turn, "results": results_so_far,
                           "routing": routing, "header": expected_header})
        self.state.persist_snapshot()
        ids = [a["id"] for a in approvals]
        return {"status": "awaiting_approval",
                "said": f"Consequential action(s) need approval: {ids}. "
                        f"/approve <id> or /deny <id>.",
                "approvals": ids}

    def _pending_approvals(self) -> list[dict]:
        return [a for a in self.state.snapshot["approvals"] if a["status"] == "pending"]

    def approve(self, approval_id: str | None = None) -> dict:
        all_aps = self.state.snapshot["approvals"]
        if approval_id:
            ap = next((a for a in all_aps if a["id"] == approval_id), None)
            if ap is None:
                return {"status": "none", "said": f"No approval {approval_id}."}
            if ap["status"] == "stale":
                return {"status": "stale",
                        "said": f"Approval {ap['id']} is stale (scope or mission "
                                f"changed). The action was NOT executed."}
            if ap["status"] != "pending":
                return {"status": "none",
                        "said": f"Approval {approval_id} is {ap['status']}."}
        else:
            pend = self._pending_approvals()
            if not pend:
                return {"status": "none", "said": "No pending approvals."}
            ap = pend[0]
        if ap["revision"] != self._revision():
            self.state.record("approval_stale",
                              {"id": ap["id"], "reason": "plan revision changed"})
            self.state.persist_snapshot()
            return {"status": "stale",
                    "said": f"Approval {ap['id']} is stale (mission revision changed). "
                            f"The action was NOT executed."}
        if ap.get("scope_epoch", 0) != self.state.snapshot.get("scope_epoch", 0):
            self.state.record("approval_stale",
                              {"id": ap["id"], "reason": "scope epoch changed"})
            self.state.persist_snapshot()
            return {"status": "stale",
                    "said": f"Approval {ap['id']} is stale (scope changed). "
                            f"The action was NOT executed."}
        self.state.record("approval_granted", {"id": ap["id"]})
        return self._drain_pending()

    def deny(self, approval_id: str | None = None) -> dict:
        pend = self._pending_approvals()
        if not pend:
            return {"status": "none", "said": "No pending approvals."}
        ap = next((a for a in pend if a["id"] == approval_id), None) if approval_id else pend[0]
        if ap is None:
            return {"status": "none", "said": f"No pending approval {approval_id}."}
        self.state.record("approval_denied", {"id": ap["id"]})
        return self._drain_pending(denied=[ap["id"]])

    def _drain_pending(self, denied: list[str] | None = None) -> dict:
        s = self.state.snapshot
        results: list[dict] = []
        for pc in list(s["pending_calls"]):
            ap = next((a for a in s["approvals"] if a["id"] == pc["approval_id"]), None)
            if ap and ap["status"] == "granted":
                results.append(self._execute_single(pc["call_id"], pc["tool"],
                                                    pc["args"], pc["idem_key"]))
        s = self.state.snapshot
        if not s["pending_calls"]:
            self.state.record("approvals_cleared", {})
        active = s.get("active_turn")
        self.state.persist_snapshot()
        if active is None:
            return {"status": "ok", "said": "Approval recorded.",
                    "results": results}
        results = list(active.get("results", [])) + results
        if denied:
            results.append({"denied": denied,
                            "note": "operator denied the write; completed without those effects"})
        return self._finalize_turn(active["turn_id"], active["turn"], results,
                                   active.get("routing") or {},
                                   active.get("header") or "")

    # ------------------------------------------------------- contract approval
    def _has_write_effects(self) -> bool:
        rev = self.state.snapshot["mission_revision"]
        return any(e["type"] == "tool_result"
                   and e["data"].get("tool") == "write_file"
                   and not e["data"].get("result", {}).get("error")
                   and not e["data"].get("reused")
                   and e["data"].get("mission_rev") == rev
                   for e in self.state.events)

    def approve_contract(self, scope: list[str] | None = None) -> dict:
        """Operator approves the contract (elevator gate, code-enforced).

        DEFINE: draft approval → PLAN (scope still pending).
        PLAN: scoped approval → BUILD. BUILD is entered ONLY here; no
        automatic transition exists, and a scope change invalidates this
        approval entirely.
        """
        s = self.state.snapshot
        if not s["mission"]:
            return {"status": "none", "said": "No mission to approve a contract for."}
        if s["phase"] == "DEFINE":
            self.state.record("contract_approved",
                              {"revision": self._revision(), "phase": "DEFINE",
                               "scope": None})
            self.state.record("phase_changed", {"phase": "PLAN"})
            self.state.persist_snapshot()
            return {"status": "ok",
                    "said": f"Contract approved (mission revision {self._revision()}). "
                            f"Phase: PLAN. Next: /approve-contract with a SCOPE "
                            f"file list to authorize BUILD."}
        if s["phase"] == "PLAN":
            sc = list(scope or [])
            self.state.record("contract_approved",
                              {"revision": self._revision(), "phase": "PLAN",
                               "scope": sc})
            self.state.record("phase_changed", {"phase": "BUILD"})
            self.state.persist_snapshot()
            return {"status": "ok",
                    "said": f"Contract approved with SCOPE {sc} "
                            f"(mission revision {self._revision()}). Phase: BUILD."}
        if scope is not None:
            # A scope change at any later phase invalidates pending and
            # granted tool approvals (scope epoch bump) and revokes the
            # BUILD authorization: the operator must re-approve.
            return self._record_scope_change(list(scope))
        return {"status": "none",
                "said": f"No contract to approve in phase {s['phase']}."}

    def _record_scope_change(self, scope: list[str]) -> dict:
        """Record a scope change: epoch bump, approvals stale, elevator down."""
        old = self.state.snapshot["scope"]
        if old is not None and sorted(old) == sorted(scope):
            return {"status": "ok", "said": f"SCOPE unchanged: {scope}."}
        self.state.record("scope_changed", {"scope": scope})
        self.state.persist_snapshot()
        return {"status": "ok",
                "said": (f"SCOPE changed to {scope}. Prior contract and tool "
                         f"approvals are invalidated; approve the revised "
                         f"contract to continue.")}

    # -------------------------------------------------------------- inspection
    def resolve_inspection(self, call_id: str, resolution: str) -> dict:
        s = self.state.snapshot
        if s["awaiting_inspection"] != call_id:
            return {"status": "none",
                    "said": f"No inspection pending for {call_id}."}
        if resolution not in ("already_applied", "not_applied"):
            return {"status": "error",
                    "said": "resolution must be 'already_applied' or 'not_applied'."}
        called = self.state.find_event("tool_called", call_id)
        if not called:
            return {"status": "error", "said": f"No tool_called record for {call_id}."}
        tool, args = called["data"]["tool"], called["data"]["args"]
        idem = called["data"]["idem_key"]
        if resolution == "already_applied":
            # Verify rather than assume: re-read the effect where possible.
            result = {"verified": True, "note": "operator confirmed effect already applied"}
            if tool == "write_file":
                try:
                    result["digest"] = self.sandbox.read_file(args["path"]).get("digest")
                except Exception:  # noqa: BLE001
                    pass
        else:
            fn = getattr(self.sandbox, tool)
            try:
                result = fn(**args)
            except Exception as ex:  # noqa: BLE001
                result = {"error": f"{type(ex).__name__}: {ex}"}
        self.state.record("tool_result",
                          {"call_id": call_id, "tool": tool, "args": args,
                           "idem_key": idem, "result": result,
                           "resolution": resolution})
        self.state.record("inspection_resolved",
                          {"call_id": call_id, "resolution": resolution})
        self.state.persist_snapshot()
        return {"status": "ok", "said": f"Effect {call_id} reconciled as {resolution}."}

    # --------------------------------------------------------------- finalize
    def _has_exit_zero(self) -> bool:
        """VERIFY exit gate: a recorded exit code 0 from run_command,
        in the current mission revision."""
        rev = self.state.snapshot["mission_revision"]
        return any(e["type"] == "tool_result"
                   and e["data"].get("tool") == "run_command"
                   and e["data"].get("result", {}).get("exit_code") == 0
                   and e["data"].get("mission_rev") == rev
                   for e in self.state.events)

    def _finalize_turn(self, turn_id: str, turn: dict, results: list[dict],
                       routing: dict, expected_header: str) -> dict:
        if turn.get("plan"):
            self.state.record("plan_updated", {"plan": turn["plan"]})
        if turn.get("questions"):
            self.state.record("questions_asked", {"questions": turn["questions"]})
        for a in turn.get("assumptions", []):
            self.state.record("assumption_recorded", {"assumption": a})
        self.state.record("progress_recorded",
                          {"turn_id": turn_id, "delta": turn["progress_delta"],
                           "results": len(results)})
        self._hist("assistant", f"[{turn_id}] {turn['progress_delta'][:300]}")

        # Elevator exit gates (code checks on the transitions, not reminders).
        s = self.state.snapshot
        if s["phase"] == "BUILD" and self._has_write_effects():
            # BUILD exit gate: diff produced -> VERIFY. Done criteria are
            # checked at the done claim, not here.
            self.state.record("phase_changed", {"phase": "VERIFY"})
            s = self.state.snapshot
        if s["phase"] == "VERIFY" and self._has_exit_zero():
            self.state.record("phase_changed", {"phase": "REVIEW"})
            s = self.state.snapshot
        if s["phase"] == "REVIEW" and turn.get("done_claim"):
            # Semantic validation already proved every criterion in code, and
            # the turn already passed the REVIEW premortem rubric.
            self.state.record("mission_done", {"turn_id": turn_id, "via": "verified_claim"})
            s = self.state.snapshot

        sig = self._progress_sig()
        stalls = s["consecutive_stalls"] + 1 if sig == s["last_progress_sig"] else 0
        self.state.record("turn_completed",
                          {"turn_id": turn_id, "stalls": stalls, "sig": sig})
        s = self.state.snapshot
        out = {"status": "ok", "turn_id": turn_id, "phase": s["phase"],
               "mode": s["mode"], "results": results,
               "said": self._render_response(turn, routing, expected_header)}
        if stalls >= self.config["stall_limit"]:
            self.state.record("stalled",
                              {"turn_id": turn_id,
                               "reason": f"{stalls} turns without progress"})
            out["status"] = "stalled"
            out["said"] += f"\n[harness] stalled ({stalls} turns, no progress) — escalated."
        if s["tokens_used"] >= self.config["token_budget"]:
            self.state.record("budget_exhausted", {"reason": "token_budget"})
            out["status"] = "budget_exhausted"
        self.state.persist_snapshot()
        return out

    def _progress_sig(self):
        s = self.state.snapshot
        last = s["progress"][-1]["delta"] if s["progress"] else ""
        nres = sum(1 for e in self.state.events if e["type"] == "tool_result")
        return (s["phase"], last, nres, len(s["open_questions"]))

    def _render_response(self, turn: dict, routing: dict,
                         expected_header: str) -> str:
        """Stage 4 response: harness-rendered header, one-line stance
        announcement with the sensor's reason, the deliverable, then the
        next-action line."""
        chain = routing.get("chain") or ["advisor"]
        trigger = routing.get("trigger") or "default"
        parts = [
            expected_header,
            f"STANCE -> {'->'.join(chain)} ({trigger})",
            turn["progress_delta"],
        ]
        if turn.get("questions"):
            parts.append("Questions: " + " | ".join(turn["questions"]))
        if turn.get("assumptions"):
            parts.append("Assuming: " + " | ".join(turn["assumptions"]))
        parts.append(next_action_line(self.state.snapshot))
        return "\n".join(parts)

    def _charge_tokens(self, contract_block: str, turn) -> None:
        approx = len(contract_block) // 4 + len(json.dumps(turn, default=str)) // 4
        self.state.record("tokens_charged", {"tokens": approx})

    # ------------------------------------------------------------ completion
    def request_done(self) -> dict:
        s = self.state.snapshot
        if not s["mission"]:
            return {"status": "none", "said": "No mission set."}
        if s["ship_requested"] and not s["premortem_completed"]:
            # "ship it" requires a successful premortem before the ship
            # transition is allowed.
            return {"status": "refused",
                    "said": "Ship was requested but no premortem completed. "
                            "Run the premortem procedure first."}
        self.state.record("completion_requested", {})
        ok, gaps = verify_done_criteria(s, self.state.events,
                                        self._search_dirs(), manual_ok=True)
        if ok:
            self.state.record("phase_changed", {"phase": "REVIEW"})
            self.state.record("mission_done", {"via": "operator"})
            self.state.persist_snapshot()
            return {"status": "done",
                    "said": "Mission complete. All criteria verified from evidence."}
        self.state.record("done_rejected", {"gaps": gaps})
        self.state.persist_snapshot()
        return {"status": "rejected",
                "said": "Not done. Gaps: " + "; ".join(gaps), "gaps": gaps}

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        s = self.state.snapshot
        crit = []
        if s["mission"]:
            for c in s["mission"]["done_criteria"]:
                ok, label = criterion_status(
                    c, s, self.state.events, self._search_dirs())
                crit.append({"ok": ok, "label": label})
        return {
            "project": s["project_id"], "phase": s["phase"], "mode": s["mode"],
            "stance": s["stance"], "stance_chain": s["stance_chain"],
            "skills": s["skills"], "scope": s["scope"],
            "autonomy": (FLOORS.get(s["phase"], {}) or {}).get("autonomy"),
            "contract_approved": s["contract_approved"],
            "premortem_completed": s["premortem_completed"],
            "ship_requested": s["ship_requested"],
            "mission": s["mission"]["text"] if s["mission"] else None,
            "criteria": crit, "open_questions": s["open_questions"],
            "progress": [p["delta"] for p in s["progress"][-3:]],
            "pending_approvals": [a["id"] for a in s["approvals"]
                                  if a["status"] == "pending"],
            "turns": s["turn_count"], "tokens": s["tokens_used"],
            "flags": s["flags"][-5:], "done": s["done"],
            "next_action": next_action_line(s),
        }
