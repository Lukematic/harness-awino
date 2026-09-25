"""Event-sourced per-project state for the A.W.I.N.O. loop-owner prototype.

Layout: <home>/projects/<project_id>/events.jsonl (append-only),
        snapshot.json (derived, rewritten atomically), artifacts/.

The snapshot is *derived* from the event log: every mutation goes through
record() -> apply_event(). Crash recovery replays the log and reconciles the
tail: a tool_called with no matching tool_result is an unknown effect — the
loop pauses for inspection instead of blindly replaying it.

Tamper evidence (journal chaining): every event recorded after this feature
lands carries ``prev_hash`` and ``hash``. ``prev_hash`` is the SHA-256 of the
canonical JSON of the previous event (``"GENESIS"`` for the first event);
``hash`` is the SHA-256 of the canonical JSON of the event itself including
``prev_hash``. ``verify_chain()`` walks ``events.jsonl`` and reports the
first bad ``seq``. Events written before chaining existed carry no hash
fields and are allowed as a legacy prefix so existing journals keep
loading; each hashed event commits to its immediate predecessor's exact
bytes, so only the pre-chain predecessor (not earlier legacy history) is
tamper-evident.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from secret_redaction import redact, redact_text

SNAPSHOT_VERSION = 1

# Journal chaining: hash-chain tamper evidence for the event log.
CHAIN_VERSION = 1
GENESIS_PREV_HASH = "GENESIS"


def _canonical_bytes(ev: dict) -> bytes:
    """Canonical bytes of an event for hashing: sorted keys, compact
    separators, UTF-8. Key order and whitespace are normalized so the
    digest is stable across record-time and verify-time computation."""
    return json.dumps(ev, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


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
        # v0.6: harness-owned TODO list (state-authoritative across rounds)
        "tasks": [],  # [{id, title, status, notes, created_round, updated_round}]
        "task_seq": 0,  # monotonically increasing task id counter
        "flags": [],
        "done": False,
        "terminal": False,
        "terminal_reason": None,
        "worker_budget_allocated": 0,  # Phase D: turns allocated to workers
        "worker_id": None,  # Phase D: set on worker Loops
        "scope": [],  # Phase D: worker file ownership (list of path prefixes)
        "role_mode": None,  # Track D: active role lens {role, reason, source}
        "verify_pass": None,  # Track G: journaled verifier verdict that passed
        "verify_pending": None,  # Track G: {worker_id} while verifier runs
        "awaiting_approval": False,
        "awaiting_inspection": None,  # call_id or None
        # v0.6: set when the recursive loop halts on round budget or stall.
        "awaiting_operator": False,
        "awaiting_operator_reason": None,
        "awaiting_operator_round": None,
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
        # Track G: a new mission needs a fresh verification — a stale pass
        # from the previous mission must never unlock REVIEW.
        snap["verify_pass"] = None
        snap["verify_pending"] = None
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
        # Rigor (three-strike): the doom-loop circuit breaker. Set by the
        # harness when 3 consecutive failures share one signature; cleared by
        # the next validated turn. While active the router injects the
        # rigor-three-strike skill — an explicit rollback-and-rethink
        # transition instead of failing forward.
        snap["doom_loop_active"] = False
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
    # Track D: role lens routing. A lens, never permissions — it only changes
    # which skill bodies the harness routes into the contract.
    elif t == "role_mode":
        snap["role_mode"] = {"role": d["role"], "reason": d.get("reason", ""),
                             "source": d.get("source", "router")}
    # Track G: verification lifecycle. Only a verifier worker's journaled
    # verdict (verify_passed) unlocks REVIEW — never a builder claim.
    elif t == "verify_started":
        snap["verify_pending"] = {"worker_id": d["worker_id"], "ts": ev["ts"]}
        snap["verify_pass"] = None
    elif t == "verify_verdict":
        snap["verify_pending"] = None
    elif t == "verify_passed":
        snap["verify_pass"] = {"worker_id": d["worker_id"],
                               "verdict": d["verdict"], "ts": ev["ts"]}
    elif t == "verify_failed":
        snap["verify_pass"] = None
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
    elif t == "turn_validated":
        # A validated turn breaks any doom loop: the agent recovered.
        snap["doom_loop_active"] = False
    elif t == "doom_loop_detected":
        # Rigor circuit breaker: 3 consecutive failures, one signature.
        # The router injects rigor-three-strike until a turn validates.
        snap["doom_loop_active"] = True
        snap["flags"].append(
            f"doom loop detected ({d.get('consecutive', 3)}x "
            f"{d.get('source', 'failures')}): {d.get('signature', '')[:80]}")
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
                               "header": d.get("header"),
                               # v0.6: round context so the resumed loop
                               # continues at the next round, not round 0.
                               "round_ctx": d.get("round_ctx")}
    elif t == "tool_result":
        cid = d["call_id"]
        snap["pending_calls"] = [c for c in snap["pending_calls"] if c["call_id"] != cid]
    elif t == "progress_recorded":
        snap["progress"].append({"turn": d["turn_id"], "delta": d["delta"]})
    # v0.6: harness-owned TODO list. task_added skips duplicates by title
    # (the loop checks before journaling; this is belt-and-braces).
    elif t == "task_added":
        if not any(x["title"] == d["title"] for x in snap["tasks"]):
            snap["task_seq"] += 1
            snap["tasks"].append({"id": f"t{snap['task_seq']}",
                                  "title": d["title"], "status": "todo",
                                  "notes": "", "created_round": d.get("round"),
                                  "updated_round": d.get("round")})
    elif t == "task_updated":
        for x in snap["tasks"]:
            if x["id"] == d["id"]:
                x["status"] = d["status"]
                if d.get("notes") is not None:
                    x["notes"] = d["notes"]
                x["updated_round"] = d.get("round")
                break
    # v0.6: the recursive loop's budget/stall halt. Journaled with the
    # round number and reason so the operator sees exactly what happened.
    elif t == "round_budget_exhausted":
        snap["awaiting_operator"] = True
        snap["awaiting_operator_reason"] = d.get("reason")
        snap["awaiting_operator_round"] = d.get("round")
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


LEGACY_HOME = Path.home() / ".awino-loop"


def project_slug(folder: str | Path) -> str:
    import re
    return re.sub(r"[^a-z0-9-]+", "-", Path(folder).name.lower()).strip("-") \
        or "project"


def default_home(workspace: str | Path) -> Path:
    """Where a project's session state lives: inside the project, next to
    its registry and stories (<workspace>/.awino), so the CLI and the VS
    Code extension share one memory. AWINO_HOME overrides (tests, CI)."""
    env = os.environ.get("AWINO_HOME")
    return Path(env) if env else Path(workspace) / ".awino"


def adopt_legacy_state(home: str | Path, project_id: str) -> Path | None:
    """One-time move of pre-0.7 state (~/.awino-loop/projects/<id>) into the
    project home when the project has none yet. Copies, never deletes the
    old folder. Returns the source when state was adopted."""
    import shutil
    home = Path(home)
    src = LEGACY_HOME / "projects" / project_id
    dst = home / "projects" / project_id
    if home.resolve() == LEGACY_HOME.resolve() or not src.is_dir():
        return None
    if (dst / "snapshot.json").exists() or (dst / "events.jsonl").exists():
        return None
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return src


def ensure_state_ignored(home: str | Path) -> None:
    """Session journals hold tool output and file contents: keep them out of
    the user's git history by default (stories and the registry stay
    tracked)."""
    gi = Path(home) / ".gitignore"
    if Path(home).name == ".awino" and not gi.exists():
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text("# Local session state (journals, snapshots).\n"
                      "projects/\n")


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
        # Journaling boundary: high-confidence secrets are redacted BEFORE
        # persistence. The redacted copy is what is written to events.jsonl
        # AND kept in memory, so every downstream consumer (journal export,
        # compaction, turn history) only ever sees the redacted form.
        # redact() returns a new structure; the caller's dict is untouched.
        redacted_data = redact(data or {})
        if self.events:
            prev_hash = hashlib.sha256(
                _canonical_bytes(self.events[-1])).hexdigest()
        else:
            prev_hash = GENESIS_PREV_HASH
        ev = {"seq": len(self.events), "id": _uid(), "ts": _now(),
              "type": etype, "data": redacted_data, "prev_hash": prev_hash}
        # The hash commits to the event including prev_hash (but not to
        # itself — it is computed before the "hash" key exists).
        ev["hash"] = hashlib.sha256(_canonical_bytes(ev)).hexdigest()
        line = (json.dumps(ev) + "\n").encode()
        with open(self.events_path, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self.events.append(ev)
        apply_event(self.snapshot, ev)
        return ev

    def verify_chain(self) -> tuple[bool, int | None]:
        """Verify the journal hash chain by walking events.jsonl.

        Returns (True, None) when every hashed event links correctly to
        its predecessor. Returns (False, seq) naming the first bad
        event's seq when a prev_hash does not match the predecessor's
        bytes, a hash does not match the event's own bytes (payload
        modified), or a line is not valid JSON (no seq available ->
        None). Events without hash fields (written before chaining)
        are allowed as a legacy prefix; each hashed event still commits
        to its immediate predecessor's exact bytes, so tampering with
        the pre-chain predecessor breaks the first hashed link, while
        earlier legacy history is not tamper-evident.
        """
        if not self.events_path.exists():
            return (True, None)
        lines = self.events_path.read_bytes().split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        events: list[dict] = []
        for ln in lines:
            if not ln.strip():
                continue
            try:
                events.append(json.loads(ln))
            except json.JSONDecodeError:
                return (False, None)
        prev_bytes: bytes | None = None
        for ev in events:
            seq = ev.get("seq")
            h = ev.get("hash")
            want_prev = (GENESIS_PREV_HASH if prev_bytes is None
                         else hashlib.sha256(prev_bytes).hexdigest())
            if h:
                if ev.get("prev_hash") != want_prev:
                    return (False, seq)
                body = {k: v for k, v in ev.items() if k != "hash"}
                if hashlib.sha256(_canonical_bytes(body)).hexdigest() != h:
                    return (False, seq)
            prev_bytes = _canonical_bytes(ev)
        return (True, None)

    def persist_snapshot(self) -> None:
        # journal_tip_hash: the hash of the last journaled event at the
        # time the snapshot was written. Lets a reader detect tail
        # truncation (removed tail events still leave a valid chain).
        tip = self.events[-1].get("hash") if self.events else None
        self.snapshot["journal_tip_hash"] = tip
        tmp = self.snapshot_path.with_suffix(".tmp")
        # Defense in depth: the snapshot is derived from already-redacted
        # events, but redact the serialized form anyway so a secret can
        # never persist in cleartext in snapshot.json either.
        tmp.write_text(redact_text(json.dumps(self.snapshot, indent=2)))
        os.replace(tmp, self.snapshot_path)

    def find_event(self, etype: str, call_id: str) -> dict | None:
        for e in self.events:
            if e["type"] == etype and e["data"].get("call_id") == call_id:
                return e
        return None
