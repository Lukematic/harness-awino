"""Event-sourced per-project state for the A.W.I.N.O. loop-owner prototype.

Layout: <home>/projects/<project_id>/events.jsonl (append-only),
        snapshot.json (derived, rewritten atomically), artifacts/.

The snapshot is *derived* from the event log: every mutation goes through
record() -> apply_event(). Crash recovery replays the log and reconciles the
tail: a tool_called with no matching tool_result is an unknown effect — the
loop pauses for inspection instead of blindly replaying it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

SNAPSHOT_VERSION = 1


def _now() -> float:
    return time.time()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def initial_snapshot(project_id: str, conversation_id: str) -> dict:
    return {
        "version": SNAPSHOT_VERSION,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "objective": None,
        "mission": None,  # {id, text, kind, done_criteria:[...], revision:int}
        "phase": "IDLE",  # IDLE|DEFINE|PLAN|BUILD|VERIFY|REVIEW|SHIP
        "mode": "observe",
        "stance": "advisor",  # advisor|steel-man|feynman|planning-grill (code-routed)
        "stance_trigger": "default",
        "stance_chain": ["advisor"],  # full routed chain; stance = chain[0]
        "skills": [],  # routed skill names (triple router)
        "skill_trigger": "default",
        "scope": None,  # approved SCOPE file list (None = no scoped approval)
        "scope_epoch": 0,  # bumps on every scope change; approvals bind to it
        "mission_revision": 0,  # bumps on every set_mission; evidence binds to it
        "mission_start_ts": None,  # Phase B: wall-clock budget anchor (set on mission_set)
        "revision_history": [],  # append-only: [{revision, mission_id, text,
                                #   kind, done_criteria, ts}]; past entries
                                # are never mutated (Phase B)
        "premortem_completed": False,
        "ship_requested": False,
        "contract_approved": False,  # PLAN -> BUILD elevator gate
        "plan": [],
        "open_questions": [],
        "assumptions": [],
        "progress": [],  # [{turn, delta}]
        "learnings": [],  # Phase C: append-only [{ts, kind, text}] — teaching
                          # snapshots (feynman) and resolved Q/A pairs
        "approvals": [],  # [{id, call_id, tool, args, idem_key, revision, status}]
        "pending_calls": [],  # [{call_id, tool, args, idem_key, approval_id}]
        "active_turn": None,  # validated turn paused for approval
        "flags": [],
        "done": False,
        "terminal": False,
        "terminal_reason": None,
        "worker_budget_allocated": 0,  # Phase D: turns allocated to workers
        "worker_id": None,  # Phase D: set on worker Loops
        "scope": [],  # Phase D: worker file ownership (list of path prefixes)
        "awaiting_approval": False,
        "awaiting_inspection": None,  # call_id or None
        "awaiting_operator": False,
        "turn_count": 0,
        "retries": 0,
        "consecutive_stalls": 0,
        "last_progress_sig": None,
        "tokens_used": 0,
    }


def apply_event(snap: dict, ev: dict) -> None:
    """Fold one event into the snapshot. All state changes flow through here."""
    t = ev["type"]
    d = ev["data"]
    if t == "objective_set":
        snap["objective"] = d["objective"]
    elif t == "mission_set":
        snap["mission"] = d["mission"]
        snap["mission_revision"] = snap.get("mission_revision", 0) + 1
        # Phase B: append-only revision history; past entries are immutable.
        hist = snap.setdefault("revision_history", [])
        m = d["mission"]
        hist.append({"revision": m["revision"], "mission_id": m["id"],
                     "text": m["text"], "kind": m["kind"],
                     "done_criteria": m["done_criteria"], "ts": ev["ts"]})
        snap["mission_start_ts"] = ev["ts"]  # Phase B: wall-clock budget anchor
        snap["phase"] = "DEFINE"
        snap["plan"] = []
        snap["open_questions"] = []
        snap["assumptions"] = []
        snap["contract_approved"] = False  # new contract needs new approval
        snap["scope"] = None
        snap["skills"] = []
        snap["skill_trigger"] = "default"
        snap["stance_chain"] = ["advisor"]
        snap["premortem_completed"] = False
        snap["ship_requested"] = False
        snap["retries"] = 0
        snap["done"] = False
        snap["terminal"] = False
        snap["terminal_reason"] = None
    elif t == "plan_updated":
        snap["plan"] = d["plan"]
    elif t == "questions_asked":
        for q in d["questions"]:
            if q not in snap["open_questions"]:
                snap["open_questions"].append(q)
    elif t == "questions_resolved":
        resolved = set(d["resolved"])
        snap["open_questions"] = [q for q in snap["open_questions"] if q not in resolved]
    elif t == "assumption_recorded":
        snap["assumptions"].append(d["assumption"])
    elif t == "mode_routed":
        snap["mode"] = d["mode"]
    elif t == "stance_routed":
        snap["stance"] = d["stance"]
        snap["stance_trigger"] = d["trigger"]
        snap["stance_chain"] = d.get("chain", [d["stance"]])
    elif t == "skills_routed":
        snap["skills"] = d["skills"]
        snap["skill_trigger"] = d["trigger"]
    elif t == "scope_changed":
        # Approvals never survive a scope change: invalidate, drop the
        # elevator, require a revised contract. A paused turn is discarded.
        snap["contract_approved"] = False
        snap["scope"] = d.get("scope")
        snap["scope_epoch"] = snap.get("scope_epoch", 0) + 1
        for a in snap["approvals"]:
            if a["status"] == "pending":
                a["status"] = "stale"
        snap["active_turn"] = None
        snap["premortem_completed"] = False
        if snap["phase"] not in ("DEFINE", "PLAN"):
            snap["phase"] = "PLAN" if snap["plan"] else "DEFINE"
        snap["flags"].append("scope changed — revised contract required "
                             "before BUILD")
    elif t == "contract_approved":
        snap["contract_approved"] = True
        snap["scope"] = d.get("scope")  # None = draft approval, list = scoped
    elif t == "premortem_completed":
        snap["premortem_completed"] = True
    elif t == "ship_requested":
        snap["ship_requested"] = True
    elif t == "transition_refused":
        snap["flags"].append(
            f"transition refused: {d['from']} -> {d['to']} ({d['reason']})")
    elif t == "phase_changed":
        snap["phase"] = d["phase"]
    elif t == "turn_rejected":
        snap["retries"] += 1
    elif t == "turn_escalated":
        snap["awaiting_operator"] = True
        snap["retries"] = 0
    elif t == "operator_resumed":
        snap["awaiting_operator"] = False
        snap["retries"] = 0
    elif t == "stalled":
        snap["awaiting_operator"] = True
    elif t == "judge_failed":
        snap["flags"].append(d["reason"])
    elif t == "stance_rubric_failed":
        snap["flags"].append(f"stance rubric fail ({d['stance']}): "
                             + "; ".join(d["failures"]))
    elif t == "drift_flagged":
        snap["flags"].append("drift: " + d["question"])
        snap["open_questions"].append(d["question"])
    elif t == "approval_requested":
        snap["approvals"].extend(d["approvals"])
        snap["pending_calls"].extend(d["pending_calls"])
        snap["awaiting_approval"] = True
    elif t == "approval_granted":
        for a in snap["approvals"]:
            if a["id"] == d["id"]:
                a["status"] = "granted"
    elif t in ("approval_denied", "approval_stale"):
        for a in snap["approvals"]:
            if a["id"] == d["id"]:
                a["status"] = "denied" if t == "approval_denied" else "stale"
        snap["pending_calls"] = [c for c in snap["pending_calls"] if c["approval_id"] != d["id"]]
        if not any(a["status"] == "pending" for a in snap["approvals"]):
            snap["awaiting_approval"] = False
    elif t == "approvals_cleared":
        snap["awaiting_approval"] = False
    elif t == "turn_paused_for_approval":
        snap["active_turn"] = {"turn_id": d["turn_id"], "turn": d["turn"],
                               "results": d.get("results", []),
                               "routing": d.get("routing"),
                               "header": d.get("header")}
    elif t == "tool_result":
        cid = d["call_id"]
        snap["pending_calls"] = [c for c in snap["pending_calls"] if c["call_id"] != cid]
    elif t == "progress_recorded":
        snap["progress"].append({"turn": d["turn_id"], "delta": d["delta"]})
    elif t == "learning_recorded":
        # Phase C: append-only learning record.
        snap.setdefault("learnings", []).append(
            {"ts": ev["ts"], "kind": d["kind"], "text": d["text"]})
    elif t == "worker_spawned":
        # Phase D: track allocated worker budget.
        snap["worker_budget_allocated"] = (
            snap.get("worker_budget_allocated", 0)
            + d.get("budget_share", {}).get("max_turns", 0))
    elif t == "worker_scoped":
        # Phase D: worker file ownership (fixed scope).
        snap["worker_id"] = d["worker_id"]
        snap["scope"] = list(d["owned_files"])
    elif t == "mission_done":
        snap["done"] = True
        snap["phase"] = "SHIP"
        snap["active_turn"] = None
    elif t == "budget_exhausted":
        snap["terminal"] = True
        snap["terminal_reason"] = d["reason"]
    elif t == "effect_unknown":
        snap["awaiting_inspection"] = d["call_id"]
        snap["flags"].append("unknown effect: " + d["call_id"])
    elif t == "inspection_resolved":
        snap["awaiting_inspection"] = None
    elif t == "turn_completed":
        snap["turn_count"] += 1
        snap["retries"] = 0
        snap["active_turn"] = None
        snap["consecutive_stalls"] = d.get("stalls", 0)
        snap["last_progress_sig"] = d.get("sig")
    elif t == "tokens_charged":
        snap["tokens_used"] += d["tokens"]
    # Unknown / informational event types leave the snapshot untouched.


class ProjectState:
    """Per-project event-sourced state."""

    def __init__(self, home: str | Path, project_id: str, conversation_id: str | None = None):
        self.home = Path(home)
        self.project_id = project_id
        self.dir = self.home / "projects" / project_id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "artifacts").mkdir(exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.snapshot_path = self.dir / "snapshot.json"
        self.events: list[dict] = []
        self.snapshot: dict = initial_snapshot(project_id, conversation_id or _uid())
        self.repaired_tail = False
        self._load()
        self._reconcile_tail()

    # ---- loading / recovery -------------------------------------------------
    def _load(self) -> None:
        for ev in self._read_events():
            self.events.append(ev)
            apply_event(self.snapshot, ev)

    def _read_events(self) -> list[dict]:
        if not self.events_path.exists():
            return []
        data = self.events_path.read_bytes()
        lines = data.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        events = []
        for i, ln in enumerate(lines):
            if not ln.strip():
                continue
            try:
                events.append(json.loads(ln))
            except json.JSONDecodeError:
                if i == len(lines) - 1:
                    # Torn final write from a crash: truncate to last good line.
                    good = b"\n".join(lines[:i]) + (b"\n" if i > 0 else b"")
                    self.events_path.write_bytes(good)
                    self.repaired_tail = True
                    break
                raise ValueError(f"corrupt event log at line {i} (not the tail)")
        return events

    def _reconcile_tail(self) -> None:
        """Crash recovery: tool_called without a matching tool_result is an
        unknown effect. Pause for inspection; never blindly replay."""
        called: dict[str, dict] = {}
        resulted: set[str] = set()
        for e in self.events:
            if e["type"] == "tool_called":
                called[e["data"]["call_id"]] = e["data"]
            elif e["type"] == "tool_result":
                resulted.add(e["data"]["call_id"])
        for cid, data in called.items():
            if cid not in resulted:
                self.record(
                    "effect_unknown",
                    {
                        "call_id": cid,
                        "tool": data["tool"],
                        "args": data.get("args", {}),
                        "note": "tool_called without tool_result at load; "
                        "effect unknown, paused for inspection",
                    },
                )

    # ---- mutation -----------------------------------------------------------
    def record(self, etype: str, data: dict | None = None) -> dict:
        ev = {"seq": len(self.events), "id": _uid(), "ts": _now(),
              "type": etype, "data": data or {}}
        line = (json.dumps(ev) + "\n").encode()
        with open(self.events_path, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self.events.append(ev)
        apply_event(self.snapshot, ev)
        return ev

    def persist_snapshot(self) -> None:
        tmp = self.snapshot_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snapshot, indent=2))
        os.replace(tmp, self.snapshot_path)

    def find_event(self, etype: str, call_id: str) -> dict | None:
        for e in self.events:
            if e["type"] == etype and e["data"].get("call_id") == call_id:
                return e
        return None
