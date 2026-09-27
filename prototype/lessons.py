"""Lessons: what closed stories taught, fed back into every later turn.

Inspired by autoharness (learn from real work, merge instead of pile up,
keep the library in front of the model, prune by use), with one difference:
Awino has receipts, so a lesson is judged by OUTCOMES, not by whether it
was loaded.

  learn     Each receipt yields lessons deterministically: steps that ran
            well past their forecast, checks that failed before passing,
            three-strike loops, criteria closed without proof. No model
            call, no prose invented.
  merge     A lesson has a key (kind + subject). The same key again
            reinforces the existing lesson (count, evidence, ledger line)
            instead of adding a near-duplicate.
  surface   The live lessons are written into the turn contract
            (## LESSONS), bounded to a few lines, newest trouble first.
  judge     Each later receipt is the test. A lesson whose problem recurs
            after it was shown is ESCALATED (it becomes a challenge in
            planning). One shown across LEARNED_AFTER closed stories
            without recurring is LEARNED and leaves the index. Nothing is
            deleted: a learned lesson that recurs comes back live.

Every change is appended to the lesson's ledger with the receipt that
caused it. Stored at <awino_dir>/lessons/lessons.json.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

LEARNED_AFTER = 3      # closed stories shown without recurrence -> learned
INDEX_LIMIT = 6        # lines in the turn contract
OVERRUN = 2.0          # step actual / forecast that counts as a miss


def _store_path(awino_dir) -> Path:
    return Path(awino_dir) / "lessons" / "lessons.json"


def load(awino_dir) -> dict:
    try:
        data = json.loads(_store_path(awino_dir).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(awino_dir, lessons: dict) -> None:
    p = _store_path(awino_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(lessons, indent=2, sort_keys=True))
    tmp.replace(p)


def _norm(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "-".join(w for w in words if len(w) > 2)[:48] or "general"


def extract(receipt: dict) -> list[dict]:
    """Lesson candidates from one receipt: [{key, kind, text}]."""
    out = []
    pr = receipt.get("proof", {})
    for st in pr.get("steps", []):
        f, a = st.get("forecast_s"), st.get("actual_s")
        if f and a and a > OVERRUN * f:
            out.append({
                "key": f"forecast:{_norm(st.get('title'))}",
                "kind": "forecast",
                "text": (f"Steps like '{st.get('title')}' ran "
                         f"{a / f:.1f}x the forecast ({st.get('forecast')}). "
                         f"Forecast higher or split the step.")})
    for c in pr.get("checks", []):
        if c.get("failures"):
            out.append({
                "key": f"check:{c.get('cmd')}",
                "kind": "check",
                "text": (f"`{c.get('cmd')}` failed {c['failures']} of "
                         f"{c.get('runs')} run(s) before the story closed. "
                         f"Run it before claiming a step is done.")})
    loops = [n for n in receipt.get("lesson", {}).get("notes", [])
             if "Three-strike" in n]
    if loops:
        out.append({"key": "doom-loop", "kind": "loop",
                    "text": ("The three-strike breaker fired. When a fix "
                             "fails twice the same way, rethink the approach "
                             "before a third patch.")})
    unproven = [c["criterion"] for c in pr.get("criteria", [])
                if c.get("proof") == "unproven"]
    if unproven:
        out.append({"key": "proof:unproven", "kind": "proof",
                    "text": (f"Stories closed with unproven criteria (last: "
                             f"'{unproven[0][:80]}'). Make every done "
                             f"criterion checkable before BUILD.")})
    return out


def _ledger(lesson: dict, action: str, reason: str, evidence: str) -> None:
    lesson.setdefault("ledger", []).append(
        {"ts": time.time(), "action": action, "reason": reason,
         "evidence": evidence})


def learn_from_receipt(awino_dir, receipt: dict,
                       evidence: str = "") -> dict:
    """Fold one closed story's receipt into the lesson store.

    Returns {"created": [...], "reinforced": [...], "escalated": [...],
    "learned": [...], "revived": [...]} (lesson keys)."""
    lessons = load(awino_dir)
    sid = receipt.get("story", {}).get("id", "?")
    title = receipt.get("story", {}).get("title", "")
    evidence = evidence or f"receipt:{sid}"
    found = {c["key"]: c for c in extract(receipt)}
    res = {k: [] for k in ("created", "reinforced", "escalated", "learned",
                           "revived")}
    for key, cand in found.items():
        cur = lessons.get(key)
        if cur is None:
            cur = lessons[key] = {"key": key, "kind": cand["kind"],
                                  "text": cand["text"], "status": "live",
                                  "seen": 1, "shown": 0, "clean": 0,
                                  "evidence": [evidence],
                                  "created_ts": time.time()}
            _ledger(cur, "create", f"story '{title}'", evidence)
            res["created"].append(key)
            continue
        cur["seen"] = cur.get("seen", 0) + 1
        cur["text"] = cand["text"]
        cur["clean"] = 0
        cur["evidence"] = (cur.get("evidence", []) + [evidence])[-10:]
        if cur.get("status") == "learned":
            cur["status"] = "live"
            _ledger(cur, "revive", f"recurred in '{title}'", evidence)
            res["revived"].append(key)
        elif cur.get("shown", 0) > 0:
            cur["status"] = "escalated"
            _ledger(cur, "escalate",
                    f"recurred in '{title}' after being shown "
                    f"{cur['shown']} time(s)", evidence)
            res["escalated"].append(key)
        else:
            _ledger(cur, "reinforce", f"again in '{title}'", evidence)
            res["reinforced"].append(key)
    # Outcome-based graduation: shown, and this story did not repeat it.
    for key, cur in lessons.items():
        if key in found or cur.get("status") == "learned":
            continue
        if cur.get("shown", 0) > 0:
            cur["clean"] = cur.get("clean", 0) + 1
            if cur["clean"] >= LEARNED_AFTER:
                cur["status"] = "learned"
                _ledger(cur, "learned",
                        f"{cur['clean']} closed stories without recurring",
                        evidence)
                res["learned"].append(key)
    _save(awino_dir, lessons)
    return res


def live(awino_dir) -> list[dict]:
    """Live lessons, escalated first, then most often seen."""
    rows = [l for l in load(awino_dir).values()
            if l.get("status") in ("live", "escalated")]
    rows.sort(key=lambda l: (l.get("status") != "escalated",
                             -l.get("seen", 0), -l.get("created_ts", 0)))
    return rows


def index_lines(awino_dir, limit: int = INDEX_LIMIT) -> list[str]:
    out = []
    for l in live(awino_dir)[:limit]:
        tag = "ESCALATED" if l["status"] == "escalated" else f"x{l['seen']}"
        out.append(f"[{tag}] {l['text']}")
    return out


def mark_shown(awino_dir, keys: list[str] | None = None) -> None:
    """A session (or story) saw these lessons: from here, a recurrence is
    a lesson not taken, and a clean close counts toward LEARNED_AFTER."""
    lessons = load(awino_dir)
    want = set(keys) if keys is not None else {
        l["key"] for l in live(awino_dir)[:INDEX_LIMIT]}
    for k in want:
        if k in lessons:
            lessons[k]["shown"] = lessons[k].get("shown", 0) + 1
    if want:
        _save(awino_dir, lessons)
