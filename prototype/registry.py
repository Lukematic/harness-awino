"""Memory registry (seeds v2): milestones, breadcrumbs, task tracker.

Auto-created at .awino/registry/ on the first mission in a project — no
manual step. It is the cross-session memory the mission loop reads from and
writes to:

  milestones  — decisions and completions, with dates (reflection writes
                these at mission close).
  breadcrumbs — open threads, parked questions, and the exact stop point per
                mission, so any session can resume mid-trail.
  tasks       — the task tracker: open/doing/done/blocked.

Existing .awino/seeds/*.md templates keep working alongside the registry;
seed_save also registers a seed's checklist tasks here.

`audit()` reports everything the registry believes, flagged as
fact / assumption / outdated, plus a plain-language summary.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

TASK_STATES = ("open", "doing", "done", "blocked")
MILESTONE_KINDS = ("decision", "completion")


def _now() -> float:
    return time.time()


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


_TASKS_SCHEMA_VERSION = 1
# tasks.json is a flat {task_id: task} dict with one reserved key:
# "schema_version". Task ids are "t-<hex>" so they never collide.

_REGISTRY_README = """# registry/

The memory registry for this project — auto-created on the first mission,
never by hand.

- `meta.json` — creation timestamp + schema_version.
- `milestones.jsonl` — one JSON object per line: decisions, completions, dates.
- `breadcrumbs.jsonl` — open threads, parked questions, exact stop points.
- `tasks.json` — the task DAG: {task_id: {text, state, depends_on,
  done_criteria, evidence, source, ts}}. `schema_version` is a reserved key.

