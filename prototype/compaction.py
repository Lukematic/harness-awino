"""State-authoritative context compaction (v0.6 back half).

Alignment rule: STATE IS AUTHORITATIVE; CONVERSATION IS DISPOSABLE CONTEXT.

The Loop's working context (``loop.history`` plus the in-turn
``loop._round_transcript``) grows every turn and round. When it exceeds
budget, this module compacts it DOWN TO the authoritative state — the
event log (journal) plus the state snapshot (mission, phase, tasks,
approvals, session binding, progress) — and rebuilds a working context
from state alone. Never the reverse: the transcript is never treated as
a source of truth.

What survives, and how:
- KEPT VERBATIM (rebuilt from state, not from the old transcript): the
  state block — mission text + revision, phase, mode, done criteria with
  live status, the task list, pending approvals, the progress tail, open
  questions, the learnings tail. Deterministic projection of the
  snapshot: identical across repeated compactions of the same state
  except the rebuild-timestamp header line.
- KEPT VERBATIM (live working set): the most recent user message and the
  in-progress round transcript. These have no durable home yet — the
  round transcript is folded into history only when the turn advances —
  so dropping them would lose the current turn's working memory.
- DROPPED: older conversational entries — assistant chatter, stale tool
  result summaries, superseded user messages. Full detail remains in the
  journal (events.jsonl), the durable record, so nothing is silently
  lost. A previous compaction's state block is dropped too: it is a
  stale projection, always rebuilt fresh.

Invariant: compaction NEVER touches state. Every behaviorally-relevant
input (mission, tasks, approvals, binding, criteria) is read from state,
which is unchanged — so post-compaction behavior is identical, and the
compiled contract block is byte-identical before and after. The
``context_compacted`` journal event records what was dropped and the
tokens saved; it folds into no snapshot field.
"""

from __future__ import annotations

import copy
import datetime

# Default working-context budget in estimated tokens. The sidecar's own
# 0.85-of-window proposal flow is separate (it does extractive
# summarization and asks the operator); this budget drives the automatic,
# approval-free compaction, which is safe precisely because state is
# untouched.
DEFAULT_CONTEXT_BUDGET_TOKENS = 24000

# How many progress / learning entries the state block carries.
_PROGRESS_TAIL = 3
_LEARNINGS_TAIL = 3


