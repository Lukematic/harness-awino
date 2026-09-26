"""Receipts: every closed story ends with promise -> proof -> lesson.

A receipt is built from what the harness already recorded, never from what
the model says about itself:

  promise  the story as planned: problem, done criteria, ordered steps with
           their success/failure criteria and (optional) time forecasts,
           the Bugatti pitched in brief.
  proof    what actually happened in the journal while the story was open:
           check commands and their exit codes, verifier verdicts (and who
           gave them), files written, approvals, DAG tasks with evidence,
           commits on the story branch, and the journal head hash.
  lesson   forecast vs actual per step and in total, failed verifications,
           doom loops, and done criteria nothing proved.

It is honest by construction: a story closed without a passing verdict is
labelled UNVERIFIED, and an unproven criterion is listed as unproven.
Receipts are written to <awino_dir>/registry/receipts/<story_id>.json and
.md; the .md doubles as a pull-request description.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

WRITE_TOOLS = ("write_file", "patch_file")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_forecast(text: str) -> float | None:
    """'30m', '2h', '1h30m', '45 min', '1.5h', '2 days' -> seconds.
    None when there is no parseable duration."""
    s = (text or "").strip().lower()
    if not s:
        return None
    total, hit = 0.0, False
    for num, unit in re.findall(
            r"(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|"
            r"seconds?|secs?|s)(?![a-z])", s):
        total += float(num) * _UNIT[unit[0]]
        hit = True
    return total if hit and total > 0 else None


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, _ = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}d {h}h"
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _day(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


# ---------------------------------------------------------------- proof
def _window(events: list[dict], start: float, end: float) -> list[dict]:
    return [e for e in events or []
            if isinstance(e, dict) and start <= e.get("ts", 0) <= end]


def _checks(events: list[dict]) -> list[dict]:
    """run_command results, newest result per command kept, in order."""
    by_cmd: dict[str, dict] = {}
    for e in events:
        d = e.get("data") or {}
        if e.get("type") != "tool_result" or d.get("tool") != "run_command":
            continue
        res = d.get("result") if isinstance(d.get("result"), dict) else {}
        if "exit_code" not in res:
            continue
        cmd = str((d.get("args") or {}).get("cmd") or "?")
        row = by_cmd.setdefault(cmd, {"cmd": cmd, "runs": 0, "failures": 0})
        row["runs"] += 1
        row["failures"] += 1 if res.get("exit_code") != 0 else 0
        row["last_exit"] = res.get("exit_code")
        row["ts"] = e.get("ts")
    return list(by_cmd.values())


def _verdicts(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        d = e.get("data") or {}
        if e.get("type") == "verify_passed":
            wid = d.get("worker_id")
            by = ("independent verifier" if wid not in (None, "harness")
                  else "harness self-check")
            criteria = []
            if isinstance(d.get("verdict"), list):
                criteria = [{"criterion": str(v.get("criterion", "")),
                             "accomplished": v.get("accomplished"),
                             "proof": v.get("proof_link", "")}
                            for v in d["verdict"] if isinstance(v, dict)]
            out.append({"passed": True, "by": by, "ts": e.get("ts"),
                        "criteria": criteria,
                        "note": d.get("note") or d.get("via") or ""})
        elif e.get("type") == "verify_failed":
            out.append({"passed": False, "by": d.get("source") or "verifier",
                        "ts": e.get("ts"),
                        "reason": str(d.get("reason", ""))[:200]})
    return out


def _files(events: list[dict]) -> list[str]:
    seen: list[str] = []
    for e in events:
        d = e.get("data") or {}
        if e.get("type") != "tool_result" or d.get("tool") not in WRITE_TOOLS:
            continue
        res = d.get("result")
        if isinstance(res, dict) and res.get("error"):
            continue
        path = str((d.get("args") or {}).get("path") or "")
        if path and path not in seen:
            seen.append(path)
    return seen


def _commits(project_root: Path, branch: str, since: float) -> list[str]:
    """Commits on the story branch since it opened. Best-effort: [] when
    git or the branch is absent."""
    try:
        r = subprocess.run(
            ["git", "log", branch, f"--since=@{int(since)}",
             "--format=%h %s", "-n", "30"],
            cwd=str(project_root), capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return []
        return [ln for ln in r.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def _step_actuals(awino_dir: Path, story: dict) -> list[dict]:
    """Per step: the DAG task's state, evidence and wall time. Start is
    the first 'doing' stamp, else when the previous step finished, else
    when the task was seeded; end is the 'done' stamp."""
    try:
        from registry import Registry
        tasks = [t for t in Registry(awino_dir).tasks()
                 if t.get("story_id") == story["id"]
                 and isinstance(t.get("step_index"), int)]
    except Exception:
        tasks = []
    by_index = {t["step_index"]: t for t in tasks}
    rows, prev_done = [], None
    for i, st in enumerate(story.get("steps") or []):
        t = by_index.get(i) or {}
        hist = t.get("history") or []
        doing = next((h["ts"] for h in hist if h.get("state") == "doing"), None)
        done = next((h["ts"] for h in reversed(hist)
                     if h.get("state") == "done"), None)
        start = doing or prev_done or t.get("ts")
        actual = (done - start) if (done and start and done >= start) else None
        forecast_s = st.get("forecast_s")
        if forecast_s is None and st.get("forecast"):
            forecast_s = parse_forecast(st["forecast"])
        rows.append({"index": i, "title": st.get("title", ""),
                     "success": st.get("success", ""),
                     "failure": st.get("failure", ""),
                     "forecast": st.get("forecast", ""),
                     "forecast_s": forecast_s,
                     "state": t.get("state", "not seeded"),
                     "evidence": list(t.get("evidence") or []),
                     "actual_s": actual})
        prev_done = done or prev_done
    return rows


# ---------------------------------------------------------------- build
def build_receipt(awino_dir: str | Path, story_id: str, *,
                  events: list[dict] | None = None,
                  chain_ok: bool | None = None,
                  project_root: str | Path | None = None) -> dict:
    """Assemble the receipt for one story. `events` is the loop journal
    (state.events); without it the proof section only has what the
    registry and git hold. Raises KeyError for an unknown story."""
    from story import StoryStore, story_time_spent
    awd = Path(awino_dir)
    story = StoryStore(awd).get(story_id)
    opened = story.get("created_ts") or 0
    closed = story.get("closed_ts") or time.time()
    win = _window(events or [], opened, closed)

    steps = _step_actuals(awd, story)
    verdicts = _verdicts(win)
    passed = [v for v in verdicts if v["passed"]]
    checks = _checks(win)
    head = next((e.get("hash") for e in reversed(win) if e.get("hash")), "")

    # Which done criteria did a verifier verdict actually name?
    named = " ".join(c["criterion"].lower() for v in passed
                     for c in v.get("criteria", [])
                     if c.get("accomplished") == "yes")
    # A verdict that lists criteria only proves the ones it names; a pass
    # with no per-criterion list (auto-verify) covers the mission as a whole.
    itemized = any(v.get("criteria") for v in passed)
    criteria = []
    for c in story.get("done_criteria") or []:
        text = c if isinstance(c, str) else json.dumps(c)
        if named and text.lower()[:60] in named:
            how = "named in verifier verdict"
        elif passed and not itemized:
            how = "covered by passing verification"
        else:
            how = "unproven"
        criteria.append({"criterion": text, "proof": how})
    unproven = [c["criterion"] for c in criteria if c["proof"] == "unproven"]

    if not passed:
        status = "UNVERIFIED"
    elif unproven:
        status = "PARTLY PROVEN"
    elif any(v["by"] == "independent verifier" for v in passed):
        status = "PROVEN"
    else:
        status = "SELF-CHECKED"

    fc = [s["forecast_s"] for s in steps if s["forecast_s"]]
    ac = [s["actual_s"] for s in steps
          if s["forecast_s"] and s["actual_s"] is not None]
    total_forecast = sum(fc) if fc else None
    total_actual = sum(ac) if ac and len(ac) == len(fc) else None
    ratio = (round(total_actual / total_forecast, 2)
             if total_forecast and total_actual is not None else None)
    lessons = []
    if ratio is not None:
        if ratio > 1.25:
            lessons.append(f"Took {ratio}x the forecast "
                           f"({_fmt(total_actual)} vs {_fmt(total_forecast)}).")
        elif ratio < 0.8:
            lessons.append(f"Finished in {ratio}x the forecast "
                           f"({_fmt(total_actual)} vs {_fmt(total_forecast)}).")
        else:
            lessons.append(f"Forecast held ({_fmt(total_actual)} vs "
                           f"{_fmt(total_forecast)}).")
    for s in steps:
        if s["forecast_s"] and s["actual_s"] and \
                s["actual_s"] > 2 * s["forecast_s"]:
            lessons.append(f"Step {s['index'] + 1} '{s['title']}' ran "
                           f"{round(s['actual_s'] / s['forecast_s'], 1)}x "
                           f"its forecast.")
    fails = [v for v in verdicts if not v["passed"]]
    if fails:
        lessons.append(f"{len(fails)} failed verification(s) before close.")
    loops = [e for e in win if e.get("type") == "doom_loop_detected"]
    if loops:
        lessons.append(f"Three-strike breaker fired {len(loops)} time(s).")
    if unproven:
        lessons.append(f"{len(unproven)} done criteria closed without proof.")
    if not fc and steps:
        lessons.append("No forecasts were given, so nothing to calibrate "
                       "against next time.")

    root = Path(project_root) if project_root else awd.parent
    return {
        "schema": 1,
        "story": {"id": story["id"], "title": story.get("title", ""),
                  "type": story.get("type", "story"),
                  "branch": story.get("branch", ""),
                  "outcome": story.get("outcome", ""),
                  "opened_ts": opened, "closed_ts": story.get("closed_ts"),
                  "time_s": story_time_spent(awd, story_id)},
        "status": status,
        "promise": {"problem": story.get("problem", ""),
                    "done_criteria": story.get("done_criteria") or [],
                    "bugatti_brief": story.get("bugatti_brief", "")},
        "proof": {"criteria": criteria, "steps": steps, "checks": checks,
                  "verdicts": verdicts, "files": _files(win),
                  "approvals": sum(1 for e in win
                                   if e.get("type") == "approval_requested"),
                  "commits": _commits(root, story.get("branch", ""), opened)
                  if story.get("branch") else [],
                  "journal": {"events": len(win), "head": head,
                              "chain_ok": chain_ok}},
        "lesson": {"forecast_s": total_forecast, "actual_s": total_actual,
                   "ratio": ratio, "notes": lessons},
    }


# ---------------------------------------------------------------- render
def render_receipt_md(r: dict) -> str:
    """Markdown receipt, written to paste as a pull-request description."""
    s, p, pr, le = r["story"], r["promise"], r["proof"], r["lesson"]
    out = [f"## {s['title']}", "",
           f"**Receipt: {r['status']}** · {s['type']} · "
           f"`{s['branch']}` · opened {_day(s['opened_ts'])} · "
           f"closed {_day(s['closed_ts'])} · time "
           f"{_fmt(s['time_s']) if (s['time_s'] or 0) >= 1 else 'not tracked'}", ""]
    if s.get("outcome"):
        out += [f"**Outcome.** {s['outcome']}", ""]
    out += ["### Promise", ""]
    if p.get("problem"):
        out += [p["problem"], ""]
    if pr["criteria"]:
        out += ["| Done criterion | Proof |", "|---|---|"]
        out += [f"| {c['criterion']} | {c['proof']} |" for c in pr["criteria"]]
        out.append("")
    if pr["steps"]:
        out += ["| # | Step | Forecast | Actual | State | Evidence |",
                "|---|---|---|---|---|---|"]
        for st in pr["steps"]:
            ev = ", ".join(f"`{x}`" for x in st["evidence"]) or "—"
            out.append(f"| {st['index'] + 1} | {st['title']} | "
                       f"{st['forecast'] or '—'} | {_fmt(st['actual_s'])} | "
                       f"{st['state']} | {ev} |")
        out.append("")
    out += ["### Proof", ""]
    if pr["checks"]:
        for c in pr["checks"]:
            mark = "pass" if c.get("last_exit") == 0 else "FAIL"
            out.append(f"- `{c['cmd']}` → exit {c.get('last_exit')} ({mark}); "
                       f"{c['runs']} run(s), {c['failures']} failed")
    else:
        out.append("- No check commands were run while this story was open.")
    for v in pr["verdicts"]:
        if v["passed"]:
            out.append(f"- Verification passed ({v['by']}, {_day(v['ts'])})")
        else:
            out.append(f"- Verification failed ({v['by']}): {v['reason']}")
    if pr["files"]:
        out.append(f"- Files written: {', '.join(f'`{f}`' for f in pr['files'][:20])}")
    if pr["commits"]:
        out.append(f"- Commits ({len(pr['commits'])}):")
        out += [f"  - {c}" for c in pr["commits"][:15]]
    j = pr["journal"]
    chain = {True: "intact", False: "BROKEN", None: "not checked"}[j["chain_ok"]]
    head = f"`{j['head'][:12]}`" if j["head"] else "—"
    out += [f"- Journal: {j['events']} events, head {head}, chain {chain}; "
            f"{pr['approvals']} approval(s)", "", "### Lesson", ""]
    out += [f"- {n}" for n in le["notes"]] or ["- Nothing notable."]
    if p.get("bugatti_brief"):
        out += ["", f"**Bugatti (pitched, not built).** {p['bugatti_brief']}"]
    out += ["", "_Receipt generated by A.W.I.N.O. from the session journal._"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- store
def receipts_dir(awino_dir: str | Path) -> Path:
    return Path(awino_dir) / "registry" / "receipts"


def write_receipt(awino_dir: str | Path, receipt: dict) -> Path:
    d = receipts_dir(awino_dir)
    d.mkdir(parents=True, exist_ok=True)
    sid = receipt["story"]["id"]
    (d / f"{sid}.json").write_text(json.dumps(receipt, indent=2, default=str))
    md = d / f"{sid}.md"
    md.write_text(render_receipt_md(receipt))
    return md


def load_receipt(awino_dir: str | Path, story_id: str) -> dict | None:
    p = receipts_dir(awino_dir) / f"{story_id}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def calibration_history(awino_dir: str | Path, limit: int = 10) -> list[dict]:
    """Forecast/actual ratios from past receipts, newest first — what a
    challenge can cite ("your last 3 stories ran 2x the forecast")."""
    d = receipts_dir(awino_dir)
    rows = []
    for p in d.glob("*.json") if d.is_dir() else []:
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("lesson", {}).get("ratio") is not None:
            rows.append({"story_id": r["story"]["id"],
                         "title": r["story"]["title"],
                         "closed_ts": r["story"].get("closed_ts") or 0,
                         "ratio": r["lesson"]["ratio"]})
    rows.sort(key=lambda x: x["closed_ts"], reverse=True)
    return rows[:limit]
