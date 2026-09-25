"""The owned turn loop.

The harness runs the `while` (here: per user-turn), compiles the contract
fresh every turn from code-owned state, validates schema + semantics in code,
runs an always-on code-scheduled judge, executes tools behind mode permission
profiles + approval gates, and persists everything to the event log.

Nothing in this file is optional for the model: the model produces a turn
dict; everything else is code.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import inspect
import json
import re
import time
from pathlib import Path

from state import ProjectState, _uid
from contract import (
    MODES, HARNESS_TOOLS, compile_contract, criterion_status, detect_mission_kind,
    knowledge_counts, next_action_line, parse_criteria, render_header,
    route_mode, validate_header, validate_schema, verify_done_criteria,
    coerce_turn_contract, ContractTypeError,
    skills_for_kind, get_skill_store,
)
import modes as _modes
from skills import SkillIntegrityError
from synthesis import synthesize_learning as _synthesize_learning
from stances import evaluate_chain, route_triple, FLOORS
from tools import (Sandbox, TOOL_DEFS, _parse_unified_diff, _apply_hunks,
                    PatchRefusal)
from approval_targets import resolve_shell_targets
from backends import ScriptedJudge
from contract_loop import (
    BREAK_WRITE_WITHOUT_APPROVAL, check_pre_execute, check_pre_turn,
    compile_turn_contract,
)
# v0.6 recursive agent loop: cooperative cancellation, provider-agnostic
# tool schemas, and native tool-call normalization.
from cancel import CancelToken, Cancelled
from tool_schema import schemas_for
from provider_tools import NormalizationError
from hooks import HookRegistry


SCOPE_CHANGE_RE = re.compile(
    r"\bnow let'?s add\b|\bcan we also\b|\blet'?s also\b|\badditionally\b"
    r"|\bscope change\b|\bnew requirement\b|\bactually,?\s+let'?s\b")


# ---------------------------------------------------------------------------
# Phase B: explicit phase transition table. All phase changes go through
# Loop.request_phase, which refuses illegal jumps (recording
# transition_refused) instead of silently allowing them.
# ---------------------------------------------------------------------------
ALLOWED_TRANSITIONS = {
    "IDLE": ("DEFINE",),
    "DEFINE": ("PLAN", "DEFINE"),  # DEFINE->DEFINE: mission re-set
    "PLAN": ("BUILD", "DEFINE"),
    "BUILD": ("VERIFY", "PLAN", "DEFINE"),
    "VERIFY": ("REVIEW", "BUILD", "DEFINE"),
    "REVIEW": ("SHIP", "BUILD", "DEFINE"),
    "SHIP": ("DEFINE",),  # new mission after ship
}


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


# ---------------------------------------------------------------------------
# Sidecar streaming protocol (spec §3.2–3.3).
#
# _SidecarStream is the per-turn streaming context. The sidecar attaches an
# emitter (loop.sidecar_emit) and an optional turn_start metadata hook
# (loop.sidecar_turn_meta); both are None in every other context (CLI, MCP,
# unit tests), where every streaming code path is a no-op and today's
# behavior is preserved exactly.
#
# The stream object doubles as the backend stream_cb(kind, text): kind is
# "thinking" or "said". Thinking text is UI-only — it is accumulated here
# for turn_result enrichment and never written to the journal, the
# contract, or any .awino/ file.
# ---------------------------------------------------------------------------
class _SidecarStream:
    CHUNK_CAP = 4096  # max text payload per stdout line (split larger)
    THINKING_CAP = 8000  # max accumulated thinking chars in turn_result

    def __init__(self, emit, turn_id: str, turn_start_extra=None):
        self._emit = emit
        self.turn_id = turn_id
        self._turn_start_extra = turn_start_extra
        self._thinking_parts: list[str] = []
        self._thinking_chars = 0
        self._thinking_truncated = False
        self._thinking_seen = False
        self.checks: list[dict] = []

    # -- stream_cb(kind, text) -------------------------------------------
    def __call__(self, kind: str, text: str) -> None:
        if kind == "thinking":
            self._thinking_seen = True
            self._accumulate_thinking(text or "")
            self._emit_text("thinking_delta", text or "")
        elif kind == "said":
            self._emit_text("said_delta", text or "")

    def _emit_text(self, event: str, text: str) -> None:
        if not text:
            return
        # Split so every chunk's UTF-8 encoding is at most CHUNK_CAP bytes;
        # multi-byte characters are never cut (a single char is <= 4 bytes).
        buf: list[str] = []
        size = 0
        for ch in text:
            n = len(ch.encode("utf-8"))
            if buf and size + n > self.CHUNK_CAP:
                self._emit({"event": event, "turn_id": self.turn_id,
                            "text": "".join(buf)})
                buf, size = [], 0
            buf.append(ch)
            size += n
        if buf:
            self._emit({"event": event, "turn_id": self.turn_id,
                        "text": "".join(buf)})

    def _accumulate_thinking(self, text: str) -> None:
        if self._thinking_truncated or not text:
            return
        room = self.THINKING_CAP - self._thinking_chars
        if room <= 0:
            self._thinking_parts.append("… [truncated]")
            self._thinking_truncated = True
            return
        self._thinking_parts.append(text[:room])
        self._thinking_chars += min(len(text), room)
        if len(text) > room:
            self._thinking_parts.append("… [truncated]")
            self._thinking_truncated = True

    def thinking_text(self) -> str | None:
        """Full accumulated thinking, or None when no thinking_delta was
        emitted (spec: the provider exposes no thinking)."""
        if not self._thinking_seen:
            return None
        return "".join(self._thinking_parts)

    # -- turn_start -------------------------------------------------------
    def turn_start(self, phase: str, mode_id: str) -> None:
        event = {"event": "turn_start", "turn_id": self.turn_id,
                 "phase": phase,
                 "mode": {"id": mode_id, "source": "stage"},
                 "persona": None}
        if self._turn_start_extra is not None:
            extra = self._turn_start_extra(self.turn_id, phase, mode_id) or {}
            if isinstance(extra.get("mode"), dict):
                event["mode"] = extra["mode"]
            if "persona" in extra:
                event["persona"] = extra["persona"]
        self._emit(event)

    # -- harness_check ----------------------------------------------------
    def harness_check(self, check: str, verdict: str, detail: str = "") -> None:
        row = {"check": check, "verdict": verdict, "detail": detail or ""}
        self.checks.append(row)
        self._emit({"event": "harness_check", "turn_id": self.turn_id, **row})

    # -- tool_progress ----------------------------------------------------
    def tool_progress(self, tool: str, phase: str, summary: str = "",
                      ms: int | None = None) -> None:
        event = {"event": "tool_progress", "turn_id": self.turn_id,
                 "tool": tool, "phase": phase, "summary": summary or ""}
        if ms is not None:
            event["ms"] = ms
        self._emit(event)


def _ms_since(t0: float) -> int:
    return max(0, int((time.monotonic() - t0) * 1000))


def _tool_call_summary(tool_name: str, args: dict) -> str:
    """Short human summary for a tool_progress start event (no secrets:
    paths/commands only)."""
    args = args or {}
    for key in ("path", "cmd", "pattern"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val[:80]
    return tool_name


def _tool_failed(result) -> bool:
    return (isinstance(result, dict)
            and (bool(result.get("error"))
                 or (isinstance(result.get("exit_code"), int)
                     and result["exit_code"] != 0)))


def _tool_result_summary(result) -> str:
    if isinstance(result, dict):
        if result.get("error"):
            return str(result["error"])[:160]
        if isinstance(result.get("exit_code"), int):
            extra = ""
            err = str(result.get("stderr") or "").strip().splitlines()
            if err:
                extra = ": " + err[0][:120]
            return f"exit {result['exit_code']}{extra}"
        return json.dumps(result, default=str)[:160]
    return str(result)[:160]


def _judge_check_rows(judge, jv: dict):
    """Rows for harness_check emission from a judge verdict: one per panel
    vote when the judge exposes votes (JudgePanel), else a single row for
    the judge backend. Yields (name, passed, reason)."""
    votes = jv.get("votes") if isinstance(jv, dict) else None
    if isinstance(votes, list) and votes:
        for v in votes:
            name = v.get("judge") or "?"
            yield (str(name), v.get("verdict") == "PASS",
                   str(v.get("reason") or ""))
        return
    name = getattr(judge, "name", None) or type(judge).__name__
    verdict = jv.get("verdict") if isinstance(jv, dict) else None
    reason = jv.get("reason") if isinstance(jv, dict) else ""
    yield (str(name), verdict == "PASS", str(reason or ""))


class FanoutFailed(Exception):
    """Fail-closed fan-out error: at least one worker did not succeed.

    Carries .details = {"objective": str, "workers": [{"worker_id": str,
    "status": "ok"|"failed", "result": dict|None, "error": str|None}]}.
    Successful workers' results are included explicitly — never silent,
    never partial without saying so.
    """

    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


class UnknownBackendError(ValueError):
    """Named fail-closed error for fan-out model routing: a subtask named a
    backend that is not in the request's code-owned routing map
    (``model_routes``). Raised in fan-out pre-flight, before any worker
    spawns. A ValueError subclass so it reads like the other fan-out
    refusals ("fanout refused: ...")."""


def _owned_overlap(a: list, b: list) -> str | None:
    """Return a representative overlapping owned-file entry, or None.

    Uses the same prefix semantics as the worker scope check in
    _execute_single: owned entries are prefixes, so "docs/" owns
    "docs/api/x.txt" and overlaps "docs/api/".
    """
    norm = lambda p: str(p).rstrip("/")
    for x in a:
        for y in b:
            nx, ny = norm(x), norm(y)
            if nx == ny or nx.startswith(ny + "/") or ny.startswith(nx + "/"):
                return str(x)
    return None


def _worker_hit_scope_violation(wloop) -> bool:
    """True if the worker's journal holds a ScopeViolation tool result."""
    for e in wloop.state.events:
        if e.get("type") == "tool_result":
            if "ScopeViolation" in str(e.get("data", {}).get("result", "")):
                return True
    return False


def _default_fanout_runner(wloop, subtask: dict) -> dict:
    """Default fanout worker runner: set the subtask as the worker's
    mission and drive one turn through the worker's own pipeline."""
    wloop.set_mission(subtask["objective"], ["manual"])
    return wloop.run_user_turn(subtask["objective"])