The harness is the only writer. `registry audit` explains what the
registry believes, flagged as fact vs assumption vs outdated.
"""


class Registry:
    """File-backed registry at <awino_dir>/registry/."""

    def __init__(self, awino_dir: str | Path):
        self.awino_dir = Path(awino_dir)
        self.dir = self.awino_dir / "registry"

    # ------------------------------------------------------------- lifecycle
    def ensure(self) -> dict:
        """Create the registry on first use. Idempotent. Never raises."""
        created = not self.dir.is_dir()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            meta = self.dir / "meta.json"
            if not meta.is_file():
                _atomic_write(meta, json.dumps(
                    {"schema_version": 1, "created_ts": _now(),
                     "version": 1}, indent=2))
            for name, default in (("milestones.jsonl", ""),
                                  ("breadcrumbs.jsonl", ""),
                                  ("tasks.json",
                                   json.dumps({"schema_version":
                                               _TASKS_SCHEMA_VERSION}))):
                p = self.dir / name
                if not p.is_file():
                    p.write_text(default)
            readme = self.dir / "README.md"
            if not readme.is_file():
                readme.write_text(_REGISTRY_README)
        except OSError as e:
            return {"created": False, "error": str(e)}
        return {"created": created, "dir": str(self.dir)}

    @property
    def exists(self) -> bool:
        return (self.dir / "meta.json").is_file()

    # ------------------------------------------------------------ milestones
    def add_milestone(self, kind: str, text: str,
                      assumption: bool = False,
                      ts: float | None = None) -> dict:
        if kind not in MILESTONE_KINDS:
            raise ValueError(f"milestone kind must be one of {MILESTONE_KINDS}")
        item = {"id": "ms-" + _uid(), "kind": kind, "text": text,
                "assumption": assumption, "ts": ts if ts is not None else _now()}
        with open(self.dir / "milestones.jsonl", "a") as f:
            f.write(json.dumps(item) + "\n")
        return item

    def milestones(self) -> list[dict]:
        p = self.dir / "milestones.jsonl"
        if not p.is_file():
            return []
        out = []
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # ----------------------------------------------------------- breadcrumbs
    def add_breadcrumb(self, mission_id: str, note: str, stop_point: str = "",
                       ts: float | None = None) -> dict:
        item = {"id": "bc-" + _uid(), "mission_id": mission_id, "note": note,
                "stop_point": stop_point,
                "ts": ts if ts is not None else _now()}
        with open(self.dir / "breadcrumbs.jsonl", "a") as f:
            f.write(json.dumps(item) + "\n")
        return item

    def breadcrumbs(self, mission_id: str | None = None) -> list[dict]:
        p = self.dir / "breadcrumbs.jsonl"
        if not p.is_file():
            return []
        out = []
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if mission_id is None or item.get("mission_id") == mission_id:
                out.append(item)
        return out

    def last_stop_point(self, mission_id: str) -> str:
        """Exact stop point of a mission: where to resume."""
        crumbs = self.breadcrumbs(mission_id)
        for c in reversed(crumbs):
            if c.get("stop_point"):
                return c["stop_point"]
        return ""

    # ----------------------------------------------------------------- tasks
    def _load_tasks(self) -> dict:
        p = self.dir / "tasks.json"
        try:
            data = json.loads(p.read_text())
            if not isinstance(data, dict):
                return {}
            # tolerate pre-schema files; never surface the reserved key
            return {k: v for k, v in data.items()
                    if k != "schema_version"}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_tasks(self, tasks: dict) -> None:
        _atomic_write(self.dir / "tasks.json",
                      json.dumps({"schema_version": _TASKS_SCHEMA_VERSION,
                                  **tasks}, indent=2, sort_keys=True))

    def add_task(self, text: str, source: str = "manual",
                 state: str = "open", ts: float | None = None,
                 depends_on: list[str] | None = None,
                 done_criteria: str = "", evidence: list[str] | None = None,
                 **extra) -> dict:
        if state not in TASK_STATES:
            raise ValueError(f"task state must be one of {TASK_STATES}")
        tasks = self._load_tasks()
        deps = [d for d in (depends_on or []) if d]
        unknown = [d for d in deps if d not in tasks]
        if unknown:
            raise ValueError(
                f"cannot add task: depends_on unknown task id(s): "
                f"{', '.join(unknown)} — list task ids first, then link them")
        ts = ts if ts is not None else _now()
        task = {"id": "t-" + _uid(), "text": text, "state": state,
                "source": source, "ts": ts,
                "depends_on": deps,
                "done_criteria": done_criteria,
                "evidence": list(evidence or []),
                "history": [{"state": state, "ts": ts}]}
        for k, v in extra.items():
            if k not in task:
                task[k] = v
        tasks[task["id"]] = task
        self._save_tasks(tasks)
        return task

    def set_task_state(self, task_id: str, state: str,
                       evidence: list[str] | None = None) -> dict:
        if state not in TASK_STATES:
            raise ValueError(f"task state must be one of {TASK_STATES}")
        tasks = self._load_tasks()
        if task_id not in tasks:
            raise KeyError(f"no such task: {task_id}")
        task = tasks[task_id]
        if evidence:
            task.setdefault("evidence", []).extend(evidence)
        if state == "done":
            unmet = [d for d in task.get("depends_on", [])
                     if tasks.get(d, {}).get("state") != "done"]
            if unmet:
                raise ValueError(
                    f"task {task_id} is blocked: depends on unfinished "
                    f"task(s) {', '.join(unmet)}. Finish those first — "
                    f"dependencies propagate, you cannot skip the chain.")
        task["state"] = state
        task["history"].append({"state": state, "ts": _now()})
        self._save_tasks(tasks)
        return task

    # ------------------------------------------------------- DAG (Track F)
    def topological_order(self) -> list[str]:
        """Task ids in dependency order (Kahn's algorithm).

        Raises ValueError naming the cycle members when the DAG has one —
        cycles are a harness bug, never silently ignored.
        """
        tasks = self._load_tasks()
        indeg = {tid: 0 for tid in tasks}
        children: dict[str, list[str]] = {tid: [] for tid in tasks}
        for tid, t in tasks.items():
            for d in t.get("depends_on", []):
                if d in tasks:
                    children[d].append(tid)
                    indeg[tid] += 1
        ready = sorted([tid for tid, n in indeg.items() if n == 0],
                       key=lambda tid: tasks[tid]["ts"])
        order: list[str] = []
        while ready:
            tid = ready.pop(0)
            order.append(tid)
            for c in sorted(children[tid], key=lambda x: tasks[x]["ts"]):
                indeg[c] -= 1
                if indeg[c] == 0:
                    ready.append(c)
        if len(order) != len(tasks):
            stuck = sorted(t for t in tasks if t not in order)
            raise ValueError(
                "task DAG has a dependency cycle involving: "
                + ", ".join(stuck) + " — break the cycle before continuing")
        return order

    def whats_next(self, limit: int = 5) -> list[dict]:
        """Unblocked work only: open tasks whose dependencies are all done."""
        tasks = self._load_tasks()
        out = []
        for t in self.tasks():
            if t["state"] != "open":
                continue
            if all(tasks.get(d, {}).get("state") == "done"
                   for d in t.get("depends_on", [])):
                out.append(t)
        return out[:limit]

    def blocked_by(self, task_id: str) -> list[dict]:
        """Which unfinished tasks block this one (direct dependencies)."""
        tasks = self._load_tasks()
        if task_id not in tasks:
            raise KeyError(f"no such task: {task_id}")
        return [tasks[d] for d in tasks[task_id].get("depends_on", [])
                if d in tasks and tasks[d].get("state") != "done"]

    def unblock_report(self) -> list[dict]:
        """Every non-done task that is currently blocked, and by what."""
        out = []
        for t in self.tasks():
            if t["state"] in ("done",):
                continue
            blockers = self.blocked_by(t["id"])
            if blockers:
                out.append({"task": t,
                            "blocked_by": [{"id": b["id"], "text": b["text"],
                                            "state": b["state"]}
                                           for b in blockers]})
        return out

    def dag_summary(self) -> dict:
        """Counts for dashboards and the contract block."""
        tasks = self.tasks()
        return {
            "total": len(tasks),
            "done": sum(1 for t in tasks if t["state"] == "done"),
            "doing": sum(1 for t in tasks if t["state"] == "doing"),
            "open": sum(1 for t in tasks if t["state"] == "open"),
            "blocked": len(self.unblock_report()),
            "next": [t["id"] for t in self.whats_next()],
        }

    def tasks(self, state: str | None = None) -> list[dict]:
        all_tasks = list(self._load_tasks().values())
        if state is not None:
            all_tasks = [t for t in all_tasks if t["state"] == state]
        return sorted(all_tasks, key=lambda t: t["ts"])

    def import_seed_tasks(self, seed_tasks: list[dict]) -> int:
        """Import checklist tasks from seed files; dedupe by (text, source).

        seed_tasks: [{"text", "done" (bool), "source"}] from
        bootstrap.collect_seed_tasks. Done items import as done — but a
        checked box is human attestation, not harness verification, so done
        imports carry an evidence entry saying exactly that. Without it the
        Tasks panel (which presents completed states as verified) would
        mislead.
        """
        existing = {(t["text"], t["source"]) for t in self.tasks()}
        n = 0
        for st in seed_tasks:
            key = (st["text"], st.get("source", "seed"))
            if key in existing:
                continue
            done = bool(st.get("done"))
            source = st.get("source", "seed")
            evidence = ([f"{source} checklist [x] (human attestation, "
                         "not verified)"] if done else None)
            self.add_task(st["text"], source=source,
                          state="done" if done else "open",
                          evidence=evidence)
            existing.add(key)
            n += 1
        return n

    # ----------------------------------------------------------------- audit
    def audit(self, outdated_days: float = 30) -> dict:
        """Report everything the registry believes.

        Each item is flagged fact / assumption / outdated, plus a
        plain-language summary. `outdated_days` bounds "stale but not done".
        """
        cutoff = _now() - outdated_days * 86400
        items: list[dict] = []

        def flag(ts: float, assumption: bool, terminal: bool) -> str:
            if assumption:
                return "assumption"
            if not terminal and ts < cutoff:
                return "outdated"
            return "fact"

        for m in self.milestones():
            items.append({"kind": "milestone", "text": m["text"],
                          "status": flag(m["ts"], m.get("assumption", False),
                                         True),
                          "ts": m["ts"]})
        for t in self.tasks():
            terminal = t["state"] in ("done",)
            items.append({"kind": f"task:{t['state']}", "text": t["text"],
                          "status": flag(t["ts"], False, terminal),
                          "ts": t["ts"]})
        for b in self.breadcrumbs():
            items.append({"kind": "breadcrumb", "text": b["note"],
                          "status": flag(b["ts"], False, False),
                          "ts": b["ts"]})

        counts = {"fact": 0, "assumption": 0, "outdated": 0}
        for i in items:
            counts[i["status"]] += 1
        open_tasks = [t for t in self.tasks() if t["state"] == "open"]
        doing = [t for t in self.tasks() if t["state"] == "doing"]
        parts = [
            f"The registry holds {len(items)} items: "
            f"{counts['fact']} verified facts, "
            f"{counts['assumption']} flagged assumptions, "
            f"{counts['outdated']} possibly outdated."]
        if open_tasks:
            parts.append(f"{len(open_tasks)} tasks still open"
                         + (f" (oldest: {open_tasks[0]['text'][:80]})"
                            if open_tasks else "") + ".")
        if doing:
            parts.append(f"{len(doing)} in progress: "
                         + ", ".join(t["text"][:60] for t in doing[:3]) + ".")
        if counts["assumption"]:
            parts.append("Review the flagged assumptions — they were "
                         "recorded as guesses, not evidence.")
        if counts["outdated"]:
            parts.append("Some items are older than "
                         f"{outdated_days:g} days without progress — "
                         "confirm or drop them.")
        return {"items": items, "counts": counts,
                "summary": " ".join(parts)}

    # ------------------------------------------------------- contract section
    def contract_section(self) -> str:
        """Registry summary for the compiled contract block. "" when empty."""
        if not self.exists:
            return ""
        lines = []
        open_tasks = self.tasks(state="open") + self.tasks(state="doing")
        blocked = self.tasks(state="blocked")
        crumbs = self.breadcrumbs()
        miles = self.milestones()
        if not (open_tasks or blocked or crumbs or miles):
            return ""
        lines.append("## REGISTRY (harness-owned memory — milestones, "
                     "breadcrumbs, tasks)")
        if miles:
            lines.append("Milestones:")
            for m in miles[-5:]:
                lines.append(f"- [{m['kind']}] {m['text']}")
        # Track F: DAG progress summary (done/doing/open/blocked counts).
        dag = self.dag_summary()
        if dag["total"]:
            lines.append(
                f"DAG progress: {dag['done']} done / {dag['doing']} doing / "
                f"{dag['open']} open / {dag['blocked']} blocked "
                f"(total {dag['total']})")
            nxt = self.whats_next()
            if nxt:
                lines.append("What's next (unblocked):")
                for t in nxt:
                    lines.append(f"- {t['text']} (id: {t['id']})")
        if miles:
            lines.append("Milestones:")
            for m in miles[-5:]:
                lines.append(f"- [{m['kind']}] {m['text']}")
        if open_tasks:
            lines.append("Open tasks:")
            for t in open_tasks[:10]:
                lines.append(f"- [{t['state']}] {t['text']} "
                             f"(id: {t['id']}, from: {t['source']})")
        if blocked:
            lines.append("Blocked:")
            for t in blocked[:5]:
                lines.append(f"- {t['text']} (id: {t['id']})")
        if crumbs:
            lines.append("Breadcrumbs (latest stop points):")
            for b in crumbs[-3:]:
                sp = f" — stopped at: {b['stop_point']}" if b.get("stop_point") else ""
                lines.append(f"- {b['note']}{sp}")
        return "\n".join(lines)