def estimate_tokens(text: str) -> int:
    """Rough token estimate (char//4), consistent with _charge_tokens."""
    return max(1, len(text or "") // 4)


def _entry_tokens(entry: dict) -> int:
    return estimate_tokens(f"{entry.get('role', '')} {entry.get('text', '')}")


def context_size_tokens(loop) -> int:
    """Estimated tokens of the loop's current working context: the
    compiled contract block plus history plus the in-turn round
    transcript."""
    try:
        from contract import compile_contract
        turn_no = loop.state.snapshot.get("turn_count", 0) + 1
        block = compile_contract(loop.state, turn_no=turn_no)
    except Exception:  # noqa: BLE001 - an estimate must never break a turn
        block = ""
    total = estimate_tokens(block)
    total += sum(_entry_tokens(h) for h in (loop.history or []))
    total += sum(_entry_tokens(h) for h in (loop._round_transcript or []))
    return total


def needs_compaction(loop, budget: int = DEFAULT_CONTEXT_BUDGET_TOKENS) -> bool:
    """True when the working context exceeds the budget."""
    return context_size_tokens(loop) > budget


def _criterion_lines(loop, search_dirs: list) -> list[str]:
    from contract import criterion_status
    s = loop.state.snapshot
    mission = s.get("mission") or {}
    lines = []
    for c in mission.get("done_criteria", []) or []:
        try:
            ok, label = criterion_status(c, s, loop.state.events,
                                         search_dirs or [], manual_ok=False)
        except Exception:  # noqa: BLE001 - a bad criterion must not
            # break compaction; report it unmet.
            ok, label = False, f"unreadable criterion: {c!r}"[:120]
        lines.append(f"[{'x' if ok else ' '}] {label}")
    return lines


def _task_lines(loop) -> list[str]:
    marks = {"todo": "[ ]", "doing": "[~]", "done": "[x]"}
    lines = []
    for t in loop.state.snapshot.get("tasks", []) or []:
        mark = marks.get(t.get("status"), "[?]")
        notes = f" — {t['notes']}" if t.get("notes") else ""
        lines.append(f"{mark} {t.get('id')}: {t.get('title')}{notes}")
    return lines


def _approval_lines(loop) -> list[str]:
    lines = []
    for a in loop.state.snapshot.get("approvals", []) or []:
        if a.get("status") != "pending":
            continue
        args = a.get("args") or {}
        target = args.get("path") or args.get("cmd") or ""
        target = str(target)
        if len(target) > 80:
            target = target[:77] + "..."
        lines.append(f"{a.get('id')}: {a.get('tool')}"
                     + (f" {target}" if target else ""))
    return lines


def rebuild_context_from_state(loop, search_dirs: list | None = None) -> list[dict]:
    """Project the authoritative state into working-context entries.

    Pure function of (snapshot, events): deterministic, no side effects,
    never reads the old transcript. Returns a one-entry list holding the
    state block; ``compact()`` prepends it to the live tail.
    """
    s = loop.state.snapshot
    mission = s.get("mission") or {}
    ts = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    backend = type(getattr(loop, "backend", None)).__name__
    head = (f"[context rebuilt from authoritative state @ {ts} — "
            f"full detail remains in the journal]")
    lines = [head]
    if mission.get("text"):
        lines.append(f"MISSION (rev {s.get('mission_revision', 0)}): "
                     f"{mission['text']}")
    else:
        lines.append("MISSION: none")
    lines.append(f"PHASE: {s.get('phase')} | MODE: {s.get('mode')} | "
                 f"BINDING: {backend}")
    crit = _criterion_lines(loop, search_dirs or [])
    lines.append("CRITERIA:")
    lines.extend(crit if crit else ["(none)"])
    tasks = _task_lines(loop)
    lines.append("TASKS:")
    lines.extend(tasks if tasks else ["(none)"])
    approvals = _approval_lines(loop)
    lines.append("PENDING APPROVALS:")
    lines.extend(approvals if approvals else ["(none)"])
    progress = [p.get("delta", "") for p in (s.get("progress") or [])
                [-_PROGRESS_TAIL:]]
    lines.append("PROGRESS:")
    if progress:
        # Truncated like the live history entries (progress_delta[:300]):
        # the projection must stay bounded even when deltas are huge.
        # Full text remains in the journal.
        lines.extend(f"- {str(d)[:300]}" for d in progress if d)
    else:
        lines.append("(none)")
    questions = s.get("open_questions") or []
    lines.append("OPEN QUESTIONS:")
    if questions:
        lines.extend(f"- {q}" for q in questions)
    else:
        lines.append("(none)")
    learnings = [l.get("text", "") for l in (s.get("learnings") or [])
                 [-_LEARNINGS_TAIL:]]
    lines.append("LEARNINGS:")
    if learnings:
        lines.extend(f"- {t}" for t in learnings if t)
    else:
        lines.append("(none)")
    return [{"role": "system", "text": "\n".join(lines)}]


def compact(loop, budget: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
            by: str = "auto") -> dict:
    """Compact the working context down to authoritative state.

    Replaces ``loop.history`` with [state block] + live tail (most recent
    user message + in-progress round transcript, verbatim). The round
    transcript is live working memory and is never rewritten. Journals a
    ``context_compacted`` event (folds into no snapshot field) and fires
    the ``context_compacted`` hook. Returns a report dict.
    """
    old_history = list(loop.history or [])
    # Live tail: the most recent user message (verbatim) — everything
    # older is disposable conversation, fully journaled.
    tail: list[dict] = []
    for entry in reversed(old_history):
        if entry.get("role") == "user":
            tail = [copy.deepcopy(entry)]
            break
    search_dirs = []
    try:
        search_dirs = loop._search_dirs()
    except Exception:  # noqa: BLE001 - best effort only
        pass
    state_block = rebuild_context_from_state(loop, search_dirs)
    before = sum(_entry_tokens(h) for h in old_history)
    loop.history = state_block + tail
    after = sum(_entry_tokens(h) for h in loop.history)
    dropped = len(old_history) - len(tail)
    report = {"ok": True, "by": by,
              "entries_before": len(old_history),
              "entries_after": len(loop.history),
              "entries_dropped": dropped,
              "tokens_before": before, "tokens_after": after,
              "tokens_saved": max(0, before - after),
              "budget": budget}
    loop.state.record("context_compacted", {
        "by": by, "entries_dropped": dropped,
        "tokens_before": before, "tokens_after": after,
        "tokens_saved": report["tokens_saved"], "budget": budget})
    hooks = getattr(loop, "hooks", None)
    if hooks is not None:
        try:
            hooks.fire("context_compacted", dict(report))
        except Exception:  # noqa: BLE001 - hooks never break the loop
            pass
    return report


def maybe_compact(loop, source: str = "turn") -> dict | None:
    """Compact when over budget; otherwise a no-op returning None.

    ``source`` is "turn" (checked at turn start) or "round" (checked per
    round). The budget comes from ``loop.config["context_budget_tokens"]``.
    """
    try:
        budget = int(loop.config.get("context_budget_tokens",
                                     DEFAULT_CONTEXT_BUDGET_TOKENS))
    except (TypeError, ValueError):
        budget = DEFAULT_CONTEXT_BUDGET_TOKENS
    if not needs_compaction(loop, budget):
        return None
    return compact(loop, budget=budget, by=source)