class Loop:
    def __init__(self, home, project_id: str, backend, judge=None,
                 sandbox_dir=None, config: dict | None = None,
                 conversation_id: str | None = None):
        # Phase C: mandatory skill loading — verify the pinned skill store
        # at startup; a hash mismatch refuses to start the loop.
        self.skill_store = get_skill_store()
        self.home = Path(home)
        self.state = ProjectState(home, project_id, conversation_id)
        self.backend = backend
        self.judge = judge or ScriptedJudge()
        self.sandbox = Sandbox(sandbox_dir or (self.state.dir / "sandbox"))
        cfg = {"max_retries": 3, "max_turns": 50, "stall_limit": 5,
               "token_budget": 200_000, "max_seconds": 3600,
               # v0.6 recursive loop: per-turn round budget and in-turn
               # stall detector (3 consecutive identical rounds).
               "max_rounds_per_turn": 25, "round_stall_limit": 3}
        cfg.update(config or {})
        self.config = cfg
        # v0.6: cooperative cancellation. One token per user turn, created
        # by run_user_turn (or the sidecar) and threaded through the round
        # driver -> tool executor -> sandbox/backend.
        self._cancel_token: CancelToken | None = None
        # v0.6: in-turn recursive-loop state. Re-initialized at the start of
        # every user turn by _run_agent_loop (and restored on approval
        # resume); never persisted — the journal is the durable record.
        self._round_transcript: list[dict] = []
        self._round_no: int = 0
        self._call_offset: int = 0
        self._round_schemas: list[dict] = []
        self._last_round_sig = None
        self._round_stall_count: int = 0
        self._stall_warned: bool = False
        self.history: list[dict] = []
        self.setup_checks = [self._check_sandbox_writable]
        # Track B (memory registry): the sidecar attaches a Registry here on
        # mission start. None in unit tests — every use is guarded.
        self.registry = None
        # Native tool application (VS Code extension builder): when the
        # extension advertises apply/terminal capability, the sidecar
        # attaches a delegation object here. None (CLI, unit tests, older
        # extensions) preserves the historical in-process sandbox behavior
        # exactly. The delegation interface is duck-typed:
        #   tool_fn(name) -> callable | None   (per-call, used for run_command)
        #   apply_batch(edits) -> [result]     (batched writes, one undo unit)
        self.delegation = None
        # Story ledger: the wall-clock start of THIS session. The 25-turn
        # review nudge treats a story as "untouched this session" when it
        # has no session-log entry at/after this timestamp.
        self._session_start_ts = time.time()
        # Turn boundaries the stories_nudge already fired on (early-return
        # turns don't advance turn_count — without this the same boundary
        # could fire twice).
        self._stories_nudged_at = set()
        self._rebuild_history()
        # Sidecar streaming protocol (spec §3): the sidecar attaches an
        # event emitter + a turn_start metadata hook per streamed turn.
        # None everywhere else (CLI, MCP, unit tests) — every streaming
        # code path checks for None, so behavior there is unchanged.
        self.sidecar_emit = None
        self.sidecar_turn_meta = None
        # The current/last turn's _SidecarStream (None when the turn is not
        # streamed). Persists after the pipeline so approval resume and
        # turn_result enrichment can reference the turn's context.
        self._turn_stream: _SidecarStream | None = None
        # v0.6 back half: user-definable hooks on loop events
        # (turn_start/end, tool_result, approval_requested/decided,
        # mission_set/complete, context_compacted). Error-isolated: a
        # failing hook never breaks the loop.
        self.hooks = HookRegistry()

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

    def maybe_compact(self, source: str = "turn") -> dict | None:
        """State-authoritative compaction (v0.6 back half).

        When the working context (contract block + history + round
        transcript) exceeds ``config["context_budget_tokens"]``, compact
        it DOWN TO the authoritative state and rebuild the working
        context from state alone — never the reverse. Returns the
        compaction report, or None when under budget (no-op).

        Approval-free by design: compaction never touches state, so
        post-compaction behavior is identical.
        """
        from compaction import maybe_compact as _maybe_compact
        return _maybe_compact(self, source=source)

    # ---------------------------------------------------------------- mission
    def set_mission(self, text: str, criteria: list) -> dict:
        snap = self.state.snapshot
        revision = (snap["mission"]["revision"] + 1) if snap["mission"] else 1
        kind = detect_mission_kind(text)
        # Phase C: mandatory skill loading — the mission kind must map to at
        # least one skill present in the verified store.
        required = skills_for_kind(kind)
        store = get_skill_store()
        missing = [n for n in required if n not in store.names()]
        if missing:
            raise SkillIntegrityError(
                f"mission kind {kind!r} requires skills {required}, "
                f"missing from store: {missing}; mission refused")
        mission = {"id": f"m-{_uid()[:8]}", "text": text,
                   "kind": kind,
                   "done_criteria": [parse_criteria(c) for c in criteria],
                   "revision": revision}
        self.state.record("mission_set", {"mission": mission})
        # v0.6 back half: mission_set hook (error-isolated).
        self.hooks.fire("mission_set",
                        {"mission_id": mission["id"],
                         "revision": mission["revision"],
                         "text": mission["text"][:200]})
        # Story ledger: a new mission arriving while stories are still
        # doing/open journals `stale_stories` — the session must surface
        # "we started this new thing, but X is still open — what's up?"
        # before proceeding. Best-effort; never blocks the mission.
        try:
            from story import check_stale_on_mission  # local: import-light
            reg = getattr(self, "registry", None)
            awino_dir = getattr(reg, "awino_dir", None) if reg is not None else None
            if awino_dir is None:
                awino_dir = getattr(self, "awino_dir", None)
            if awino_dir is not None:
                warning = check_stale_on_mission(awino_dir)
                if warning:
                    self.state.record("stale_stories",
                                      {"mission_id": mission["id"],
                                       "warning": warning})
                    mission["stale_warning"] = warning
        except Exception:
            pass
        return mission

    # ------------------------------------------------- story planning (PLAN)
    def plan_story(self, story_id: str, *, breakdown: str, surveyed: str,
                   user_guidance: str, proposal: str,
                   steps: list[dict] | None, bugatti_brief: str,
                   stances: list[str] | None = None) -> dict:
        """PLAN-phase advisory cycle for a story.

        Writes the approach with the required six-part shape (enforced
        at write time by story.plan_story): (a)-(d) the pitch — A/B/C
        options + Bugatti brief, Honda first; (e) ordered steps with
        success/failure criteria, which seed the task DAG; (f) the
        Bugatti proposal in brief form (expanded only on request —
        IRON RULE: pitched, never built unasked). Deliberately cycles
        the PLAN-affine stances — architect for the breakdown,
        researcher for existing approaches, engineer for feasibility and
        the pitch — journaling each contribution into the mission event
        stream as well as the registry. `stances` overrides the cycle;
        the user can override at any point. Returns a plain-language
        result dict — no exceptions escape.
        """
        try:
            from story import plan_story as _plan  # local: import-light
            reg = getattr(self, "registry", None)
            awino_dir = (getattr(reg, "awino_dir", None)
                         if reg is not None else None)
            if awino_dir is None:
                awino_dir = getattr(self, "awino_dir", None)
            if awino_dir is None:
                return {"status": "error",
                        "said": ("No story ledger attached to this loop — "
                                 "plan_story needs a project registry.")}
            res = _plan(awino_dir, story_id, breakdown=breakdown,
                        surveyed=surveyed, user_guidance=user_guidance,
                        proposal=proposal, steps=steps,
                        bugatti_brief=bugatti_brief, stances=stances)
            for stance in res["planned_stances"]:
                self.state.record("story_plan_stance",
                                  {"story_id": story_id, "stance": stance})
            self.state.persist_snapshot()
            return {"status": "ok", "story_id": story_id,
                    "stances": res["planned_stances"],
                    "dag_seeded": res.get("dag_seeded", 0),
                    "said": (f"Story '{res['title']}' planned through "
                             f"{len(res['planned_stances'])} stances "
                             f"({', '.join(res['planned_stances'])}): the "
                             f"six-part spine is written, each contribution "
                             f"is journaled, and "
                             f"{res.get('dag_seeded', 0)} DAG task(s) were "
                             f"seeded from the ordered steps.")}
        except (KeyError, ValueError) as e:
            return {"status": "error",
                    "said": f"Story planning refused: {e}"}
        except Exception as e:  # noqa: BLE001 — plain-language, never raise
            return {"status": "error",
                    "said": (f"Story planning failed "
                             f"({type(e).__name__}: {e}).")}

    # ------------------------------------------------- Track D: role modes
    # A role mode is a LENS: it routes skill bodies and evidence checklists
    # into the contract. It never changes offered tools or permissions —
    # the floor mode (observe/plan/build/verify/ship) owns those alone.
    def set_role_mode(self, role: str, reason: str = "",
                      source: str = "override") -> dict:
        """Activate a role lens. Validates, journals, mirrors to .awino/.

        Returns a plain-language result dict — no exceptions escape.
        """
        if role not in _modes.ROLE_IDS:
            return {"status": "error",
                    "said": (f"Unknown role '{role}'. What happened: the role "
                             f"name isn't one of the five lenses, so nothing "
                             f"changed. What it means: the current lens "
                             f"stays active. Next action: pick one of "
                             f"{', '.join(_modes.ROLE_IDS)}.")}
        self.state.record("role_mode",
                          {"role": role, "reason": reason, "source": source})
        reg = getattr(self, "registry", None)
        awino_dir = getattr(reg, "awino_dir", None) if reg is not None else None
        if awino_dir is None:
            awino_dir = getattr(self, "awino_dir", None)
        if awino_dir is not None:
            try:
                _modes.write_role_state(awino_dir, role, reason, source,
                                        self.state.snapshot.get("phase", ""))
            except Exception:
                pass  # the mirror is a convenience; the journal is truth
        self.state.persist_snapshot()
        return {"status": "ok", "role": role,
                "reason": reason, "source": source}

    def active_role(self) -> dict | None:
        """The active role lens snapshot, or None."""
        return self.state.snapshot.get("role_mode")

    def route_role(self, mission_text: str = "",
                   registry_context: str = "",
                   configured_profile: str | None = None,
                   force: bool = False) -> dict:
        """Deterministic role proposal; applies when it differs (or force).

        Re-evaluated at mission start and phase boundaries. Never raises.
        """
        try:
            s = self.state.snapshot
            current = (s.get("role_mode") or {}).get("role")
            proposal = _modes.propose_role(
                mission_text, s.get("phase", ""), registry_context,
                configured_profile=configured_profile, current_role=current)
            if force or proposal["role"] != current:
                return self.set_role_mode(proposal["role"], proposal["reason"],
                                          proposal["source"])
            return {"status": "unchanged", "role": current,
                    "reason": proposal["reason"]}
        except Exception as e:  # noqa: BLE001 — routing never breaks a turn
            return {"status": "error",
                    "said": f"role routing skipped ({type(e).__name__}): keeping current lens"}

    def _check_role_trigger(self, text: str) -> None:
        """Mid-mission triggers: secrets/security or experiments/results in
        user text re-route the role lens immediately. Never raises."""
        try:
            s = self.state.snapshot
            current = (s.get("role_mode") or {}).get("role")
            proposal = _modes.propose_role(text or "", s.get("phase", ""),
                                           current_role=current)
            if proposal["source"] == "trigger" and proposal["role"] != current:
                self.set_role_mode(proposal["role"], proposal["reason"],
                                   "trigger")
        except Exception:
            pass

    def _revision(self) -> int:
        m = self.state.snapshot["mission"]
        return m["revision"] if m else 0

    def _search_dirs(self) -> list:
        return [self.state.dir / "artifacts", self.sandbox.root]

    # -------------------------------------------------------------- user turn
    def run_user_turn(self, text: str, cancel_token: CancelToken | None = None) -> dict:
        """One user turn, plus the story-ledger turn-boundary checks.

        The nudge check runs after the turn completes: every
        STORIES_NUDGE_EVERY turns, untouched open stories journal a
        `stories_nudge` event and the reminder is appended to the reply.

        v0.6: cancel_token is the turn's cooperative cancellation token.
        None (CLI/MCP/tests) creates a fresh unset token. The sidecar
        passes the token it also hands to its stdin pump so typing
        /cancel while a turn runs stops the loop at the next checkpoint.
        """
        # v0.6 back half: turn_start hook (error-isolated; never breaks the turn).
        self.hooks.fire("turn_start", {
            "text": text,
            "turn_no": self.state.snapshot.get("turn_count", 0) + 1})
        result = self._run_user_turn_inner(text, cancel_token)
        try:
            self._stories_nudge_check(result)
        except Exception:
            pass  # the nudge is advisory; never break a turn
        # v0.6 back half: turn_end hook. fire() is error-isolated, so this
        # can never break the turn's return path.
        self.hooks.fire("turn_end", {
            "status": result.get("status") if isinstance(result, dict) else None,
            "turn_no": self.state.snapshot.get("turn_count", 0)})
        return result

    def _stories_nudge_check(self, result: dict) -> None:
        """Periodic review nudge: every STORIES_NUDGE_EVERY user turns, if
        open (non-blocked) stories have no session-log entry since this
        session began, journal `stories_nudge` and append the reminder to
        the turn's reply. Fires exactly once per boundary."""
        from story import STORIES_NUDGE_EVERY, StoryStore  # local: import-light
        s = self.state.snapshot
        turn_count = s.get("turn_count", 0)
        if (turn_count <= 0 or turn_count % STORIES_NUDGE_EVERY != 0
                or turn_count in self._stories_nudged_at):
            return
        reg = getattr(self, "registry", None)
        awino_dir = (getattr(reg, "awino_dir", None)
                     if reg is not None else None)
        if awino_dir is None:
            awino_dir = getattr(self, "awino_dir", None)
        if awino_dir is None:
            return
        store = StoryStore(awino_dir)
        if not store.exists:
            return
        untouched = []
        for st in store.open_stories():
            if st.get("status") not in ("open", "doing"):
                continue
            sessions = st.get("sessions") or []
            worked = any((e.get("started_ts") or e.get("ts") or 0)
                         >= self._session_start_ts for e in sessions)
            if not worked:
                untouched.append(st)
        if not untouched:
            return
        names = ", ".join(f"'{st['title']}' ({st['id']})"
                          for st in untouched)
        self.state.record("stories_nudge",
                          {"turn": turn_count,
                           "untouched": [st["id"] for st in untouched]})
        self._stories_nudged_at.add(turn_count)
        nudge = (f"Story review nudge ({turn_count} turns in): {names} "
                 f"{'has' if len(untouched) == 1 else 'have'} not been "
                 f"touched this session. Still the right stories to have "
                 f"open, or should we work on / close "
                 f"{'it' if len(untouched) == 1 else 'them'}?")
        if isinstance(result, dict) and "said" in result:
            result["said"] = f"{result['said']}\n\n{nudge}"

    def _run_user_turn_inner(self, text: str,
                               cancel_token: CancelToken | None = None) -> dict:
        s = self.state.snapshot
        if s["done"]:
            return {"status": "closed", "said": "Mission already complete (SHIP)."}
        if s["terminal"]:
            return {"status": "closed",
                    "said": f"Terminal state: {s['terminal_reason']}. Start a new project to continue."}
        kind, payload = classify_user_input(text)
        self.state.record("user_message", {"kind": kind, "text": text})
        self._hist("user", text)
        # Track D: mid-mission triggers — secrets/security or
        # experiments/results re-route the role lens before the turn runs.
        self._check_role_trigger(text)
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
            resolved = list(s["open_questions"])
            self.state.record("questions_resolved",
                              {"resolved": resolved, "answer": text})
            # Phase C: resolved Q/A is a learning.
            self.state.record("learning_recorded",
                              {"kind": "qa",
                               "text": f"Q: {' | '.join(resolved)[:200]} -- "
                                       f"A: {text[:300]}"})
        return self._run_turn(user_text=text, input_kind=kind,
                              cancel_token=cancel_token)

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
    def _run_turn(self, user_text: str = "", input_kind: str = "info",
                  cancel_token: CancelToken | None = None) -> dict:
        s = self.state.snapshot
        cfg = self.config
        # v0.6: the turn owns one cancellation token; None creates a fresh
        # unset one so every code path below can poll unconditionally.
        # (Explicit None check: an unset CancelToken is falsy, so `or`
        # would discard a live token handed in by the sidecar.)
        self._cancel_token = (cancel_token if cancel_token is not None
                              else CancelToken())
        if s["turn_count"] >= cfg["max_turns"]:
            self.state.record("budget_exhausted", {"reason": f"max_turns={cfg['max_turns']}"})
            self.state.persist_snapshot()
            return {"status": "budget_exhausted",
                    "said": f"Turn budget exhausted ({cfg['max_turns']}). Terminal."}
        # Phase B: wall-clock budget. Never auto-increased; exhaustion is terminal.
        start_ts = s.get("mission_start_ts")
        if start_ts and (time.time() - start_ts) >= cfg["max_seconds"]:
            self.state.record("budget_exhausted",
                              {"reason": f"max_seconds={cfg['max_seconds']}"})
            self.state.persist_snapshot()
            return {"status": "budget_exhausted",
                    "said": f"Time budget exhausted ({cfg['max_seconds']}s). Terminal."}

        setup_errs = self.check_setup()
        if setup_errs:
            self.state.record("setup_blocked", {"errors": setup_errs})
            return {"status": "setup_blocked",
                    "said": "Setup blocked: " + "; ".join(setup_errs)}

        # v0.6 back half: state-authoritative compaction. When the working
        # context exceeds budget, compact it down to state and rebuild
        # from state alone. Approval-free: state is untouched.
        self.maybe_compact("turn")
        return self._pipeline(user_text, input_kind)

    def _begin_stream(self, turn_id: str) -> "_SidecarStream | None":
        """Create the per-turn sidecar streaming context, or None when the
        sidecar has not attached an emitter (CLI/MCP/tests: zero behavior
        change — the caller skips every streaming step)."""
        if self.sidecar_emit is None:
            return None
        return _SidecarStream(self.sidecar_emit, turn_id,
                              turn_start_extra=self.sidecar_turn_meta)

    def _stream_for(self, turn_id: str) -> "_SidecarStream | None":
        """The turn's streaming context, or None when the turn is not
        streamed. Guards against a stale context from an earlier turn."""
        s = self._turn_stream
        return s if (s is not None and s.turn_id == turn_id) else None

    @staticmethod
    def _turn_of_call(call_id: str) -> str:
        """call_ids are f"{turn_id}.{index}" — recover the turn."""
        return (call_id or "").rsplit(".", 1)[0]

    def _pipeline(self, user_text: str, input_kind: str) -> dict:
        """Stages 0-4b of the per-turn pipeline."""
        cfg = self.config
        s = self.state.snapshot
        turn_no = s["turn_count"] + 1
        turn_id = f"t{turn_no}"

        # Sidecar streaming protocol (spec §3): per-turn streaming context,
        # or None when the sidecar did not attach an emitter. None means
        # zero behavior change anywhere below.
        stream = self._begin_stream(turn_id)
        if stream is not None:
            self._turn_stream = stream

        # ---- Stage 0: per-turn contract loop (compile -> check -> refuse) ----
        # The contract is compiled from code-owned state BEFORE the backend
        # acts. A broken contract refuses the turn here: the backend is never
        # called and no tool executes.
        tcontract = compile_turn_contract(self.state)
        breaks = check_pre_turn(self.state, tcontract)
        if breaks:
            if stream is not None:
                # The turn never reaches the backend, but the UI still gets
                # a card: turn_start first (event order invariant), then the
                # failed contract check.
                stream.turn_start(phase=s["phase"], mode_id=s["mode"])
                stream.harness_check(
                    "contract", "fail",
                    "; ".join(f"{b.reason}: {b.detail}" for b in breaks))
            return self._refuse_turn(breaks, stage="pre_turn")
        # The stage-0 check passed; its harness_check is buffered and
        # emitted right after turn_start (stage 4) to keep the event order
        # turn_start -> checks -> deltas.
        contract_check = (
            "contract", "pass",
            f"phase {tcontract.get('phase')} · mode {tcontract.get('mode')}")

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

        # ---- Stage 4: the recursive agent loop (v0.6) ----
        # model -> validate -> pre-execute -> intercept -> execute ->
        # observe -> model ... until a validated round emits no tool calls
        # (normal exit), completion verifies (mission done), or a budget /
        # stall / cancel halt fires. Contract enforcement runs before every
        # model round and every tool execution — never once per turn.
        if stream is not None:
            # turn_start precedes the first backend call, carrying the
            # routed mode; the stream object is the backend's stream_cb.
            # (Emitted once per turn, not per round.)
            stream.turn_start(phase=s["phase"], mode_id=routing["mode"])
            stream.harness_check(*contract_check)
        return self._run_agent_loop(turn_id, turn_no, routing, tcontract,
                                    offered, stream, user_text,
                                    input_kind=input_kind)

    # ------------------------------------------------- v0.6 recursive loop
    # The engine: model -> validate -> pre-execute -> intercept harness ->
    # execute -> observe -> model -> ... The v0.5.2 control plane (contract
    # compilation, permissions, approvals, judges, evidence, journal) is
    # unchanged; only the driver changed from one-generation-per-turn to
    # one-generation-per-ROUND, with tool results feeding the next round.
    #
    # Per-turn loop state (owned by _run_agent_loop, reset on every turn):
    #   _round_transcript  in-turn assistant/tool messages (assistant-side
    #                      only; the contract block carries persistent state)
    #   _call_offset       next call index for turn-scoped call ids
    #   _last_round_sig    previous round's (phase, calls, last-result) sig
    #   _round_stall_count consecutive identical-signature rounds
    #   _round_no          current round number (diagnostics / streaming)
    #   _round_schemas     canonical tool schemas offered this turn
    # self._cancel_token is the turn's CancelToken (sidecar-owned, set from
    # the sidecar's stdin pump thread); every layer polls it at explicit
    # checkpoints. A Loop instance drives one turn at a time, so sharing
    # the token as an attribute is safe.

    def _round_history(self) -> list[dict]:
        """Model context for one round: the persistent tail (up to 6
        entries) plus the in-turn transcript. Tool output travels here as
        data — never inside the contract block."""
        tail = self.history[-6:] if len(self.history) > 6 else list(self.history)
        return tail + self._round_transcript

    def _compile_round_contract(self, turn_id: str, turn_no: int,
                                round_no: int) -> tuple[str, str]:
        """Re-compile the contract block for one round: a fresh
        `loop: turn.round` header, current persisted tasks, and the in-turn
        ## ROUND history so the model continues from what the turn already
        did. Returns (block, expected_header)."""
        summaries = [e.get("summary", "") for e in self._round_transcript
                     if e.get("role") == "tool" and e.get("summary")]
        block = compile_contract(
            self.state, turn_no=turn_no, round_no=round_no,
            round_context={"max_rounds": self.config["max_rounds_per_turn"],
                           "results": summaries})
        return block, block.split("\n", 1)[0]

    def _validate_round(self, turn_id: str, turn_no: int, round_no: int,
                        routing: dict, user_text: str, offered: list[str],
                        stream, contract_block: str, expected_header: str,
                        feedback: str | None = None):
        """One model generation plus the full v0.5.2 validation chain
        (schema -> header/semantics -> drift -> judge -> stance rubric ->
        typed coercion). The chain is unchanged; only its caller changed —
        once per turn then, once per round now.

        Returns ("valid", typed_turn_as_dict) or ("escalated", outcome).
        Cancelled propagates to the caller (it becomes a cancelled turn,
        never a model error). A provider native-call NormalizationError is
        harness-rejection feedback, never a crash.
        """
        cfg = self.config
        turn = None
        errs: list[str] = []
        # v0.6: only pass the per-round kwargs the backend accepts —
        # older/test backends keep their generate(contract, history, …)
        # shape and must not break.
        _extra: dict = {}
        try:
            _params = inspect.signature(self.backend.generate).parameters
            _kw = any(p.kind == inspect.Parameter.VAR_KEYWORD
                      for p in _params.values())
            if _kw or "tools" in _params:
                _extra["tools"] = self._round_schemas
            if _kw or "cancel" in _params:
                _extra["cancel"] = self._cancel_token
        except (TypeError, ValueError):
            _extra = {"tools": self._round_schemas,
                      "cancel": self._cancel_token}
        for attempt in range(cfg["max_retries"] + 1):
            tok = self._cancel_token
            if tok is not None and tok.is_set():
                raise Cancelled("cancelled before model call")
            try:
                if stream is not None:
                    raw = self.backend.generate(
                        contract_block, self._round_history(),
                        feedback=feedback, stream_cb=stream, **_extra)
                else:
                    raw = self.backend.generate(
                        contract_block, self._round_history(),
                        feedback=feedback, **_extra)
                errs = []
            except Cancelled:
                raise
            except NormalizationError as ex:
                # A provider's native tool payload that cannot be
                # normalized is harness-rejection feedback, never a crash.
                raw = None
                errs = [f"native tool-call normalization failed: {ex}"]
            if not errs:
                # Track C (egress audit): journal network I/O the backend did.
                self._record_egress(turn_id)
                self._charge_tokens(contract_block, raw)
                errs = validate_schema(raw)
                if not errs and ("mode_hint" in raw or "phase_hint" in raw):
                    self.state.record("turn_hint_ignored",
                                      {"turn_id": turn_id,
                                       "hints": {k: raw[k]
                                                 for k in ("mode_hint", "phase_hint")
                                                 if k in raw}})
                if not errs:
                    # Native tool-call merge errors reported by the backend.
                    errs = list(raw.pop("_native_tool_errors", []) or [])
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
                    if stream is not None:
                        for name, ok, reason in _judge_check_rows(self.judge, jv):
                            stream.harness_check(f"judge:{name}",
                                                 "pass" if ok else "fail",
                                                 reason[:200])
                if not errs and routing["chain"] != ["advisor"]:
                    ok, failures = evaluate_chain(routing["chain"], raw, user_text)
                    if ok:
                        self.state.record("stance_rubric_passed",
                                          {"turn_id": turn_id,
                                           "stance": "->".join(routing["chain"])})
                        if "feynman" in routing["chain"]:
                            self.state.record("learning_recorded",
                                              {"kind": "feynman",
                                               "text": raw.get("progress_delta", "")[:500]})
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
                self.state.record("turn_validated",
                                  {"turn_id": turn_id, "attempt": attempt,
                                   "round": round_no})
                if stream is not None:
                    stream.harness_check("validation", "pass",
                                         f"attempt {attempt}")
                break
            self.state.record("turn_rejected",
                              {"turn_id": turn_id, "attempt": attempt,
                               "round": round_no, "errors": errs})
            doom = self._check_doom_loop(
                "turn_rejected", self._failure_signature("turn_rejected", errs))
            if attempt >= cfg["max_retries"]:
                self.state.record("turn_escalated", {"turn_id": turn_id,
                                                     "round": round_no,
                                                     "errors": errs})
                self.state.persist_snapshot()
                return ("escalated",
                        {"status": "escalated",
                         "said": "Turn escalated to operator: " + "; ".join(errs),
                         "errors": errs})
            feedback = (f"HARNESS REJECTION (attempt {attempt + 1}): "
                        f"{'; '.join(errs)}. Fix and resubmit a valid TurnContract.")
            if doom:
                feedback = self._doom_loop_feedback() + " " + feedback

        if turn is None:
            # Only reachable when every attempt died in native-call
            # normalization. Bounded by max_retries; escalate honestly.
            self.state.record("turn_escalated",
                              {"turn_id": turn_id, "round": round_no,
                               "errors": errs})
            self.state.persist_snapshot()
            return ("escalated",
                    {"status": "escalated",
                     "said": "Turn escalated to operator: " + "; ".join(errs),
                     "errors": errs})
        try:
            typed = coerce_turn_contract(turn)
        except ContractTypeError as e:
            self.state.record("turn_rejected",
                              {"turn_id": turn_id, "attempt": "coerce",
                               "round": round_no, "errors": [str(e)]})
            self.state.persist_snapshot()
            return ("escalated",
                    {"status": "escalated",
                     "said": "Turn failed typed coercion: " + str(e),
                     "errors": [str(e)]})
        return ("valid", typed.as_dict())

    # ------------------------------------------------- harness interception
    def _execute_harness_tool(self, name: str, args: dict,
                              turn_id: str, round_no: int) -> dict:
        """Run a harness tool in-process. These never touch the sandbox or
        the delegation layer: task_add/task_update mutate persisted task
        state; attempt_completion runs the evidence-gated verifier."""
        if name == "task_add":
            return self._harness_task_add(args, turn_id, round_no)
        if name == "task_update":
            return self._harness_task_update(args, turn_id, round_no)
        if name == "attempt_completion":
            return self._attempt_completion(args, turn_id, round_no)
        return {"error": f"unknown harness tool: {name}",
                "error_code": "UNKNOWN_HARNESS_TOOL"}

    def _journal_harness_result(self, turn_id: str, index: int, name: str,
                                args: dict, inner: dict) -> dict:
        """Journal a harness tool_result (same shape as executor results)
        and return the {"tool", "result"} envelope the loop carries."""
        call_id = f"{turn_id}.{self._call_offset + index}"
        call = {"name": name, "args": args}
        self.state.record("tool_result",
                          {"call_id": call_id, "tool": name, "args": args,
                           "idem_key": self._idem(call), "result": inner,
                           "harness": True,
                           "mission_rev": self.state.snapshot["mission_revision"]})
        # v0.6 back half: tool_result hook (error-isolated; harness tools
        # journal tool_result entries too).
        self.hooks.fire("tool_result",
                        {"call_id": call_id, "tool": name,
                         "ok": "error" not in inner, "harness": True})
        return {"tool": name, "result": inner}

    # ------------------------------------------------- harness tool bodies
    def _harness_task_add(self, args: dict, turn_id: str,
                          round_no: int) -> dict:
        title = (args.get("title") or "").strip()
        if not title:
            return {"error": "task_add requires a non-empty title",
                    "error_code": "BAD_TITLE"}
        existing = next((t for t in self.state.snapshot["tasks"]
                         if t["title"] == title), None)
        if existing is not None:
            # Duplicate titles return the existing id — never a duplicate.
            return {"id": existing["id"], "title": title,
                    "status": existing["status"], "duplicate": True}
        self.state.record("task_added",
                          {"turn_id": turn_id, "round": round_no,
                           "title": title})
        task = next(t for t in self.state.snapshot["tasks"]
                    if t["title"] == title)
        return {"id": task["id"], "title": title, "status": task["status"]}

    def _harness_task_update(self, args: dict, turn_id: str,
                             round_no: int) -> dict:
        tid = args.get("id")
        status = args.get("status")
        notes = args.get("notes")
        task = next((t for t in self.state.snapshot["tasks"]
                     if t["id"] == tid), None)
        if task is None:
            return {"error": f"unknown task id: {tid}",
                    "error_code": "UNKNOWN_TASK"}
        if status not in ("todo", "doing", "done"):
            return {"error": f"invalid status: {status!r} (todo|doing|done)",
                    "error_code": "BAD_STATUS"}
        if status == "doing":
            other = next((t for t in self.state.snapshot["tasks"]
                          if t["status"] == "doing" and t["id"] != tid), None)
            if other is not None:
                return {"error": (f"task {other['id']} is already doing; mark "
                                  "exactly one task doing at a time"),
                        "error_code": "DOING_CONFLICT"}
        self.state.record("task_updated",
                          {"turn_id": turn_id, "round": round_no,
                           "id": tid, "status": status, "notes": notes})
        return {"id": tid, "status": status}

    def _attempt_completion(self, args: dict, turn_id: str,
                            round_no: int) -> dict:
        """Evidence-gated completion. Every done criterion is checked in
        code via verify_done_criteria; unverified criteria come back as a
        missing-evidence list and the loop continues. On pass, journals a
        harness verify_passed and returns completed=True — the driver then
        finalizes with mission_done. Honored only on REVIEW; elsewhere the
        phase floor is reported as missing evidence."""
        summary = (args.get("summary") or "").strip()
        s = self.state.snapshot
        gaps: list[str] = []
        if s.get("phase") != "REVIEW":
            gaps.append(f"phase is {s.get('phase')}, not REVIEW "
                        "(VERIFY -> REVIEW needs the verifier's pass first)")
        _ok, crit_gaps = verify_done_criteria(
            s, self.state.events, self._search_dirs())
        gaps.extend(crit_gaps)
        if gaps:
            self.state.record("completion_rejected",
                              {"turn_id": turn_id, "round": round_no,
                               "missing_evidence": gaps})
            return {"completed": False, "missing_evidence": gaps,
                    "error": "completion rejected: missing evidence",
                    "error_code": "COMPLETION_WITHOUT_EVIDENCE"}
        self.state.record("verify_passed",
                          {"worker_id": "harness",
                           "verdict": "pass",
                           "turn_id": turn_id, "round": round_no,
                           "via": "attempt_completion"})
        self.state.record("completion_verified",
                          {"turn_id": turn_id, "round": round_no,
                           "summary": summary[:500]})
        return {"completed": True, "evidence": {}, "summary": summary[:500],
                "via": "attempt_completion"}

    def _harness_set_mission(self, **args) -> dict:
        """Model-callable mission setter (interview convergence; 0.5.3 hotfix port).

        Turn-schema args are {str: str}, so `criteria` arrives as one
        string; it is split on newlines/semicolons into the criteria list.
        Tool errors are data, never exceptions: the model sees the error
        result and can repair or report it. A worker's mission is fixed by
        its parent, so workers are refused.
        """
        if self.state.snapshot.get("worker_id"):
            return {"error": "set_mission refused: a worker's mission is "
                             "fixed by its parent"}
        text = args.get("text", "")
        raw_criteria = args.get("criteria", "")
        if not isinstance(text, str) or not text.strip():
            return {"error": "set_mission needs a non-empty 'text'"}
        if not isinstance(raw_criteria, str):
            return {"error": "set_mission 'criteria' must be a string "
                             "(semicolon- or newline-separated)"}
        criteria = [c.strip() for c in re.split(r"[;\n]+", raw_criteria)
                    if c.strip()]
        if not criteria:
            return {"error": "set_mission needs at least one done criterion "
                             "in 'criteria'"}
        try:
            mission = self.set_mission(text.strip(), criteria)
        except Exception as ex:  # noqa: BLE001 - e.g. SkillIntegrityError
            return {"error": f"{type(ex).__name__}: {ex}"}
        return {"ok": True,
                "mission": {"id": mission["id"], "text": mission["text"],
                            "kind": mission["kind"],
                            "revision": mission["revision"]},
                "said": f"Mission set: {mission['text'][:160]}"}

    def _intercept_harness_calls(self, turn_id: str, turn_no: int,
                                 round_no: int, calls: list[dict]):
        """Split harness calls out of a validated round.

        task_add/task_update run immediately (bookkeeping — never
        consequential, never approval-gated). attempt_completion is
        DEFERRED: it verifies against the round's tool results, so it runs
        after the round's immediate calls execute. (If the round pauses for
        approval, the deferred claim is dropped — the resumed round's model
        must re-claim after observing the approved effects.)

        Returns (h_results, harness_idx, deferred) where h_results maps
        call index -> result envelope, harness_idx is the set of intercepted
        indices, and deferred is (index, call) for attempt_completion or
        None.
        """
        h_results: dict[int, dict] = {}
        harness_idx: set[int] = set()
        deferred = None
        for i, c in enumerate(calls):
            name = c.get("name")
            if name not in HARNESS_TOOLS:
                continue
            harness_idx.add(i)
            if name == "attempt_completion":
                deferred = (i, c)
                continue
            inner = self._execute_harness_tool(name, c.get("args", {}),
                                               turn_id, round_no)
            h_results[i] = self._journal_harness_result(
                turn_id, i, name, c.get("args", {}), inner)
        return h_results, harness_idx, deferred

    def _run_deferred_completion(self, turn_id: str, round_no: int,
                                 deferred) -> tuple[dict, dict]:
        """Run a deferred attempt_completion and journal it. Returns
        (envelope, inner_result)."""
        i, c = deferred
        inner = self._execute_harness_tool("attempt_completion",
                                           c.get("args", {}), turn_id, round_no)
        return (self._journal_harness_result(turn_id, i, "attempt_completion",
                                             c.get("args", {}), inner),
                inner)

    # ------------------------------------------------- in-turn transcript
    def _record_round_assistant(self, turn: dict) -> None:
        self._round_transcript.append({
            "role": "assistant",
            "text": json.dumps({
                "objective": turn.get("objective"),
                "plan": turn.get("plan"),
                "tool_calls": turn.get("tool_calls"),
                "progress_delta": turn.get("progress_delta"),
            }, default=str)[:2000],
        })

    @staticmethod
    def _result_summary(env) -> str:
        """One-line summary of a {"tool", "result"} envelope for the
        ## ROUND history and the stall detector."""
        if not isinstance(env, dict):
            return str(env)[:200]
        name = env.get("tool", "?")
        res = env.get("result", env)
        if not isinstance(res, dict):
            return str(res)[:200]
        if res.get("cancelled"):
            return "cancelled before dispatch"
        if res.get("missing_evidence"):
            return ("completion rejected — missing evidence: "
                    + "; ".join(res["missing_evidence"])[:300])
        if res.get("error"):
            return f"error: {str(res['error'])[:200]}"
        if res.get("completed"):
            return "completion VERIFIED by the harness"
        if name == "run_command":
            out = str(res.get("stdout", ""))[-200:]
            return f"exit={res.get('exit_code')} {out}"
        if name in ("write_file", "patch_file"):
            return f"ok ({res.get('path', '')})"
        if name == "read_file":
            return f"{len(str(res.get('content', '')))} chars read"
        if name == "task_add":
            return f"task {res.get('id')}: {res.get('title', '')}"
        if name == "task_update":
            return f"task {res.get('id')} -> {res.get('status')}"
        return json.dumps(res, default=str)[:200]

    def _record_round_tool(self, round_pairs: list[tuple[int, dict]],
                           calls: list[dict], note: str | None = None) -> None:
        """round_pairs: [(call index, result envelope)] in call order."""
        parts = []
        for i, env in round_pairs:
            name = calls[i]["name"] if 0 <= i < len(calls) else env.get("tool", "?")
            parts.append(f"{name} -> {self._result_summary(env)}")
        if note:
            parts.append(note)
        text = "\n".join(parts) if parts else "(no tool calls)"
        self._round_transcript.append({"role": "tool", "text": text[:4000],
                                       "summary": text[:500]})

    def _round_signature(self, turn: dict, results_all: list[dict]):
        """Stall detector: identical (phase, tool calls, last result
        summary) three rounds running means the model is looping."""
        calls = tuple(
            (c.get("name"),
             tuple(sorted((k, str(v)) for k, v in c.get("args", {}).items())))
            for c in turn.get("tool_calls", []))
        last = self._result_summary(results_all[-1]) if results_all else ""
        return (self.state.snapshot.get("phase"), calls, last)

    # ------------------------------------------------- elevator (per round)
    def _check_elevator_gates(self, turn_id: str) -> None:
        """Elevator exit gates as code checks on the transitions. Runs after
        every round's tool results (and once more at finalize, which is
        idempotent): BUILD -> VERIFY on write effects; VERIFY -> REVIEW on
        exit 0 plus the journaled verifier pass."""
        s = self.state.snapshot
        if s["phase"] == "BUILD" and self._has_write_effects():
            # BUILD exit gate: diff produced -> VERIFY. Done criteria are
            # checked at the done claim, not here.
            self.request_phase("VERIFY", reason="write effects produced")
            s = self.state.snapshot
        if s["phase"] == "VERIFY" and self._has_exit_zero():
            # Track G: exit 0 alone does NOT unlock REVIEW — only the
            # verifier worker's journaled pass verdict does. v0.6: the
            # harness auto-spawns the verifier (existing machinery) instead
            # of waiting for an operator to drive it.
            if s.get("verify_pass"):
                self.request_phase("REVIEW",
                                   reason="verify exit 0 + verifier pass")
            elif self._auto_verify():
                s = self.state.snapshot
                if s.get("verify_pass"):
                    self.request_phase("REVIEW",
                                       reason="verify exit 0 + verifier pass")
            s = self.state.snapshot

    def _auto_verify(self) -> bool:
        """Track G auto-spawn: run the verifier worker without operator
        shepherding. Returns True when a pass verdict was journaled.

        Pre-check: every non-manual done criterion must already hold
        against the journal (artifacts on disk, events journaled) —
        otherwise the gate waits. The worker then independently
        re-verifies the artifact criteria plus the recipe result; a pass
        journals verify_passed (REVIEW unlocks), findings route back to
        BUILD with verifier-filed tasks (existing complete_verification
        behavior). Manual criteria stay operator-only at the done claim.
        """
        from contract import verify_done_criteria
        from verify import criterion_text
        s = self.state.snapshot
        # v0.6 fix: the pre-check must search the REAL sandbox (which may be
        # injected via sandbox_dir), not the default state.dir/"sandbox".
        # The hardcoded default stalled VERIFY -> REVIEW for any loop with an
        # injected sandbox: artifacts were "missing" so the gate waited forever.
        search_dirs = self._search_dirs()
        _, gaps = verify_done_criteria(s, self.state.events, search_dirs,
                                       manual_ok=False)
        blocking = [g for g in gaps if not g.startswith("manual:")]
        if blocking:
            self.state.record("verify_gate_waiting",
                              {"reason": ("exit 0 observed but done criteria "
                                          "unmet"),
                               "gaps": blocking})
            self.state.persist_snapshot()
            return False
        mission = s.get("mission") or {}
        artifact_criteria = [c for c in mission.get("done_criteria", [])
                             if isinstance(c, dict)
                             and c.get("kind") == "artifact_exists"]
        if not artifact_criteria:
            # Nothing file-based for the worker to independently re-check;
            # the journal pre-check above is the verification.
            self.state.record("verify_passed",
                              {"worker_id": None,
                               "verdict": "pass",
                               "note": ("auto-verify: no artifact criteria; "
                                        "exit 0 + journal criteria hold")})
            self.state.persist_snapshot()
            return True
        b = self.begin_verification()
        if b["status"] != "ok":
            # A verifier is already pending — wait for it.
            return False
        wid = b["worker_id"]
        evidence_links = {criterion_text(c): c.get("path", "")
                          for c in artifact_criteria}
        rev = s["mission_revision"]
        recipe = None
        for e in reversed(self.state.events):
            d = e.get("data", {})
            if (e["type"] == "tool_result"
                    and d.get("tool") == "run_command"
                    and d.get("mission_rev") == rev):
                res = d.get("result", {})
                recipe = {"runner": "run_command",
                          "recipe": res.get("cmd", "command"),
                          "exit_code": res.get("exit_code")}
                break
        self.run_verifier_turn(
            wid,
            {"evidence_links": evidence_links,
             "recipe_result": recipe,
             # v0.6 fix: the verifier checks artifacts on disk under
             # project_root — it must be the real (possibly injected)
             # sandbox, not the default state.dir/"sandbox".
             "project_root": str(self.sandbox.root),
             "criteria": artifact_criteria})
        res = self.complete_verification(wid)
        return bool(res.get("passed"))

    # ------------------------------------------------- halts
    def _halt_turn(self, reason: str, turn_id: str, turn_no: int,
                   round_no: int) -> dict:
        """Honest terminal halt: round budget, in-turn stall, or operator
        cancellation. Journals the halt, persists, and returns a terminal
        outcome — never a silent stop."""
        cfg = self.config
        if reason == "cancelled":
            tok = self._cancel_token
            tok_reason = tok.reason if tok is not None else None
            # Honest rendering: did the cancel arrive after tool effects
            # were already journaled? Compare the token's set timestamp
            # with journaled (non-reused, non-harness) tool_result times.
            late_effects = 0
            tok_ts = tok.set_ts if tok is not None else None
            if tok_ts is not None:
                for e in self.state.events:
                    if (e["type"] == "tool_result"
                            and not e["data"].get("reused")
                            and not e["data"].get("harness")
                            and (e.get("ts") or 0) > tok_ts):
                        late_effects += 1
            self.state.record("turn_cancelled",
                              {"turn_id": turn_id, "round": round_no,
                               "reason": tok_reason,
                               "late_effects": late_effects})
            self.state.persist_snapshot()
            said = f"Turn cancelled by the operator at round {round_no}."
            if late_effects:
                said += (f" The cancel arrived after {late_effects} tool "
                         f"effect(s) were already journaled — those stand "
                         f"(too-late-with-effects); nothing further ran.")
            else:
                said += " No tool effects ran after the cancel."
            return {"status": "cancelled", "said": said,
                    "late_effects": late_effects}
        if reason == "stall":
            # state.py's `stalled` handler sets awaiting_operator — the
            # halt is operator-visible on replay, not just in the log.
            self.state.record("stalled",
                              {"turn_id": turn_id, "round": round_no,
                               "reason": "round_stall",
                               "detail": (f"{cfg['round_stall_limit']} consecutive "
                                          "rounds with identical phase, tool "
                                          "calls, and last result")})
            self.state.persist_snapshot()
            return {"status": "stalled",
                    "said": (f"Turn halted at round {round_no}: the agent "
                             f"repeated the same round {cfg['round_stall_limit']} "
                             f"times with no new information. Escalated to the "
                             f"operator.")}
        # round_budget
        # state.py's `round_budget_exhausted` handler sets awaiting_operator
        # with reason + round — replay restores the operator-wait state.
        self.state.record("round_budget_exhausted",
                          {"turn_id": turn_id, "round": round_no,
                           "reason": "round_budget",
                           "detail": f"max_rounds_per_turn={cfg['max_rounds_per_turn']}"})
        self.state.persist_snapshot()
        return {"status": "budget_exhausted",
                "said": (f"Turn halted at round {round_no}: round budget "
                         f"exhausted (max_rounds_per_turn="
                         f"{cfg['max_rounds_per_turn']}). The mission is "
                         f"unchanged; re-run to continue from journaled state.")}

    # ------------------------------------------------- the driver
    def _run_agent_loop(self, turn_id: str, turn_no: int, routing: dict,
                        tcontract: dict, offered: list[str], stream,
                        user_text: str = "", input_kind: str = "info",
                        resume: dict | None = None) -> dict:
        """The recursive agent loop (v0.6 engine).

        Fresh turn: round_no starts at 0 with an empty transcript. Resume
        (from _drain_pending after an approval round-trip): continues at
        resume["round_no"] + 1 with the persisted transcript, call offset,
        and stall state — the model sees the grant/deny note, not a blank
        slate.

        Each round: elevator gates -> re-route (mode/stance/offered follow
        the current phase) -> recompile the contract (fresh `loop: turn.round`
        header) -> one model generation -> full validation chain (retry on
        harness rejection) -> legacy done_claim rerouted to
        attempt_completion -> in-turn stall check -> per-round contract
        pre-execute check (hard breaks are round feedback; the missing-
        approval break routes to the approval gate) -> harness interception
        (task tools now, attempt_completion after immediate calls) ->
        execute -> observe -> next round.

        Exits: a validated round with no tool calls (normal); a verified
        attempt_completion (mission done); round budget, in-turn stall, or
        operator cancellation (honest halts); validation escalation.
        """
        cfg = self.config
        max_rounds = cfg["max_rounds_per_turn"]
        if resume is not None:
            round_no = resume["round_no"] + 1
            self._round_transcript = list(resume.get("transcript", []))
            self._call_offset = resume.get("call_offset", 0)
            self._last_round_sig = resume.get("last_round_sig")
            self._round_stall_count = resume.get("round_stall_count", 0)
            self._stall_warned = resume.get("stall_warned", False)
            results_all = list(resume.get("results_so_far", []))
            round_feedback = resume.get("round_feedback")
            user_text = resume.get("user_text", user_text)
            input_kind = resume.get("input_kind", input_kind)
            resume_note = resume.get("resume_note")
            if resume_note:
                self._round_transcript.append(
                    {"role": "tool", "text": resume_note[:2000],
                     "summary": resume_note[:500]})
        else:
            round_no = 0
            self._round_transcript = []
            self._call_offset = 0
            self._last_round_sig = None
            self._round_stall_count = 0
            self._stall_warned = False
            results_all = []
            round_feedback = None
        self._round_no = round_no
        self._round_schemas = schemas_for(offered)
        while True:
            if round_no >= max_rounds:
                return self._halt_turn("round_budget", turn_id, turn_no,
                                       round_no)
            tok = self._cancel_token
            if tok is not None and tok.is_set():
                return self._halt_turn("cancelled", turn_id, turn_no, round_no)
            # v0.6 back half: per-round compaction check — the round
            # transcript grows within a turn; compact down to state when
            # over budget. Approval-free: state is untouched.
            self.maybe_compact("round")
            self._round_no = round_no
            # Per-round control-plane refresh: the elevator may have moved
            # the phase (after the last round's tool results, or after an
            # approval drain's granted writes). Re-route so the mode, stance
            # chain, offered tools, and typed contract track the CURRENT
            # phase — a stale routing would validate the round against the
            # wrong mode's tool set. _sensor_route only journals on actual
            # change, so steady-state rounds are journal-quiet.
            #
            # v0.6 fix: only the turn's first round honors the user's
            # utterance (intent patterns may override the floor default, as
            # in v0.5). Later rounds re-route against the CURRENT phase's
            # floor: re-matching the original text would let a stale intent
            # ("fix it now" -> build mode) pin the mode/stance to the turn's
            # first phase forever, contradicting the elevator (e.g.
            # run_command refused after BUILD -> VERIFY because it is not
            # offered in build mode). Neutral text+kind also keeps
            # new_objective from re-firing after a mid-turn set_mission.
            self._check_elevator_gates(turn_id)
            if round_no == 0:
                routing = self._sensor_route(user_text, input_kind)
            else:
                routing = self._sensor_route("", "info")
            offered = self._permission_gate()
            tcontract = compile_turn_contract(self.state)
            self._round_schemas = schemas_for(offered)
            contract_block, expected_header = self._compile_round_contract(
                turn_id, turn_no, round_no)
            try:
                status, payload = self._validate_round(
                    turn_id, turn_no, round_no, routing, user_text, offered,
                    stream, contract_block, expected_header,
                    feedback=round_feedback)
            except Cancelled:
                return self._halt_turn("cancelled", turn_id, turn_no, round_no)
            if status == "escalated":
                return payload
            turn = payload
            calls = turn.get("tool_calls", [])
            # Legacy done_claim backstop: reroute to a harness
            # attempt_completion call BEFORE the pre-execute check (which
            # would otherwise hard-break COMPLETION_WITHOUT_EVIDENCE).
            # validate_semantics already enforced the forgery + REVIEW-floor
            # rules, so this only reroutes verified claims.
            if turn.get("done_claim") and not any(
                    c.get("name") == "attempt_completion" for c in calls):
                calls = calls + [{"name": "attempt_completion",
                                  "args": {"summary": (turn.get("progress_delta")
                                                       or "")[:500]}}]
                turn["done_claim"] = False
                turn["tool_calls"] = calls
            # In-turn stall detector: identical (phase, calls, last result)
            # `round_stall_limit` rounds running trips the breaker — the
            # model is looping without new information.
            sig = self._round_signature(turn, results_all)
            if sig == self._last_round_sig:
                self._round_stall_count += 1
                if self._round_stall_count >= cfg["round_stall_limit"]:
                    if not self._stall_warned:
                        # First threshold hit: warn and grant exactly one
                        # more round with the warning as feedback — the
                        # model gets a chance to break the loop itself.
                        # A repeated identical round after the warning
                        # terminates the turn.
                        self._stall_warned = True
                        self.state.record(
                            "round_stall_warning",
                            {"turn_id": turn_id, "round": round_no,
                             "signature": str(sig)[:300],
                             "detail": ("first threshold hit; one more "
                                        "round granted before halt")})
                        self.state.persist_snapshot()
                        self._record_round_assistant(turn)
                        round_feedback = (
                            f"HARNESS STALL WARNING (round {round_no}): the "
                            f"last {cfg['round_stall_limit']} rounds were "
                            f"identical (same phase, same tool calls, same "
                            f"last result) — the turn is looping with no new "
                            f"information. Next round do something "
                            f"different (a new tool, new arguments, or stop "
                            f"calling tools and finalize), or the turn "
                            f"halts as stalled.")
                        round_no += 1
                        continue
                    return self._halt_turn("stall", turn_id, turn_no, round_no)
            else:
                self._round_stall_count = 0
                self._stall_warned = False
            self._last_round_sig = sig
            self._record_round_assistant(turn)
            if not calls:
                # Normal exit: a validated round that calls no tools.
                return self._finalize_turn(turn_id, turn, results_all,
                                           routing, expected_header)
            # Per-round contract pre-execute check. Hard breaks are round
            # feedback (the model replans); only the missing-approval break
            # routes to the approval gate.
            xbreaks = check_pre_execute(
                self.state, tcontract, turn,
                has_valid_approval=self._has_valid_approval,
                search_dirs=self._search_dirs())
            hard = [b for b in xbreaks
                    if b.reason != BREAK_WRITE_WITHOUT_APPROVAL]
            if hard:
                self.state.record(
                    "contract_refused",
                    {"stage": "pre_execute", "round": round_no,
                     "breaks": [{"reason": b.reason, "detail": b.detail}
                                for b in hard],
                     "recourse": "replan"})
                round_feedback = (
                    f"HARNESS REJECTION (round {round_no}): "
                    + "; ".join(f"{b.reason}: {b.detail}" for b in hard)
                    + ". Replan within the contract and resubmit.")
                round_no += 1
                continue
            if xbreaks:
                self.state.record(
                    "contract_refused",
                    {"stage": "pre_execute", "round": round_no,
                     "breaks": [{"reason": b.reason, "detail": b.detail}
                                for b in xbreaks],
                     "recourse": "approval_gate"})
            # Harness interception: task tools run now; attempt_completion
            # defers until after the round's immediate calls (it verifies
            # against their results).
            h_results, harness_idx, deferred = self._intercept_harness_calls(
                turn_id, turn_no, round_no, calls)
            rest = [(i, calls[i]) for i in range(len(calls))
                    if i not in harness_idx]
            need_idx = {i for i, c in rest
                        if TOOL_DEFS[c["name"]]["consequential"]
                        and not self._has_valid_approval(c)}
            immediate = [(i, c) for i, c in rest if i not in need_idx]
            # Index-keyed execution (not _execute_calls' positional list)
            # so harness + executor results merge back into call order.
            exec_pairs = {}
            for i, c in immediate:
                call_id = f"{turn_id}.{self._call_offset + i}"
                exec_pairs[i] = self._execute_single(
                    call_id, c["name"], c["args"], self._idem(c))
            merged = dict(h_results)
            merged.update(exec_pairs)
            completion = None
            if deferred is not None and not need_idx:
                tok = self._cancel_token
                if tok is None or not tok.is_set():
                    env, inner = self._run_deferred_completion(
                        turn_id, round_no, deferred)
                    merged[deferred[0]] = env
                    if inner.get("completed"):
                        completion = inner
            round_pairs = [(i, merged[i]) for i in sorted(merged)]
            round_results = [r for _, r in round_pairs]
            results_all.extend(round_results)
            self._call_offset += len(calls)
            note = None
            if deferred is not None and need_idx:
                # The completion claim is dropped: it was made before the
                # paused writes execute, so it cannot verify. The resumed
                # round's model must re-claim after observing the effects.
                note = ("attempt_completion was deferred past an approval "
                        "pause and dropped — re-claim after the approved "
                        "calls' results if the mission is complete.")
            self._record_round_tool(round_pairs, calls, note=note)
            # The elevator runs at the top of the next round (and once more
            # at finalize) — single-sourced, idempotent.
            tok = self._cancel_token
            if tok is not None and tok.is_set():
                return self._halt_turn("cancelled", turn_id, turn_no, round_no)
            if need_idx:
                need = [(i, calls[i]) for i in sorted(need_idx)]
                return self._pause_for_approval(
                    turn_id, turn, results_all, need, routing,
                    expected_header, base=self._call_offset,
                    round_ctx={"round_no": round_no,
                               "call_offset": self._call_offset,
                               "last_round_sig": self._last_round_sig,
                               "round_stall_count": self._round_stall_count,
                               "stall_warned": self._stall_warned,
                               "user_text": user_text,
                               "input_kind": input_kind,
                               "transcript": self._round_transcript})
            if completion is not None and completion.get("completed"):
                done_results = results_all
                return self._finalize_turn(turn_id, turn, done_results,
                                           routing, expected_header,
                                           completion=completion)
            round_feedback = None
            round_no += 1

    # ---- Rigor: three-strike doom-loop circuit breaker ----
    # Agent-rigor's Error Recovery Protocol (STOP → DIAGNOSE → ISOLATE →
    # ROLLBACK → LOG → RETRY) as a loop-level transition. Awino previously
    # only failed forward (verify_failed → request_phase("BUILD") → patch
    # again). This adds the explicit rollback-and-rethink path: 3 consecutive
    # failures sharing one signature record doom_loop_detected, which makes
    # the router inject rigor-three-strike until a turn validates. The breaker
    # never touches the working tree itself — rollback stays the agent's act,
    # journaled as a `rollback` event the coach later audits.
    _DOOM_LOOP_THRESHOLD = 3
    # Event types that carry no turn outcome: skipped, never chain-breaking.
    # Mirrors rigor.failure_clusters' _NEUTRAL so the live detector and the
    # coach audit the same pattern. Recovery is proven by the SUCCESS set
    # (or a rollback), not by journaling a learning between failures.
    _DOOM_LOOP_NEUTRAL = frozenset({
        "tokens_charged", "egress", "harness_check", "skills_routed",
        "stance_routed", "mode_routed", "tool_called", "progress_recorded",
        "doom_loop_detected", "rigor_report", "learning_recorded",
        "assumption_recorded", "questions_asked", "plan_updated",
    })
    # Event types proving recovery: break any failure chain.
    _DOOM_LOOP_SUCCESS = frozenset({
        "turn_validated", "verify_passed", "mission_done", "operator_resumed",
    })

    @staticmethod
    def _failure_signature(source: str, parts) -> str:
        norm = sorted({str(p).strip()[:160] for p in parts if str(p).strip()})
        return source + ":" + "|".join(norm)

    def _check_doom_loop(self, source: str, signature: str) -> bool:
        """Count consecutive same-signature failures; trip the breaker at 3.

        Walks the journal backward. Success events break the chain; failures
        with a different signature reset it (a new approach is what we want);
        bookkeeping events are skipped. Returns True when the breaker is (or
        already was) active.
        """
        if self.state.snapshot.get("doom_loop_active"):
            return True
        consecutive = 0
        for ev in reversed(self.state.events):
            t = ev.get("type")
            if t in self._DOOM_LOOP_SUCCESS:
                break
            if t in self._DOOM_LOOP_NEUTRAL:
                continue
            sig = None
            if t == "turn_rejected":
                sig = self._failure_signature(
                    "turn_rejected", ev.get("data", {}).get("errors", []))
            elif t == "verify_failed":
                d = ev.get("data", {})
                failed = [e.get("criterion") or e.get("label") or "?"
                          for e in (d.get("verdict") or [])
                          if e.get("accomplished") != "yes"]
                sig = self._failure_signature(
                    "verify_failed", failed or [d.get("reason", "?")])
            else:
                break  # escalation, stall, phase change…: not this loop
            if sig == signature:
                consecutive += 1
            else:
                break  # different signature: the agent already rethought
        if consecutive >= self._DOOM_LOOP_THRESHOLD:
            self.state.record("doom_loop_detected",
                              {"source": source, "signature": signature,
                               "consecutive": consecutive})
            return True
        return False

    def _doom_loop_feedback(self) -> str:
        return ("RIGOR CIRCUIT BREAKER — STOP. Three consecutive failures "
                "share one signature. Do NOT patch forward. Follow the "
                "rigor-three-strike skill now routed into your contract: "
                "revert to the last known-good state, state your diagnosis "
                "in one paragraph, then re-approach from clean state. "
                "A fourth identical attempt will be escalated.")

    def _sensor_route(self, user_text: str, input_kind: str) -> dict:
        """Stage 2: route the (mode, stance chain, skills) triple in code.

        Intent patterns override the floor defaults. Records routing events;
        returns the live routing for this turn.
        """
        s = self.state.snapshot
        intent, mode, chain, skills, trigger = route_triple(s, user_text,
                                                           input_kind)
        # Track D: the active role lens appends its skill body (perspective +
        # evidence checklist) to the routed skills. It cannot change the
        # offered tools — the contract's ROLE MODE section states this, and
        # a test diffs the offered set with/without the lens.
        rm = s.get("role_mode") or {}
        lens = _modes.ROLE_SKILL_NAMES.get(rm.get("role", "")) if rm else None
        if lens:
            if lens in get_skill_store().names() and lens not in skills:
                skills = [*skills, lens]
            elif lens not in get_skill_store().names():
                self.state.record("role_lens_missing", {"lens": lens})
        # Rigor: while the doom-loop circuit breaker is active, the harness
        # injects rigor-three-strike into every turn's routed skills — the
        # explicit rollback-and-rethink transition. Cleared by turn_validated.
        # Layered loading: this is the ONLY way this skill enters context.
        if (s.get("doom_loop_active") and "rigor-three-strike" not in skills
                and "rigor-three-strike" in get_skill_store().names()):
            skills = [*skills, "rigor-three-strike"]
            trigger = trigger + " + doom-loop circuit breaker"
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

        Fanout (Track H): a worker Loop carrying ``parent_mode_tools`` in
        its snapshot has its offered tools clamped to the parent's mode
        policy. Even if per-turn routing escalates the worker's own mode,
        the worker can never wield a tool its parent's mode forbids.
        Regular loops never set ``parent_mode_tools`` and are unaffected.
        """
        offered = list(MODES[self.state.snapshot["mode"]]["tools"])
        parent_tools = self.state.snapshot.get("parent_mode_tools")
        if parent_tools is not None:
            offered = [t for t in offered if t in parent_tools]
        # v0.6: the harness tools are loop protocol, not mode tools — they
        # are offered in every mode and intercepted by the loop (they never
        # reach the sandbox). Appended after the fanout clamp.
        for ht in HARNESS_TOOLS:
            if ht not in offered:
                offered.append(ht)
        return offered

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
            if c.get("name") in ("write_file", "patch_file"):
                path = c.get("args", {}).get("path", "")
                if s["phase"] != "BUILD":
                    errs.append(f"{c['name']} only permitted on the BUILD floor "
                                f"(current: {s['phase']})")
                elif not s["contract_approved"] or s["scope"] is None:
                    errs.append(f"{c['name']} requires an approved contract with "
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
        evs = self.state.events
        # Rigor (R3/R4): the judge needs verification evidence counts, not
        # just tool-result counts, to refuse evidence-free done/verified
        # claims. Additive keys — existing consumers unaffected.
        verify_events = sum(1 for e in evs if e["type"] in (
            "verify_started", "verify_verdict", "verify_passed",
            "verify_failed"))
        test_runs = 0
        for e in evs:
            if e.get("type") != "tool_called":
                continue
            d = e.get("data") or {}
            if d.get("name") != "run_command":
                continue
            cmd = str((d.get("args") or {}).get("cmd") or "")
            if re.search(r"\b(pytest|jest|vitest|mocha|go test|npm test|"
                         r"tsc\b|make test|\btest\b)", cmd, re.I):
                test_runs += 1
        return {
            "turn_count": s["turn_count"],
            "phase": s["phase"],
            "results_this_session": sum(1 for e in evs
                                        if e["type"] == "tool_result"),
            "verify_events": verify_events,
            "test_runs": test_runs,
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

    def _stream_setup(self, call_id: str, tool_name: str, args: dict):
        """Sidecar streaming protocol (spec §3): tool_progress events around
        the execution. Returns (stream, t0); stream is None when the turn is
        not streamed — zero overhead and zero behavior change otherwise."""
        stream = self._stream_for(self._turn_of_call(call_id))
        t0 = time.monotonic() if stream is not None else 0.0
        if stream is not None:
            stream.tool_progress(tool_name, "start",
                                 _tool_call_summary(tool_name, args))
        return stream, t0

    def _pre_execute(self, call_id: str, tool_name: str, args: dict,
                     idem_key: str, stream, t0) -> tuple[bool, dict | None]:
        """Shared preamble for tool execution: worker scope check and
        idempotency, plus the tool_called journal entry. Returns
        (proceed, early): when proceed is False, early is the already-
        journaled result and the tool must NOT execute."""
        # Phase D: worker file ownership — a worker Loop (has worker_id in
        # its snapshot) may only touch files within its owned_files scope.
        scope = self.state.snapshot.get("scope")
        worker_id = self.state.snapshot.get("worker_id")
        if worker_id and scope and tool_name in ("write_file", "patch_file",
                                                       "read_file"):
            path = (args.get("path") or "")
            # owned_files are prefixes like "docs/"; path must start with one
            if not any(path == p.rstrip("/") or path.startswith(p)
                       for p in scope):
                self.state.record("tool_called",
                                  {"call_id": call_id, "tool": tool_name,
                                   "args": args, "idem_key": idem_key,
                                   "mission_rev": self.state.snapshot["mission_revision"]})
                result = {"error": f"ScopeViolation: worker {worker_id} "
                                   f"cannot access '{path}' (owned: {scope})"}
                self.state.record("tool_result",
                                  {"call_id": call_id, "tool": tool_name,
                                   "args": args, "idem_key": idem_key,
                                   "result": result,
                                   "mission_rev": self.state.snapshot["mission_revision"]})
                if stream is not None:
                    stream.tool_progress(tool_name, "error",
                                         str(result["error"])[:160],
                                         ms=_ms_since(t0))
                return False, {"tool": tool_name, "result": result}
        # Idempotency: never re-execute an effect we already have a result
        # for. v0.6 fix: this applies ONLY to effect tools (write_file,
        # patch_file). Observation tools (run_command, read_file, git_diff,
        # ...) must ALWAYS re-execute — re-observing the world is the
        # point. The old code keyed reuse purely on (tool, args), so a
        # post-repair `run_command pytest` reused the pre-repair failure
        # result without executing: the repair loop could never observe
        # the green run and spun forever on a stale exit code.
        if tool_name in ("write_file", "patch_file"):
            for e in reversed(self.state.events):
                if (e["type"] == "tool_result"
                        and e["data"].get("idem_key") == idem_key):
                    res = e["data"]["result"]
                    self.state.record(
                        "tool_result",
                        {"call_id": call_id, "tool": tool_name, "args": args,
                         "idem_key": idem_key, "result": res, "reused": True,
                         "mission_rev": self.state.snapshot["mission_revision"]})
                    if stream is not None:
                        stream.tool_progress(tool_name, "done",
                                             "reused cached result",
                                             ms=_ms_since(t0))
                    return False, {"tool": tool_name, "result": res,
                                   "reused": True}
        self.state.record("tool_called",
                          {"call_id": call_id, "tool": tool_name, "args": args,
                           "idem_key": idem_key,
                           "mission_rev": self.state.snapshot["mission_revision"]})
        return True, None

    def _post_execute(self, call_id: str, tool_name: str, args: dict,
                      idem_key: str, result: dict, stream, t0) -> dict:
        """Shared postamble for tool execution: patch_file's dedicated
        journal entry, the tool_result entry, and the stream done event.
        Identical journaling whether the effect ran in-process or delegated."""
        if tool_name == "patch_file":
            # Dedicated journal entry: the applied patch or the named
            # refusal, so the journal shows patch outcomes explicitly
            # rather than only inside generic tool_result payloads.
            self.state.record(
                "patch_applied" if "error" not in result else "patch_refused",
                {"call_id": call_id, "path": args.get("path"),
                 "hunks_applied": result.get("hunks_applied", 0),
                 "digest": result.get("digest"),
                 "error": result.get("error"),
                 "error_code": result.get("error_code"),
                 "idem_key": idem_key,
                 "mission_rev": self.state.snapshot["mission_revision"]})
        self.state.record("tool_result",
                          {"call_id": call_id, "tool": tool_name, "args": args,
                           "idem_key": idem_key, "result": result,
                           "mission_rev": self.state.snapshot["mission_revision"]})
        # v0.6 back half: tool_result hook (error-isolated).
        self.hooks.fire("tool_result",
                        {"call_id": call_id, "tool": tool_name,
                         "ok": "error" not in result, "harness": False})
        if stream is not None:
            stream.tool_progress(
                tool_name,
                "error" if _tool_failed(result) else "done",
                _tool_result_summary(result), ms=_ms_since(t0))
        return {"tool": tool_name, "result": result}

    def _resolve_tool_fn(self, tool_name: str):
        """Native tool application: when a delegation is attached (the VS
        Code extension advertised apply/terminal capability), the delegated
        executor runs the effect instead of the in-process sandbox. Only
        run_command resolves per-call here — write_file/patch_file are
        partitioned to _execute_delegated_batch by _drain_pending (single
        undo unit), but the delegation's per-call tool_fn remains the
        fail-safe for any other path (e.g. _execute_calls). None (no
        delegation) returns the historical sandbox implementation."""
        if tool_name == "set_mission":
            # Harness-owned interview-convergence tool (0.5.3 hotfix port):
            # not a Sandbox method, never delegated — the model calls it to
            # converge the discovery interview into a recorded mission.
            return self._harness_set_mission
        if self.delegation is not None:
            fn = self.delegation.tool_fn(tool_name)
            if fn is not None:
                return fn
        return getattr(self.sandbox, tool_name)

    def _compute_delegated_edit(self, tool_name: str, args: dict) -> dict:
        """Compute the full new content for a delegated write WITHOUT
        touching disk. patch_file hunks are validated in-process with the
        same strict, atomic parser the sandbox uses, so a malformed patch
        refuses here — the extension only ever applies bytes the loop
        already accepted. Returns the edit payload:
          {"call_id"... set by caller, "tool", "path", "content",
           "old_digest": sha256-of-preimage | None, "digest": sha256(content)}
        or {"error", "error_code"} for refusals."""
        path = args.get("path") or ""
        try:
            self.sandbox._resolve(path)  # ValueError on traversal, like write_file
        except ValueError as ex:
            return {"error": str(ex), "error_code": "delegated_path_escape"}
        if tool_name == "write_file":
            content = args.get("content", "")
            old_digest = None
            p = self.sandbox._resolve(path)
            if p.is_file():
                old_digest = hashlib.sha256(p.read_bytes()).hexdigest()
        elif tool_name == "patch_file":
            diff = args.get("diff", "")
            try:
                hunks, new_file, strip_nl = _parse_unified_diff(diff)
                p = self.sandbox._resolve(path)
                existed = p.is_file()
                if not existed:
                    if not (new_file and all(not h["old_lines"] for h in hunks)):
                        raise PatchRefusal(
                            "PATCH_TARGET_MISSING",
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
                content = "" if not new_lines else \
                    "\n".join(new_lines) + ("\n" if new_trailing else "")
                old_digest = hashlib.sha256(orig_text.encode()).hexdigest() \
                    if existed else None
            except PatchRefusal as r:
                return {"error": str(r), "error_code": r.code}
        else:
            return {"error": f"not a delegated write tool: {tool_name}",
                    "error_code": "delegated_edit_invalid"}
        digest = hashlib.sha256(content.encode()).hexdigest()
        return {"tool": tool_name, "path": path, "content": content,
                "old_digest": old_digest, "digest": digest}

    def _execute_delegated_batch(self, calls: list[dict]) -> list[dict]:
        """NATIVE-APPLY (extension builder): batch every granted file write
        of one drain into a single delegated round-trip, so the extension
        can apply them in ONE WorkspaceEdit (one undo unit). Scope,
        idempotency, and journaling are identical to _execute_single —
        only the effect backend changes (extension instead of in-process
        sandbox). Returns results in the same order as `calls`. The
        recursive-loop builder owns control flow; this method only changes
        WHERE the bytes land."""
        out: dict[int, dict] = {}
        pending: list[tuple[int, dict, dict, object, float]] = []  # (i, pc, edit, stream, t0)
        for i, pc in enumerate(calls):
            call_id, tool_name, args, idem_key = (
                pc["call_id"], pc["tool"], pc["args"], pc["idem_key"])
            stream, t0 = self._stream_setup(call_id, tool_name, args)
            proceed, early = self._pre_execute(call_id, tool_name, args,
                                               idem_key, stream, t0)
            if not proceed:
                out[i] = early
                continue
            edit = self._compute_delegated_edit(tool_name, args)
            edit["call_id"] = call_id
            edit["idem_key"] = idem_key
            if edit.get("error"):
                out[i] = self._post_execute(
                    call_id, tool_name, args, idem_key,
                    {"error": edit["error"],
                     "error_code": edit.get("error_code",
                                            "delegated_edit_invalid")},
                    stream, t0)
                continue
            pending.append((i, pc, edit, stream, t0))
        if pending:
            try:
                batch_results = self.delegation.apply_batch(
                    [edit for _, _, edit, _, _ in pending])
            except Exception as ex:  # noqa: BLE001 - delegation failure is data
                batch_results = [
                    {"call_id": pc["call_id"], "ok": False,
                     "error": f"delegation failed: {type(ex).__name__}: {ex}"}
                    for _, pc, _, _, _ in pending]
            by_call = {r.get("call_id"): r for r in batch_results
                       if isinstance(r, dict)}
            for i, pc, edit, stream, t0 in pending:
                call_id, tool_name, args, idem_key = (
                    pc["call_id"], pc["tool"], pc["args"], pc["idem_key"])
                br = by_call.get(call_id)
                if not isinstance(br, dict) or not br.get("ok"):
                    err = (br or {}).get("error",
                                         "extension returned no result")
                    result: dict = {"error": str(err),
                                    "error_code": "delegated_apply_failed"}
                else:
                    result = br.get("result") or {}
                    # Integrity: the extension echoes the sha256 of what it
                    # wrote; it must match the content the loop computed.
                    if result.get("digest") != edit["digest"]:
                        result = {
                            "error": "digest mismatch: the extension wrote "
                                     "different bytes than the loop computed",
                            "error_code": "delegated_digest_mismatch"}
                    else:
                        # The sandbox did not write these bytes — record them
                        # in the tamper manifest so verify_manifest stays
                        # honest about delegated writes.
                        self.sandbox.note_external_write(args.get("path", ""),
                                                         result["digest"])
                out[i] = self._post_execute(call_id, tool_name, args,
                                            idem_key, result, stream, t0)
        return [out[i] for i in range(len(calls))]

    def _execute_single(self, call_id: str, tool_name: str, args: dict,
                        idem_key: str) -> dict:
        stream, t0 = self._stream_setup(call_id, tool_name, args)
        proceed, early = self._pre_execute(call_id, tool_name, args, idem_key,
                                           stream, t0)
        if not proceed:
            return early
        fn = self._resolve_tool_fn(tool_name)
        # v0.6: cooperative cancellation reaches the tool itself — a tool
        # whose signature accepts `cancel` (e.g. run_command's Popen poll
        # loop) gets the turn's CancelToken so a long-running command can
        # be pre-empted mid-execution, not just between rounds.
        try:
            import inspect as _inspect
            _params = _inspect.signature(fn).parameters
            _extra = ({"cancel": self._cancel_token}
                      if "cancel" in _params and self._cancel_token is not None
                      else {})
        except (TypeError, ValueError):
            _extra = {}
        try:
            result = fn(**args, **_extra)
        except Exception as ex:  # noqa: BLE001 - tool errors are data
            result = {"error": f"{type(ex).__name__}: {ex}"}
        return self._post_execute(call_id, tool_name, args, idem_key, result,
                                  stream, t0)

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
                            expected_header: str, base: int = 0,
                            round_ctx: dict | None = None) -> dict:
        approvals, pending = [], []
        for i, c in need:
            call_id = f"{turn_id}.{base + i}"
            ap = {"id": "ap-" + _uid()[:8], "call_id": call_id, "tool": c["name"],
                  "args": c["args"], "idem_key": self._idem(c),
                  "revision": self._revision(), "status": "pending"}
            if c["name"] == "run_command":
                # Approval-target visibility (not prohibition): resolve the
                # shell command's file targets against the workspace cwd and
                # flag out-of-workspace addressing on the approval card.
                # The user remains the authority — this changes nothing
                # about approve/deny semantics.
                try:
                    ap["shell_targets"] = resolve_shell_targets(
                        str((c.get("args") or {}).get("cmd") or ""),
                        str(self.sandbox.root))
                except Exception:  # noqa: BLE001 - visibility must never
                    # break the approval gate itself
                    pass
            approvals.append(ap)
            pending.append({"call_id": call_id, "tool": c["name"], "args": c["args"],
                            "idem_key": ap["idem_key"], "approval_id": ap["id"]})
        self.state.record("approval_requested",
                          {"turn_id": turn_id, "approvals": approvals,
                           "pending_calls": pending})
        self.state.record("turn_paused_for_approval",
                          {"turn_id": turn_id, "turn": turn, "results": results_so_far,
                           "routing": routing, "header": expected_header,
                           # v0.6: the real round context so the resumed
                           # loop continues at round_no + 1 with the same
                           # transcript, call offset, and stall state.
                           "round_ctx": round_ctx or {}})
        self.state.persist_snapshot()
        ids = [a["id"] for a in approvals]
        # v0.6 back half: approval_requested hook (error-isolated).
        self.hooks.fire("approval_requested",
                        {"turn_id": turn_id, "approval_ids": ids})
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
            _s = self._stream_for(self._turn_of_call(ap.get("call_id", "")))
            if _s is not None:
                _s.harness_check(
                    "approval_gate", "warn",
                    f"approval {ap['id']} stale: plan revision changed")
            self.state.persist_snapshot()
            return {"status": "stale",
                    "said": f"Approval {ap['id']} is stale (mission revision changed). "
                            f"The action was NOT executed."}
        if ap.get("scope_epoch", 0) != self.state.snapshot.get("scope_epoch", 0):
            self.state.record("approval_stale",
                              {"id": ap["id"], "reason": "scope epoch changed"})
            _s = self._stream_for(self._turn_of_call(ap.get("call_id", "")))
            if _s is not None:
                _s.harness_check(
                    "approval_gate", "warn",
                    f"approval {ap['id']} stale: scope changed")
            self.state.persist_snapshot()
            return {"status": "stale",
                    "said": f"Approval {ap['id']} is stale (scope changed). "
                            f"The action was NOT executed."}
        self.state.record("approval_granted", {"id": ap["id"]})
        # v0.6 back half: approval_decided hook (error-isolated). Fires
        # per decision, right where the decision is journaled — including
        # the early-return path below (other approvals still pending).
        self.hooks.fire("approval_decided",
                        {"approval_id": ap["id"], "decision": "granted"})
        _s = self._stream_for(self._turn_of_call(ap.get("call_id", "")))
        if _s is not None:
            # Parallel _emit: surface the gate decision; the journal record
            # above is unchanged.
            _s.harness_check("approval_gate", "pass",
                             f"approval {ap['id']} granted")
        # NATIVE-APPLY (extension builder): drain only when the approval
        # round is complete (no pending approvals left), so all granted
        # writes of the turn batch into ONE delegated request (one
        # WorkspaceEdit, one undo unit). Draining eagerly would split the
        # change set across multiple undo units.
        remaining = self._pending_approvals()
        if remaining:
            self.state.persist_snapshot()
            return {"status": "awaiting_approval",
                    "said": f"Approval {ap['id']} recorded; "
                            f"{len(remaining)} still pending."}
        return self._drain_pending()

    def deny(self, approval_id: str | None = None) -> dict:
        pend = self._pending_approvals()
        if not pend:
            return {"status": "none", "said": "No pending approvals."}
        ap = next((a for a in pend if a["id"] == approval_id), None) if approval_id else pend[0]
        if ap is None:
            return {"status": "none", "said": f"No pending approval {approval_id}."}
        # v0.6 back half: compute BEFORE recording — the approval_denied
        # fold removes the denied call from pending_calls, so computing
        # after would always yield []. The id feeds the resume note and
        # the approval_decided hook payload.
        denied_ids = [ap["id"]]
        self.state.record("approval_denied", {"id": ap["id"]})
        # v0.6 back half: approval_decided hook (error-isolated). Fires
        # per decision, right where the decision is journaled — including
        # the early-return path below (other approvals still pending).
        self.hooks.fire("approval_decided",
                        {"approval_id": ap["id"], "decision": "denied"})
        _s = self._stream_for(self._turn_of_call(ap.get("call_id", "")))
        if _s is not None:
            # Parallel _emit: surface the gate decision; the journal record
            # above is unchanged.
            _s.harness_check("approval_gate", "fail",
                             f"approval {ap['id']} denied")
        # NATIVE-APPLY (extension builder): like approve(), drain only when
        # the round is complete so granted writes batch atomically.
        remaining = self._pending_approvals()
        if remaining:
            self.state.persist_snapshot()
            return {"status": "awaiting_approval",
                    "said": f"Approval {ap['id']} denied; "
                            f"{len(remaining)} still pending."}
        # The round's denied IDs were computed above, before the fold
        # removed the denied call from pending_calls.
        return self._drain_pending(denied=denied_ids)

    def _drain_pending(self, denied: list[str] | None = None) -> dict:
        s = self.state.snapshot
        results: list[dict] = []
        granted = []
        for pc in list(s["pending_calls"]):
            ap = next((a for a in s["approvals"] if a["id"] == pc["approval_id"]), None)
            if ap and ap["status"] == "granted":
                granted.append(pc)
        # NATIVE-APPLY (extension builder): when a delegation is attached,
        # every granted file write of this drain goes through ONE delegated
        # batch (one extension round-trip, one WorkspaceEdit, one undo
        # unit) instead of per-call sandbox writes. Results splice back into
        # pending_calls order so journaling order is unchanged.
        tok = self._cancel_token
        try:
            if tok is not None and tok.is_set():
                raise Cancelled("cancelled before approval drain")
            if self.delegation is not None:
                writes = [pc for pc in granted
                          if pc["tool"] in ("write_file", "patch_file")]
                batch_out = self._execute_delegated_batch(writes) if writes else []
                by_call = {pc["call_id"]: r for pc, r in zip(writes, batch_out)}
                for pc in granted:
                    if pc["tool"] in ("write_file", "patch_file"):
                        results.append(by_call[pc["call_id"]])
                    else:
                        results.append(self._execute_single(pc["call_id"], pc["tool"],
                                                            pc["args"], pc["idem_key"]))
            else:
                for pc in granted:
                    results.append(self._execute_single(pc["call_id"], pc["tool"],
                                                        pc["args"], pc["idem_key"]))
        except Cancelled:
            # Cooperative cancel during the approval drain: whatever ran is
            # journaled (too-late-with-effects); the rest stays pending and
            # the turn stays paused — re-run approve/deny to continue.
            self.state.record("turn_cancelled",
                              {"phase": "approval_drain",
                               "reason": tok.reason if tok is not None else None,
                               "executed": len(results),
                               "remaining_pending": len(
                                   self.state.snapshot["pending_calls"])})
            self.state.persist_snapshot()
            return {"status": "cancelled",
                    "said": (f"Cancelled during the approval drain after "
                             f"{len(results)} granted call(s) executed. The rest "
                             "stay pending — re-run approve/deny to continue.")}
        s = self.state.snapshot
        if not s["pending_calls"]:
            self.state.record("approvals_cleared", {})
        active = s.get("active_turn")
        self.state.persist_snapshot()
        if active is None:
            return {"status": "ok", "said": "Approval recorded.",
                    "results": results}
        # v0.6: the turn does NOT finalize here. Resume the recursive loop
        # at the next round so the model observes the grant/deny outcome
        # and replans — an approval denial becomes a replan round, not a
        # dead end. The round context was persisted by _pause_for_approval.
        round_ctx = active.get("round_ctx") or {}
        notes = []
        if denied:
            notes.append(f"operator DENIED approval(s) {denied}: those calls "
                         "did not execute — replan without those effects.")
        if results:
            notes.append(f"operator APPROVED {len(results)} call(s); the "
                         "harness executed them and their results are recorded.")
        resume = {
            "round_no": round_ctx.get("round_no", 0),
            "call_offset": round_ctx.get("call_offset", 0),
            "last_round_sig": round_ctx.get("last_round_sig"),
            "round_stall_count": round_ctx.get("round_stall_count", 0),
            "stall_warned": round_ctx.get("stall_warned", False),
            "user_text": round_ctx.get("user_text", ""),
            "input_kind": round_ctx.get("input_kind", "info"),
            "transcript": list(round_ctx.get("transcript", [])),
            "results_so_far": list(active.get("results", [])) + results,
            "round_feedback": None,
            "resume_note": " ".join(notes) if notes else None,
        }
        tcontract = compile_turn_contract(self.state)
        offered = list(MODES[s.get("mode", "observe")]["tools"])
        for ht in HARNESS_TOOLS:
            if ht not in offered:
                offered.append(ht)
        return self._run_agent_loop(
            active["turn_id"], s.get("turn_count", 0) + 1,
            active.get("routing") or {}, tcontract, offered, None,
            resume=resume)

    # ------------------------------------------------------- contract approval
    def _has_write_effects(self) -> bool:
        """BUILD exit gate: fresh write effects on the BUILD floor.

        Only writes journaled SINCE the most recent entry into BUILD count.
        The predicate used to be monotonic over the whole mission revision,
        so an operator repair route (VERIFY -> BUILD after a test failure)
        bounced straight back to VERIFY at the next round's elevator check —
        before the model could patch — and the repair loop could never run.
        """
        rev = self.state.snapshot["mission_revision"]
        events = self.state.events
        start = 0
        for i, e in enumerate(events):
            if (e["type"] == "phase_changed"
                    and e["data"].get("phase") == "BUILD"):
                start = i + 1
        return any(e["type"] == "tool_result"
                   and e["data"].get("tool") in ("write_file", "patch_file")
                   and not e["data"].get("result", {}).get("error")
                   and not e["data"].get("reused")
                   and e["data"].get("mission_rev") == rev
                   for e in events[start:])

    def request_phase(self, target: str, reason: str = "") -> dict:
        """Request a phase transition through the ALLOWED_TRANSITIONS table.

        Legal: records phase_changed and returns ok. Illegal: records
        transition_refused, leaves the phase unchanged, returns refused.
        """
        s = self.state.snapshot
        frm = s["phase"]
        if target == frm:
            return {"status": "ok", "phase": frm, "note": "already there"}
        # Track G (hard gate): VERIFY -> REVIEW requires a journaled pass
        # verdict from a verifier worker. The builder never grades its own
        # work — without the verdict the transition is refused, plainly.
        if frm == "VERIFY" and target == "REVIEW" and not s.get("verify_pass"):
            self.state.record("transition_refused",
                              {"from": frm, "to": target,
                               "reason": "no journaled verification pass"})
            self.state.persist_snapshot()
            return {"status": "refused",
                    "said": ("Transition refused: VERIFY -> REVIEW needs a "
                             "passing verification first. What happened: no "
                             "verifier worker has journaled a pass verdict "
                             "for this mission. What it means: the done "
                             "criteria are not proven yet — the builder's "
                             "word doesn't count. Next action: run "
                             "verification (begin_verification), fix any "
                             "findings it reports, then retry.")}
        # Mechanical layer (osmani-constraints FLOOR): REVIEW -> SHIP runs the
        # deterministic floor checks on the mission diff — no new
        # suppression comments, no stubs, no unexplained skipped tests, no
        # secrets. Rules that tooling can check are checked by tooling; the
        # model never self-grades. Same hard-refuse pattern as VERIFY ->
        # REVIEW above.
        if frm == "REVIEW" and target == "SHIP":
            findings = self._floor_gate()
            if findings:
                shown = "; ".join(
                    f"{f['file']}:{f['line']} [{f['check']}] {f['detail']}"
                    for f in findings[:8])
                more = (f" (+{len(findings) - 8} more)"
                        if len(findings) > 8 else "")
                self.state.record(
                    "transition_refused",
                    {"from": frm, "to": target,
                     "reason": "constraints floor violations",
                     "findings": findings})
                self.state.persist_snapshot()
                return {"status": "refused",
                        "said": (
                            f"Transition refused: REVIEW -> SHIP — the "
                            f"constraints floor found "
                            f"{len(findings)} violation(s): {shown}{more}. "
                            f"What happened: deterministic checks on the "
                            f"mission diff found suppression comments, "
                            f"unimplemented stubs, skipped tests without a "
                            f"reason, or possible secrets. What it means: "
                            f"the floor is always enforced, no setup — a "
                            f"skipped test needs an inline reason, and the "
                            f"record is never weakened to make a change "
                            f"pass. Next action: fix each finding, then "
                            f"retry the transition.")}
        # Story ledger (planning gate): entering BUILD requires the active
        # story's spine — problem, approach, done criteria. The approach is
        # defined during planning; BUILD without it is refused, plainly.
        # Same hard-refuse pattern as the VERIFY gate above.
        if target == "BUILD" and target != frm:
            gate_said = self._story_planning_gate(frm, target)
            if gate_said is not None:
                self.state.record("transition_refused",
                                  {"from": frm, "to": target,
                                   "reason": "story spine incomplete"})
                self.state.persist_snapshot()
                return {"status": "refused", "said": gate_said}
        allowed = ALLOWED_TRANSITIONS.get(frm, ())
        if target not in allowed:
            self.state.record("transition_refused",
                              {"from": frm, "to": target,
                               "reason": reason or "not in ALLOWED_TRANSITIONS"})
            self.state.persist_snapshot()
            return {"status": "refused",
                    "said": f"Transition refused: {frm} -> {target} is not "
                            f"allowed ({reason or 'illegal jump'})."}
        self.state.record("phase_changed", {"phase": target, "reason": reason})
        # Track B (memory registry): phase transitions append breadcrumbs;
        # mission close (SHIP) runs the deterministic reflection pass and
        # banks a milestone. Guarded: no-ops when no registry is attached.
        reg = getattr(self, "registry", None)
        if reg is not None:
            try:
                mission = self.state.snapshot.get("mission") or {}
                mid = mission.get("id", "?")
                reg.add_breadcrumb(mid,
                                   f"phase {frm} -> {target}",
                                   stop_point=reason or f"entered {target}")
                if target == "SHIP":
                    reg.add_milestone("completion", self._reflect_on_close())
            except Exception:
                pass  # registry writes never break the loop
        # Track D: re-evaluate the role lens at phase boundaries (advisory —
        # the router only switches on a trigger or a clearly better fit).
        try:
            mission = self.state.snapshot.get("mission") or {}
            self.route_role(mission_text=mission.get("text", ""))
        except Exception:
            pass
        self.state.persist_snapshot()
        return {"status": "ok", "phase": target}

    def _story_planning_gate(self, frm: str, target: str) -> str | None:
        """Refuse ->BUILD when the active story lacks its spine.

        The active story is the one with status "doing". Its problem,
        approach, and done criteria must all be non-empty — the approach
        is defined during planning, so BUILD without it is illegal.
        Returns the plain-language refusal, or None when the gate passes
        (no registry attached, no doing story, or spine complete).
        """
        reg = getattr(self, "registry", None)
        if reg is None:
            return None
        try:
            from story import doing_stories  # local: keep loop import-light
            stories = doing_stories(reg.awino_dir)
        except Exception:
            return None
        need = {"problem": "the problem",
                "approach": "the approach (the spine)",
                "done_criteria": "done criteria (how we'll know it's done)"}
        for st in stories:
            missing = [m for m in need if not st.get(m)]
            if missing:
                return (
                    f"Transition refused: {frm} -> {target} needs the "
                    f"story's spine. What happened: story "
                    f"'{st['title']}' ({st['id']}) is missing "
                    f"{', '.join(need[m] for m in missing)}. What it "
                    f"means: the approach is defined during planning — "
                    f"BUILD without it is illegal, the mission would run "
                    f"without knowing how it will be solved or when it's "
                    f"done. Next action: fill in the missing fields "
                    f"(story_update), then retry.")
        return None

    # ------------------------------------------------- mechanical: floor gate
    def _mission_diff(self) -> str | None:
        """Best-effort unified diff of the mission workspace vs HEAD.

        Tracked changes come from `git diff HEAD`; untracked files the
        sandbox wrote are appended as synthetic added-file diffs (new files
        are exactly where secrets and stubs hide). Returns None when the
        workspace is not a git checkout — the gate then skips, journaled.
        """
        import subprocess
        from floor_checks import diff_for_new_file
        root = self.sandbox.root
        try:
            p = subprocess.run(
                ["git", "-C", str(root), "diff", "HEAD", "--"],
                capture_output=True, text=True, timeout=30)
            if p.returncode != 0:
                return None
            diff = p.stdout
            q = subprocess.run(
                ["git", "-C", str(root), "ls-files",
                 "--others", "--exclude-standard"],
                capture_output=True, text=True, timeout=30)
            if q.returncode == 0:
                for rel in q.stdout.splitlines():
                    rel = rel.strip()
                    if not rel:
                        continue
                    try:
                        content = (root / rel).read_text()
                    except (OSError, UnicodeDecodeError):
                        continue
                    if "\x00" in content:
                        continue
                    diff += diff_for_new_file(rel, content)
            return diff
        except Exception:
            return None

    def _floor_gate(self) -> list:
        """Run the osmani-constraints FLOOR checks on the mission diff.

        Returns the findings (empty = the floor holds). The run is
        journaled either way; a workspace with no VCS baseline is
        recorded as skipped, never silently passed.
        """
        from floor_checks import check_diff
        diff = self._mission_diff()
        if diff is None:
            self.state.record("floor_checks",
                              {"status": "skipped",
                               "reason": "no VCS baseline in workspace"})
            return []
        findings = check_diff(diff)
        self.state.record("floor_checks",
                          {"status": "pass" if not findings else "fail",
                           "findings": len(findings)})
        return findings

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
            self.request_phase("PLAN", reason="contract approved (DEFINE)")
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
            r = self.request_phase("BUILD",
                                   reason="contract approved with SCOPE")
            self.state.persist_snapshot()
            if r["status"] == "refused":
                # A hard gate (e.g. the story planning gate) refused the
                # transition — propagate the refusal plainly instead of
                # claiming BUILD was entered.
                return r
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
            if tool in ("write_file", "patch_file"):
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
                       routing: dict, expected_header: str,
                       completion: dict | None = None) -> dict:
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

        # Elevator exit gates (code checks on the transitions, not
        # reminders). Also runs per round; idempotent here.
        self._check_elevator_gates(turn_id)
        s = self.state.snapshot
        done = None
        if completion is not None and completion.get("completed"):
            done = completion
        elif s["phase"] == "REVIEW" and turn.get("done_claim"):
            # Legacy backstop: a done_claim that reached finalize without
            # interception (e.g. a turn paused under v0.5.2 and resumed).
            # Semantic validation already proved every criterion in code,
            # and the turn already passed the REVIEW premortem rubric.
            done = {"completed": True,
                    "evidence": {},
                    "summary": (turn.get("progress_delta") or "")[:500],
                    "via": "legacy_done_claim"}
        if s["phase"] == "REVIEW" and done:
            # The mission_done event is the terminal record; the phase
            # stays REVIEW (there is no DONE phase — downstream tooling
            # keys on the journal, not a phase string).
            self.state.record("mission_done",
                              {"turn_id": turn_id,
                               "via": done.get("via", "attempt_completion"),
                               "evidence": done.get("evidence"),
                               "summary": done.get("summary")})
            # v0.6 back half: mission_complete hook (error-isolated).
            self.hooks.fire("mission_complete",
                            {"turn_id": turn_id,
                             "via": done.get("via", "attempt_completion"),
                             "summary": done.get("summary")})
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

    def _record_egress(self, turn_id: str) -> None:
        """Track C: journal network I/O the backend performed for a turn.

        Backends that do real HTTP expose `last_egress` ->
        {"destination", "bytes_out", "bytes_in"} (consumed here). Local
        backends (scripted/echo) report nothing and no event is recorded.
        """
        report = getattr(self.backend, "last_egress", None)
        if not report:
            return
        try:
            self.backend.last_egress = None  # consume: one event per call
        except AttributeError:
            pass
        skills_now = list(self.state.snapshot.get("skills", []))
        store = get_skill_store()
        declared = all(store.network_declaration(n)["network"] == "declared"
                       for n in skills_now) if skills_now else True
        self.state.record("egress", {
            "turn_id": turn_id,
            "skills": skills_now,
            "destination": report.get("destination", "?"),
            "bytes_out": report.get("bytes_out", 0),
            "bytes_in": report.get("bytes_in", 0),
            "declared": declared,
            "note": ("network declared by routed skill(s)" if declared
                     else "UNDECLARED egress: no routed skill declared network"),
        })

    def _reflect_on_close(self) -> str:
        """Track B: deterministic mission-close reflection -> milestone text.

        Code-generated from the journal (no model): phases traversed,
        criteria status, learnings count. The model never grades itself.
        """
        s = self.state.snapshot
        mission = s.get("mission") or {}
        phases = []
        for e in self.state.events:
            if e["type"] == "phase_changed":
                phases.append(e["data"].get("phase"))
        seen = []
        for p in phases:
            if p not in seen:
                seen.append(p)
        ok, _gaps = verify_done_criteria(s, self.state.events,
                                         self._search_dirs(), manual_ok=True)
        n_learn = len(s.get("learnings", []))
        return (f"Mission closed: {mission.get('text', '(none)')[:120]} | "
                f"phases: {'->'.join(seen) or 'none'} | "
                f"criteria verified: {ok} | learnings banked: {n_learn}")

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
            self.request_phase("REVIEW", reason="done criteria verified")
            self.state.record("mission_done", {"via": "operator"})
            # v0.6 back half: mission_complete hook (error-isolated) —
            # the operator path completes the mission too.
            self.hooks.fire("mission_complete",
                            {"via": "operator", "summary": None})
            self.state.persist_snapshot()
            return {"status": "done",
                    "said": "Mission complete. All criteria verified from evidence."}
        self.state.record("done_rejected", {"gaps": gaps})
        self.state.persist_snapshot()
        return {"status": "rejected",
                "said": "Not done. Gaps: " + "; ".join(gaps), "gaps": gaps}

    # ---------------------------------------------------------------- status
    def budgets(self) -> dict:
        """Phase B: remaining budgets (turns, tokens, wall-clock seconds)."""
        s = self.state.snapshot
        cfg = self.config
        start_ts = s.get("mission_start_ts")
        elapsed = (time.time() - start_ts) if start_ts else 0.0
        return {
            "turns": {"used": s["turn_count"], "limit": cfg["max_turns"],
                      "remaining": max(0, cfg["max_turns"] - s["turn_count"])},
            "tokens": {"used": s["tokens_used"], "limit": cfg["token_budget"],
                       "remaining": max(0, cfg["token_budget"] - s["tokens_used"])},
            "seconds": {"used": round(elapsed, 1), "limit": cfg["max_seconds"],
                        "remaining": max(0.0, round(cfg["max_seconds"] - elapsed, 1))},
        }

    def effect_journal(self) -> list[dict]:
        """Phase B: ordered journal of effects (tool executions).

        Each entry: {seq, call_id, tool, args, idem_key, result_summary,
        digest, reused, mission_rev, resolved_via}. Derived from the event
        log; the log is the source of truth.
        """
        journal = []
        called = {}  # call_id -> tool_called event
        for e in self.state.events:
            if e["type"] == "tool_called":
                called[e["data"]["call_id"]] = e
            elif e["type"] == "tool_result":
                d = e["data"]
                c = called.get(d["call_id"], {})
                cd = c.get("data", {}) if c else {}
                result = d.get("result", {})
                # digest may live in the result payload (e.g. write_file)
                digest = d.get("digest") or result.get("digest")
                journal.append({
                    "seq": e["seq"],
                    "call_id": d["call_id"],
                    "tool": d["tool"],
                    "args": cd.get("args", d.get("args", {})),
                    "idem_key": d.get("idem_key", cd.get("idem_key")),
                    "result_summary": str(result)[:200],
                    "digest": digest,
                    "reused": bool(d.get("reused")),
                    "mission_rev": d.get("mission_rev"),
                    "resolved_via": d.get("resolved_via"),
                })
        return journal

    def verify_journal(self) -> tuple[bool, list[str]]:
        """Phase B: integrity check on the effect journal.

        Returns (ok, problems). Checks: every tool_result has a matching
        tool_called; no duplicate non-reused executions share an idem_key.
        """
        problems = []
        called_ids = {e["data"]["call_id"] for e in self.state.events
                      if e["type"] == "tool_called"}
        seen_idem = {}  # idem_key -> call_id (non-reused only)
        for e in self.state.events:
            if e["type"] != "tool_result":
                continue
            d = e["data"]
            cid = d["call_id"]
            if cid not in called_ids:
                problems.append(f"tool_result {cid} has no tool_called")
            if not d.get("reused"):
                key = d.get("idem_key")
                if key:
                    if key in seen_idem:
                        problems.append(
                            f"duplicate execution for idem_key {key}: "
                            f"{seen_idem[key]} and {cid}")
                    else:
                        seen_idem[key] = cid
        return (not problems, problems)

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
            "mission_revision": s["mission_revision"],
            "criteria": crit, "open_questions": s["open_questions"],
            "progress": [p["delta"] for p in s["progress"][-3:]],
            "pending_approvals": [a["id"] for a in s["approvals"]
                                  if a["status"] == "pending"],
            "turns": s["turn_count"], "tokens": s["tokens_used"],
            "learnings": s.get("learnings", [])[-5:],
            "flags": s["flags"][-5:], "done": s["done"],
            "next_action": next_action_line(s),
        }

    # --------------------------------------------- skill synthesis (follow-on)
    def synthesize_learning(self, index: int = -1) -> dict:
        """Synthesize a recorded learning into a verified skill.

        Pipeline (synthesis.py): injection screen -> deterministic draft ->
        sandbox verification -> hash-pinned admission into the project's
        skill registry. Refusal is the default: unverified prose, injected
        instructions, and failed checks are NEVER admitted. Records
        synthesis_admitted / synthesis_refused on the event log.
        """
        learnings = self.state.snapshot.get("learnings", [])
        if not learnings:
            return {"status": "refused", "code": "none",
                    "detail": "no learnings recorded yet"}
        try:
            learning = learnings[index]
        except IndexError:
            return {"status": "refused", "code": "bad_index",
                    "detail": f"no learning at index {index} "
                              f"({len(learnings)} recorded)"}
        registry = self.state.dir / "skills"
        result = _synthesize_learning(learning, self.sandbox, registry,
                                      packaged=self.skill_store)
        if result["status"] == "admitted":
            self.state.record("synthesis_admitted",
                              {"name": result["name"],
                               "sha256": result["sha256"],
                               "kind": learning.get("kind")})
        else:
            self.state.record("synthesis_refused",
                              {"code": result["code"],
                               "detail": result["detail"],
                               "kind": learning.get("kind")})
        return result

    # ------------------------------------------------------- Phase D: workers
    def spawn_worker(self, objective: str, owned_files: list[str],
                     budget_share: dict) -> dict:
        """Phase D: spawn a worker with fixed file ownership and a budget share.

        The worker gets its own project dir and Loop. Its SCOPE is fixed to
        owned_files — the worker cannot change its own scope. The budget_share
        (e.g. {"max_turns": 2}) is drawn from the parent's shared pool; if the
        pool is exhausted, spawning raises.
        """
        s = self.state.snapshot
        # shared budget: track allocated turns
        allocated = s.get("worker_budget_allocated", 0)
        requested = budget_share.get("max_turns", 0)
        limit = self.config.get("max_turns", 50)
        if allocated + requested > limit:
            raise RuntimeError(
                f"worker budget exhausted: allocated {allocated}, "
                f"requested {requested}, parent limit {limit}")
        wid = f"w-{_uid()[:8]}"
        worker_dir = (self.state.dir / "workers" / wid)
        worker_dir.mkdir(parents=True, exist_ok=True)
        # worker Loop with its own project id
        wloop = Loop(self.home, f"{s['project_id']}/{wid}",
                     self.backend, self.judge,
                     sandbox_dir=str(worker_dir / "sandbox"),
                     config={"max_turns": requested,
                             "max_seconds": budget_share.get("max_seconds", 3600)},
                     conversation_id=f"{wid}")
        # fix the worker's scope to owned_files (file ownership)
        wloop.state.snapshot["scope"] = list(owned_files)
        wloop.state.snapshot["worker_id"] = wid
        wloop.state.record("worker_scoped",
                           {"worker_id": wid, "owned_files": owned_files,
                            "objective": objective})
        # record the allocation on the parent (reducer updates the counter)
        self.state.record("worker_spawned",
                          {"worker_id": wid, "objective": objective,
                           "owned_files": owned_files,
                           "budget_share": budget_share})
        return {"worker_id": wid, "loop": wloop,
                "project_dir": str(worker_dir),
                "owned_files": owned_files,
                "budget_share": budget_share}

    def collect_worker(self, worker_id: str) -> dict:
        """Phase D: collect a worker's journal and artifacts into the parent."""
        worker_dir = self.state.dir / "workers" / worker_id
        if not worker_dir.exists():
            raise ValueError(f"unknown worker {worker_id}")
        # read the worker's artifacts (files in its sandbox)
        artifacts = []
        sandbox = worker_dir / "sandbox"
        if sandbox.exists():
            for p in sandbox.rglob("*"):
                if p.is_file():
                    artifacts.append(str(p.relative_to(sandbox)))
        self.state.record("worker_completed",
                          {"worker_id": worker_id, "artifacts": artifacts})
        return {"status": "collected", "worker_id": worker_id,
                "artifacts": artifacts}

    # -------------------------------------------- Track H: fan-out primitive
    # Parallel fan-out with a synthesize barrier, built on spawn_worker.
    # One worker per subtask, disjoint file ownership (checked atomically
    # up front), shared budget pool (checked atomically up front), threads
    # for parallel execution, and a fail-closed barrier: any worker
    # failure fails the whole fan-out, with explicit per-worker status.
    # Per-worker model routing is code-owned: the request carries a
    # routing map (model_routes: name -> backend) and each subtask may
    # name its backend; unknown names refuse in pre-flight (fail-closed).
    # Not yet implemented: tournament mode, loop-until-done (roadmap).
    def fanout(self, objective: str, subtasks: list,
               run_worker=None, backend_factory=None,
               synthesize=None, model_routes: dict | None = None) -> dict:
        """Spawn one worker per subtask, run them in parallel, and merge
        their structured results at a barrier.

        subtasks: list of {"objective": str, "owned_files": [str],
        "budget_share": {"max_turns": int, ...}, "backend": str (optional)}.

        Per-worker model routing (code-owned): pass model_routes, a
        mapping of backend name -> backend instance, and name a backend
        per subtask via the optional "backend" key (e.g. one worker on a
        fast/cheap model for drafting, another on a strong model for
        verification). Routing is resolved in pre-flight, before any
        worker spawns: an unknown backend name raises UnknownBackendError
        ("fanout refused") and no worker spawns. A subtask with no
        "backend" key falls back to backend_factory, then to the
        parent's backend.

        Pre-flight is atomic and runs BEFORE any worker spawns:
        - owned_files must be pairwise disjoint (overlap -> ValueError)
        - every named backend must exist in model_routes (unknown ->
          UnknownBackendError). The pool counter is untouched on refusal.
        - total budget shares must fit the shared pool (shortfall ->
          RuntimeError). The pool counter is untouched on refusal.
        There are no partial fan-outs.

        run_worker(wloop, subtask) -> dict runs one worker; the default
        sets the subtask as the worker's mission and drives one turn
        through the worker's own pipeline. A worker succeeds iff the
        runner returns {"status": "ok", ...}; a raise or any other status
        fails that worker. As defense in depth, a ScopeViolation recorded
        in the worker's journal fails the worker even if the runner
        claimed success.

        backend_factory(subtask, index) -> backend optionally gives each
        worker its own backend (recommended under threads; the default
        shares the parent's backend). backend_factory applies only to
        subtasks that do NOT name a backend in model_routes — routing
        wins where both are given.

        synthesize(workers) -> merged optionally customizes the merge;
        the default maps worker_id -> result.

        Failure is fail-closed: any worker failure raises FanoutFailed
        carrying explicit per-worker status (successful workers' results
        included, never silent).

        Mode inheritance: each worker starts at the parent's mode and
        its offered tools are clamped to the parent's mode policy (see
        _permission_gate) — a worker can never wield a tool its parent's
        mode forbids.
        """
        # ---- pre-flight (atomic; no state mutated before this passes) ----
        if not subtasks:
            raise ValueError("fanout requires at least one subtask")
        for i, st in enumerate(subtasks):
            for key in ("objective", "owned_files", "budget_share"):
                if not isinstance(st, dict) or key not in st:
                    raise ValueError(
                        f"fanout refused: subtask {i} missing required key "
                        f"{key!r}")
        for i in range(len(subtasks)):
            for j in range(i + 1, len(subtasks)):
                overlap = _owned_overlap(subtasks[i]["owned_files"],
                                         subtasks[j]["owned_files"])
                if overlap:
                    self.state.record(
                        "fanout_refused",
                        {"reason": "owned_files overlap",
                         "subtasks": (i, j), "overlap": overlap})
                    self.state.persist_snapshot()
                    raise ValueError(
                        f"fanout refused: subtask {i} and subtask {j} "
                        f"overlap on {overlap!r}; owned_files must be "
                        f"disjoint. No worker spawned.")
        # ---- routing resolution (atomic; before any worker spawns) ----
        # Code-owned model routing: resolve every subtask's named backend
        # against the request's routing map here. An unknown name refuses
        # the whole fan-out (fail-closed), exactly like overlap/budget.
        routed_backends: list = []
        for i, st in enumerate(subtasks):
            name = st.get("backend")
            if name is None:
                routed_backends.append(None)
                continue
            if (not isinstance(name, str) or model_routes is None
                    or name not in model_routes):
                known = sorted(model_routes) if model_routes else []
                self.state.record(
                    "fanout_refused",
                    {"reason": "unknown backend",
                     "subtask": i, "backend": name, "known": known})
                self.state.persist_snapshot()
                raise UnknownBackendError(
                    f"fanout refused: subtask {i} routes to unknown "
                    f"backend {name!r}; known backends: {known}. "
                    f"No worker spawned.")
            routed_backends.append(model_routes[name])
        s = self.state.snapshot
        allocated = s.get("worker_budget_allocated", 0)
        limit = self.config.get("max_turns", 50)
        total = sum(st["budget_share"].get("max_turns", 0)
                    for st in subtasks)
        if allocated + total > limit:
            self.state.record(
                "fanout_refused",
                {"reason": "budget shortfall", "allocated": allocated,
                 "requested": total, "limit": limit})
            self.state.persist_snapshot()
            raise RuntimeError(
                f"fanout refused: worker budget exhausted (allocated "
                f"{allocated}, requested {total}, parent limit {limit}). "
                f"No worker spawned; pool unchanged.")
        # ---- spawn (spawn_worker enforces the per-spawn budget again) ----
        parent_mode = s.get("mode", "observe")
        # v0.6: the authoritative offered set includes the harness tools
        # in every mode. set_mission is excluded: a worker's mission is
        # fixed by its parent, so the interview-convergence tool must never
        # reach a worker (0.5.3 hotfix port; belt-and-braces with the
        # tool-level refusal in _harness_set_mission).
        parent_tools = [t for t in
                        list(MODES[parent_mode]["tools"]) + list(HARNESS_TOOLS)
                        if t != "set_mission"]
        self.state.record(
            "fanout_started",
            {"objective": objective,
             "parent_mode": parent_mode,
             "subtasks": [{"objective": st["objective"],
                           "owned_files": list(st["owned_files"]),
                           "budget_share": dict(st["budget_share"]),
                           "backend": (st.get("backend")
                                       if routed_backends[idx] is not None
                                       else None)}
                          for idx, st in enumerate(subtasks)]})
        workers = []
        for idx, st in enumerate(subtasks):
            spawn = self.spawn_worker(
                st["objective"],
                owned_files=list(st["owned_files"]),
                budget_share=dict(st["budget_share"]))
            wloop = spawn["loop"]
            # mode inheritance: start at the parent's mode; the gate
            # clamps offered tools to the parent's policy from here on.
            wloop.state.snapshot["mode"] = parent_mode
            wloop.state.snapshot["parent_mode_tools"] = parent_tools
            # code-owned model routing: the request's routing map won in
            # pre-flight; backend_factory applies only to unrouted
            # subtasks; otherwise the worker shares the parent's backend.
            if routed_backends[idx] is not None:
                wloop.backend = routed_backends[idx]
            elif backend_factory is not None:
                wloop.backend = backend_factory(st, idx)
            workers.append({"spawn": spawn, "subtask": st})
        self.state.persist_snapshot()
        # ---- run in parallel; the barrier joins all threads ----
        runner = run_worker or _default_fanout_runner
        results: dict = {}

        def _run_one(w):
            wid = w["spawn"]["worker_id"]
            try:
                res = runner(w["spawn"]["loop"], w["subtask"])
            except Exception as e:  # noqa: BLE001 — worker failure is data
                results[wid] = {"worker_id": wid, "status": "failed",
                                "result": None,
                                "error": f"{type(e).__name__}: {e}"}
                return
            if not isinstance(res, dict) or res.get("status") != "ok":
                results[wid] = {"worker_id": wid, "status": "failed",
                                "result": None,
                                "error": f"runner returned non-ok: {res!r}"[:500]}
            else:
                results[wid] = {"worker_id": wid, "status": "ok",
                                "result": res, "error": None}

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(workers),
                thread_name_prefix="fanout") as ex:
            list(ex.map(_run_one, workers))
        # ---- defense in depth: a ScopeViolation in the worker's journal
        # fails the worker even if its runner claimed success ----
        for w in workers:
            wid = w["spawn"]["worker_id"]
            entry = results[wid]
            if entry["status"] == "ok" and _worker_hit_scope_violation(
                    w["spawn"]["loop"]):
                entry["status"] = "failed"
                entry["result"] = None
                entry["error"] = ("ScopeViolation in worker journal: worker "
                                  "attempted out-of-owned-files access")
        # ---- barrier: record, then fail closed or synthesize ----
        ordered = [results[w["spawn"]["worker_id"]] for w in workers]
        for r in ordered:
            self.state.record("fanout_worker_completed",
                              {"worker_id": r["worker_id"],
                               "status": r["status"], "error": r["error"]})
        failed = [r for r in ordered if r["status"] != "ok"]
        if failed:
            self.state.record("fanout_failed",
                              {"objective": objective, "workers": ordered})
            self.state.persist_snapshot()
            raise FanoutFailed(
                f"fanout failed: {len(failed)}/{len(ordered)} worker(s) "
                f"failed ({', '.join(r['worker_id'] for r in failed)}); "
                f"no partial results returned",
                {"objective": objective, "workers": ordered})
        merged = (synthesize(ordered) if synthesize is not None
                  else {r["worker_id"]: r["result"] for r in ordered})
        self.state.record("fanout_completed",
                          {"objective": objective,
                           "worker_ids": [r["worker_id"] for r in ordered]})
        self.state.persist_snapshot()
        return {"status": "ok", "objective": objective,
                "workers": ordered, "merged": merged}

    # -------------------------------------------- Track G: verification gate
    # The verifier is a SEPARATE worker with the verifier stance. The builder
    # never grades its own work: complete_verification reads the verdict from
    # the WORKER's journal only — a verdict forged in the parent's journal
    # is ignored. Only a worker-journaled pass unlocks VERIFY -> REVIEW.
    def begin_verification(self) -> dict:
        """Spawn the verifier worker. Plain-language result, never raises."""
        s = self.state.snapshot
        if s.get("phase") != "VERIFY":
            return {"status": "error",
                    "said": ("Verification starts from the VERIFY phase. "
                             "What happened: the mission is in "
                             f"{s.get('phase')}, not VERIFY. What it means: "
                             "there's nothing to verify yet. Next action: "
                             "finish the build work first, then verify.")}
        if s.get("verify_pending"):
            wid = s["verify_pending"]["worker_id"]
            return {"status": "error",
                    "said": (f"A verifier worker ({wid}) is already running. "
                             "What happened: verification is in progress. "
                             "Next action: run its turn, then complete it.")}
        try:
            spawn = self.spawn_worker(
                objective="verify the mission against its done criteria",
                owned_files=[],  # read-only: the verifier changes nothing
                budget_share={"max_turns": 2, "max_seconds": 600})
        except RuntimeError as e:
            return {"status": "error",
                    "said": f"Could not start verification: {e}. "
                            f"Next action: free up the turn budget and retry."}
        wid = spawn["worker_id"]
        self.state.record("verify_started", {"worker_id": wid})
        self.state.persist_snapshot()
        return {"status": "ok", "worker_id": wid,
                "said": (f"Verifier worker {wid} started (separate worker, "
                         f"read-only). Next action: run_verifier_turn, then "
                         f"complete_verification.")}

    def _worker_state(self, worker_id: str):
        """The worker's own journal (ProjectState), re-opened from disk."""
        s = self.state.snapshot
        return ProjectState(self.home, f"{s['project_id']}/{worker_id}")

    def run_verifier_turn(self, worker_id: str,
                          context: dict | None = None) -> dict:
        """Run the verifier worker's turn: compute the verdict and journal it
        ON THE WORKER's journal with role=verifier. Never raises."""
        s = self.state.snapshot
        pend = s.get("verify_pending") or {}
        if pend.get("worker_id") != worker_id:
            return {"status": "error",
                    "said": (f"No pending verification for worker {worker_id}. "
                             "What happened: this worker wasn't started by "
                             "begin_verification. Next action: call "
                             "begin_verification first.")}
        context = context or {}
        try:
            from verify import compute_verdict, criterion_text
            reg = getattr(self, "registry", None)
            dag_tasks = reg.tasks() if reg is not None else []
            blockers = reg.unblock_report() if reg is not None else []
            mission = s.get("mission") or {}
            # v0.6: the harness auto-spawn may narrow the verified set to
            # the artifact criteria (event criteria are already
            # journal-proven by the auto-spawn's pre-check; manual criteria
            # stay operator-only at the final done claim). A manual
            # begin_verification always verifies the full criterion set.
            criteria = context.get("criteria") or mission.get("done_criteria", [])
            criteria = [criterion_text(c) for c in criteria]
            role = (s.get("role_mode") or {}).get("role", "")
            role_ev = list(_modes.ROLES.get(role, {}).get("required_evidence", []))
            result = compute_verdict(
                criteria,
                evidence_links=context.get("evidence_links", {}),
                dag_tasks=dag_tasks, blockers=blockers,
                recipe_result=context.get("recipe_result"),
                role_evidence=role_ev,
                project_root=context.get("project_root", "."))
        except Exception as e:  # noqa: BLE001 — verdict failure is a finding
            result = {"verdict": [{"criterion": "verifier ran",
                                   "needed_evidence": "a computed verdict",
                                   "accomplished": "no",
                                   "proof_link": ""}],
                      "passed": False, "recipe": None,
                      "error": f"{type(e).__name__}: {e}"}
        wstate = self._worker_state(worker_id)
        wstate.record("verify_verdict",
                      {"worker_id": worker_id, "role": "verifier",
                       "verdict": result["verdict"], "passed": result["passed"]})
        wstate.persist_snapshot()
        self.state.record("verify_verdict",
                          {"worker_id": worker_id, "role": "verifier",
                           "passed": result["passed"],
                           "note": ("verdict journaled on the worker; "
                                    "parent records only the pointer")})
        self.state.persist_snapshot()
        return {"status": "ok", "worker_id": worker_id,
                "passed": result["passed"], "verdict": result["verdict"]}

    def complete_verification(self, worker_id: str) -> dict:
        """Collect the verifier's verdict from the WORKER's journal.

        Pass -> verify_passed unlocks VERIFY -> REVIEW.
        Fail -> findings become new DAG tasks; route back to BUILD.
        A verdict forged in the PARENT journal is ignored (worker isolation).
        """
        s = self.state.snapshot
        wstate = self._worker_state(worker_id)
        verdict_ev = None
        for e in wstate.events:
            d = e.get("data", {})
            if (e.get("type") == "verify_verdict"
                    and d.get("worker_id") == worker_id
                    and d.get("role") == "verifier"):
                verdict_ev = e
        if verdict_ev is None:
            self.state.record("verify_failed",
                              {"worker_id": worker_id,
                               "reason": "no verifier verdict in worker journal"})
            # Rigor: a verifier that never reports is itself a repeated
            # failure mode — trip the breaker so the agent rethinks instead
            # of blindly re-running the verifier.
            self._check_doom_loop("verify_failed", self._failure_signature(
                "verify_failed", ["no verifier verdict in worker journal"]))
            self.state.persist_snapshot()
            return {"status": "error",
                    "said": (f"Worker {worker_id} has no journaled verdict. "
                             "What happened: the verifier never recorded its "
                             "verdict. What it means: nothing was proven. "
                             "Next action: run_verifier_turn, then retry.")}
        data = verdict_ev["data"]
        entries = data.get("verdict", [])
        passed = bool(data.get("passed")) and all(
            e.get("accomplished") == "yes" for e in entries) and bool(entries)
        reg = getattr(self, "registry", None)
        if passed:
            self.state.record("verify_passed",
                              {"worker_id": worker_id, "verdict": entries})
            # Story ledger (finish ritual): a passing verdict proves the
            # active story's done criteria — journal story_ready_to_close
            # and ASK the user to close it. Close authority stays human;
            # the harness never closes a story by itself.
            said_extra = ""
            if reg is not None:
                try:
                    from story import doing_stories, mark_ready_to_close
                    stories = doing_stories(reg.awino_dir)
                    if stories:
                        st = stories[0]
                        mark_ready_to_close(reg.awino_dir, st["id"])
                        self.state.record(
                            "story_ready_to_close",
                            {"story_id": st["id"], "title": st["title"],
                             "worker_id": worker_id})
                        said_extra = (
                            f" Story '{st['title']}' ({st['id']}) is ready "
                            f"to close — its done criteria are proven. "
                            f"Next action: review it, then close it with "
                            f"story_close (the close is yours to make).")
                except Exception:
                    pass
            self.state.persist_snapshot()
            return {"status": "ok", "passed": True,
                    "said": (f"Verification PASSED ({len(entries)} criteria, "
                             f"all evidenced). Next action: request_phase "
                             f"('REVIEW') is now unlocked." + said_extra)}
        from verify import findings_as_tasks
        new_tasks = []
        if reg is not None:
            for text in findings_as_tasks(entries):
                try:
                    t = reg.add_task(text, source="verifier", state="open")
                    new_tasks.append(t["id"])
                except Exception:
                    pass
        self.state.record("verify_failed",
                          {"worker_id": worker_id, "verdict": entries,
                           "findings_tasks": new_tasks})
        # Rigor: repeated verification failures on the same criteria trip the
        # doom-loop breaker — the mission routes back to BUILD, but now with
        # rigor-three-strike in context demanding rollback-and-rethink, not
        # another fix-forward pass.
        failed_labels = [e.get("criterion") or e.get("label") or "?"
                         for e in entries if e.get("accomplished") != "yes"]
        self._check_doom_loop("verify_failed", self._failure_signature(
            "verify_failed", failed_labels))
        self.state.persist_snapshot()
        # Failed verification routes back to BUILD with findings as tasks.
        self.request_phase("BUILD", reason="verification failed")
        return {"status": "ok", "passed": False,
                "said": (f"Verification FAILED: "
                         f"{sum(1 for e in entries if e.get('accomplished') != 'yes')} "
                         f"criterion/criteria unmet. What happened: the "
                         f"verifier could not evidence everything. What it "
                         f"means: the mission goes back to BUILD. Next "
                         f"action: work the {len(new_tasks)} finding task(s) "
                         f"the verifier filed, then verify again.")}

    # -------------------------------------------------------- Phase E: rollback
    def rollback(self, seq: int) -> dict:
        """Phase E: rollback to sequence number seq (operator only).

        Truncates events.jsonl to seq (keeping a backup), rebuilds the
        snapshot by replay, and records a `rollback` event noting the
        truncation point. Crash-safe: backup first, then truncate, then reload.
        """
        import time
        import json
        import shutil
        from state import ProjectState
        events_file = self.state.dir / "events.jsonl"
        if not events_file.exists():
            return {"status": "error", "said": "no events file"}
        # backup first
        ts = int(time.time())
        backup = self.state.dir / f"events.jsonl.bak.{ts}"
        shutil.copy2(events_file, backup)
        # truncate to seq
        kept = [e for e in self.state.events if e["seq"] <= seq]
        truncated_count = len(self.state.events) - len(kept)
        # rewrite the file
        with open(events_file, "w") as f:
            for e in kept:
                f.write(json.dumps(e) + "\n")
        # rebuild state by replay (new ProjectState loads from the file)
        old_snapshot = self.state.snapshot
        self.state = ProjectState(
            self.home, old_snapshot["project_id"],
            old_snapshot.get("conversation_id"))
        # record the rollback
        self.state.record("rollback", {"to_seq": seq,
                                       "backup": backup.name,
                                       "truncated": truncated_count})
        return {"status": "ok",
                "said": f"rolled back to seq {seq} "
                        f"(backup: {backup.name}, "
                        f"{len(kept)} events kept, "
                        f"{truncated_count} truncated)"}
