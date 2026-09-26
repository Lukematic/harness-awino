"""STORY.md story ledger — the Jira-shaped story layer.

One STORY.md per project, at the project root (visible — it is also the
brag board). Every piece of work is a story:

  title, type (story | spike | chore), status (open | doing | blocked | done),
  problem, description, approach (the spine — written during planning),
  done criteria ("how we'll know it's done"), blockers, linked seeds
  (a story can own multiple seeds), branch name, session log (every
  session that touched it), closed date + outcome.

Single source of truth: the registry. Stories live in
.awino/registry/stories.json (same flat-dict shape as tasks.json);
STORY.md is RENDERED from it on every mutation — never a parallel store.
The generated file carries a header saying so; hand-edits are overwritten.

Mandatory mechanics (in code, not prompts):
  - session start: bootstrap.session_start_auto_init emits a stories_review
    listing open/doing/blocked stories — the session must present
    "these are the open stories, which do we work on / close?" first.
  - story_start: creates the entry, renders STORY.md, creates git branch
    story/<slug> (best-effort; warns, never fails).
  - planning gate: loop.request_phase refuses ->BUILD when the active
    (doing) story's problem/approach/done-criteria are empty.
  - seeds: seeds/*.md frontmatter takes an optional `story:` field;
    unlinked seeds auto-file under an "Inbox" story — never orphaned.
  - finish ritual: a passing verifier verdict journals story_ready_to_close
    and the harness ASKS the user to close (close authority stays human).
  - stale-story rule: story_start (or a new mission) while another story is
    doing/open journals `stale_stories` — the session must surface
    "we started this new thing, but X is still open — what's up?"
  - review nudge: every STORIES_NUDGE_EVERY user turns, open non-blocked
    stories untouched this session journal `stories_nudge`.
  - effort: session entries carry start/end timestamps; wall time
    accumulates per story and renders as "time dedicated".
  - planning: plan_story writes the approach with a REQUIRED six-part
    shape — (a) first-principles breakdown, (b) existing approaches, (c)
    user guidance, (d) proposed pitch (A/B/C + Bugatti brief, Honda
    first), (e) ordered steps with success AND failure criteria
    (fail-fast), (f) Bugatti proposal — and cycles the PLAN-affine
    stances, journaling each contribution. Brief-first: the Bugatti's
    full breakdown is written only when the user asks
    (expand_bugatti). IRON RULE: the Bugatti is pitched, never built
    unasked. Steps seed the task DAG preserving order + dependencies.
  - parked ideas: status parked with a revisit_on date (default 30 days
    out); session start surfaces parked ideas whose revisit arrived
    ("revisit, discard, or keep parked?"); a parked idea converts to a
    spike via convert_parked_to_spike (link preserved both ways).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

STORY_TYPES = ("story", "spike", "chore")
STORY_STATES = ("open", "doing", "blocked", "parked", "done")

#: Turn interval for the story review nudge. DOCUMENTED CONSTANT: every
#: STORIES_NUDGE_EVERY user turns, if any open (non-blocked) story has no
#: session-log entry since the session began, the harness journals a
#: `stories_nudge` event and reminds the session — quiet abandonment is
#: how stories die. Tests patch this to a small number; production is 25.
STORIES_NUDGE_EVERY = 25

#: PLAN-phase stance cycle for story planning. Each entry is
#: (role id, owned advisory subsection). The three roles are the mode
#: router's own PLAN-affine roles (see modes.ROLES phase_affinities) —
#: planning deliberately cycles architect -> researcher -> engineer
#: instead of collapsing to a single stance. The user can override the
#: stance list at any point (plan_story's `stances` argument).
PLAN_STANCES = (
    ("ai-architect", "first-principles breakdown of the problem"),
    ("ai-researcher", "existing approaches surveyed"),
    ("software-engineer", "feasibility check and the pitched approach"),
)

#: Required approach shape, in order. plan_story enforces this at write
#: time: every part must be non-empty. The proposed-approach subsection
#: must pitch the option — including any novel/inventive variant — with
#: explicit reasoning for why it beats the alternatives, and its claims
#: carry the contract calibration convention: [Certain] / [Likely] /
#: [Guessing]. The user's original proposal is the starting point; the
#: fleshed-out version is co-authored.
APPROACH_SECTIONS = (
    ("breakdown", "### (a) First-principles breakdown"),
    ("surveyed", "### (b) Existing approaches surveyed"),
    ("user_guidance", "### (c) User guidance incorporated"),
    ("proposal", "### (d) Proposed approach"),
    ("steps", "### (e) Ordered steps"),
    ("bugatti", "### (f) Bugatti proposal"),
)

#: IRON RULE (the user's standing philosophy): the Bugatti is pitched,
#: never built unasked. Rendered with every Bugatti proposal and enforced
#: by the flow — expand_bugatti only runs when the user asks for it.
BUGATTI_IRON_RULE = "IRON RULE: the Bugatti is pitched, never built unasked."

#: Claims in the pitch carry the contract calibration convention.
CALIBRATION_LABELS = ("[Certain]", "[Likely]", "[Guessing]")


def _check_calibration(proposal: str) -> None:
    """The pitch must calibrate its claims: at least one of
    [Certain] / [Likely] / [Guessing]. Enforced at write time —
    calibration is a rule, not a suggestion."""
    if not any(label in proposal for label in CALIBRATION_LABELS):
        raise ValueError(
            f"the proposed pitch must calibrate its claims with at least "
            f"one of {' '.join(CALIBRATION_LABELS)} — uncalibrated "
            f"pitches read as certainty. Mark what you know, what you "
            f"infer, and what you're guessing.")


def _default_revisit_on() -> str:
    """Parked ideas default to a revisit 30 days out (YYYY-MM-DD)."""
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def _revisit_due(revisit_on: str | None) -> bool:
    """True when a parked idea's revisit date has arrived. Never raises."""
    if not revisit_on:
        return False
    try:
        due = datetime.strptime(revisit_on, "%Y-%m-%d").date()
        return due <= datetime.now().date()
    except (ValueError, TypeError):
        return False

_SCHEMA_VERSION = 1
_GENERATED_HEADER = """<!-- GENERATED FILE — do not edit by hand.
     Source of truth: .awino/registry/stories.json (the registry).
     Edit through the harness (story_start / story_update / story_close);
     this file is regenerated on every change. -->
"""


def _now() -> float:
    return time.time()


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _day(ts: float | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return "—"


def slugify(title: str) -> str:
    """Branch-safe slug: lowercase, runs of non-alnum become one dash."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (slug or "story")[:40].strip("-") or "story"


class StoryStore:
    """File-backed story ledger at <awino_dir>/registry/stories.json."""

    def __init__(self, awino_dir: str | Path):
        self.awino_dir = Path(awino_dir)
        self.dir = self.awino_dir / "registry"
        self.path = self.dir / "stories.json"

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def ensure(self) -> None:
        """Create the store on first use. Idempotent. Never raises."""
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            if not self.path.is_file():
                _atomic_write(self.path,
                              json.dumps({"schema_version": _SCHEMA_VERSION}))
        except OSError:
            pass

    # ------------------------------------------------------------ persistence
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                return {}
            return {k: v for k, v in data.items()
                    if k != "schema_version"}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, stories: dict) -> None:
        _atomic_write(self.path,
                      json.dumps({"schema_version": _SCHEMA_VERSION,
                                  **stories}, indent=2, sort_keys=True))

    # ------------------------------------------------------------------- CRUD
    def create(self, title: str, type: str = "story", **fields) -> dict:
        if type not in STORY_TYPES:
            raise ValueError(f"story type must be one of {STORY_TYPES}")
        status = fields.pop("status", "open")
        if status not in STORY_STATES:
            raise ValueError(f"story status must be one of {STORY_STATES}")
        stories = self._load()
        ts = _now()
        slug = slugify(title)
        story = {
            "id": "st-" + _uid(),
            "title": title.strip(),
            "slug": slug,
            "type": type,
            "status": status,
            "problem": fields.pop("problem", ""),
            "description": fields.pop("description", ""),
            "approach": fields.pop("approach", ""),
            "done_criteria": list(fields.pop("done_criteria", []) or []),
            "blockers": list(fields.pop("blockers", []) or []),
            "seeds": list(fields.pop("seeds", []) or []),
            "branch": f"story/{slug}",
            "sessions": [],
            "ready_to_close": False,
            "created_ts": ts,
            "closed_ts": None,
            "outcome": "",
            # Parked ideas: a revisit date, defaulting to 30 days out.
            # User-settable via story_update(..., revisit_on="YYYY-MM-DD").
            "revisit_on": (fields.pop("revisit_on", None)
                           or (_default_revisit_on()
                               if status == "parked" else "")),
        }
        for k, v in fields.items():
            if k not in story:
                story[k] = v
        stories[story["id"]] = story
        self._save(stories)
        return story

    def get(self, story_id: str) -> dict:
        stories = self._load()
        if story_id not in stories:
            raise KeyError(f"no such story: {story_id}")
        return stories[story_id]

    def get_by_ref(self, ref: str) -> dict | None:
        """Find by id, title, or slug (case-insensitive). None when absent."""
        want = (ref or "").strip().lower()
        if not want:
            return None
        for s in self._load().values():
            if (s.get("id", "").lower() == want
                    or s.get("title", "").lower() == want
                    or s.get("slug", "").lower() == want):
                return s
        return None

    def update(self, story_id: str, **fields) -> dict:
        stories = self._load()
        if story_id not in stories:
            raise KeyError(f"no such story: {story_id}")
        story = stories[story_id]
        if "type" in fields and fields["type"] not in STORY_TYPES:
            raise ValueError(f"story type must be one of {STORY_TYPES}")
        if "status" in fields and fields["status"] not in STORY_STATES:
            raise ValueError(f"story status must be one of {STORY_STATES}")
        # Parking a story without a revisit date defaults to 30 days out.
        if (fields.get("status") == "parked"
                and not fields.get("revisit_on")
                and not story.get("revisit_on")):
            fields["revisit_on"] = _default_revisit_on()
        for k, v in fields.items():
            if k in ("done_criteria", "blockers", "seeds") and v is not None:
                story[k] = list(v)
            elif k not in ("id", "created_ts"):
                story[k] = v
        self._save(stories)
        return story

    def list(self, status: str | None = None) -> list[dict]:
        stories = list(self._load().values())
        if status is not None:
            stories = [s for s in stories if s.get("status") == status]
        return sorted(stories, key=lambda s: s.get("created_ts", 0))

    def open_stories(self) -> list[dict]:
        """Stories needing attention: doing first, then open, then blocked.

        Within each status, newest on TOP — the freshest work is what the
        session should see first; done stories leave these sections for
        the Brag board at the bottom of STORY.md.
        """
        rank = {"doing": 0, "open": 1, "blocked": 2}
        stories = [s for s in self.list() if s.get("status") in rank]
        return sorted(stories,
                      key=lambda s: (rank[s["status"]],
                                     -s.get("created_ts", 0)))


# ---------------------------------------------------------------------------
# rendering: STORY.md is generated from the registry on every mutation
# ---------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    """Human effort: 5400 -> '1h 30m', 2700 -> '45m', 90 -> '1m 30s'."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s" if s else f"{m}m"
    return f"{s}s"


def _session_seconds(entry: dict) -> float:
    """Wall time of one session-log entry. Old {ts}-only entries count 0."""
    start = entry.get("started_ts") or entry.get("ts")
    end = entry.get("ended_ts")
    if not start or not end:
        return 0.0
    return max(0.0, end - start)


def story_time_spent(awino_dir: str | Path, story_id: str) -> float:
    """Total accumulated wall-clock seconds across the story's sessions."""
    store = StoryStore(awino_dir)
    story = store.get(story_id)
    return sum(_session_seconds(e) for e in (story.get("sessions") or []))


def _render_story_block(s: dict) -> list[str]:
    crit = s.get("done_criteria") or []
    blockers = s.get("blockers") or []
    seeds = s.get("seeds") or []
    sessions = s.get("sessions") or []
    spent = sum(_session_seconds(e) for e in sessions)
    lines = [
        f"### `{s['id']}` — {s['title']} [{s['type']}] — {s['status']}",
    ]
    if s.get("ready_to_close"):
        lines[-1] += " — ✓ ready to close (passed verification, waiting on you)"
    lines.append(f"- **Problem:** {s.get('problem') or '—'}")
    lines.append(f"- **Description:** {s.get('description') or '—'}")
    lines.append(f"- **Approach (the spine):** {s.get('approach') or '—'}")
    lines.append("- **Done criteria (how we'll know it's done):**")
    if crit:
        lines.extend(f"  - [ ] {c}" for c in crit)
    else:
        lines.append("  - —")
    lines.append("- **Blockers:**")
    if blockers:
        lines.extend(f"  - {b}" for b in blockers)
    else:
        lines.append("  - none")
    lines.append(f"- **Seeds:** {', '.join(seeds) if seeds else '—'}")
    lines.append(f"- **Branch:** `{s.get('branch') or '—'}`")
    if s.get("revisit_on"):
        lines.append(f"- **Revisit on:** {s['revisit_on']}")
    if s.get("converted_from"):
        lines.append(f"- **Converted from parked idea:** `{s['converted_from']}`")
    if s.get("converted_to"):
        lines.append(f"- **Converted to spike:** `{s['converted_to']}`")
    lines.append(f"- **Time dedicated:** {format_duration(spent)}")
    lines.append("- **Sessions:**")
    if sessions:
        for sess in sessions:
            start = sess.get("started_ts") or sess.get("ts")
            end = sess.get("ended_ts")
            when = _day(start)
            dur = _session_seconds(sess)
            stamp = (f"{when} ({format_duration(dur)})" if dur
                     else when)
            lines.append(f"  - {stamp} — {sess.get('note', '')}")
    else:
        lines.append("  - —")
    return lines


def render_story_md(awino_dir: str | Path) -> str:
    """Render <project_root>/STORY.md from the registry. Idempotent.

    Returns the rendered text. Never raises — a render failure must not
    break the mission that triggered it.
    """
    try:
        awino_dir = Path(awino_dir)
        store = StoryStore(awino_dir)
        store.ensure()
        doing = [s for s in store.open_stories() if s["status"] == "doing"]
        opened = [s for s in store.open_stories() if s["status"] == "open"]
        blocked = [s for s in store.open_stories() if s["status"] == "blocked"]
        parked = sorted(store.list(status="parked"),
                        key=lambda s: s.get("revisit_on") or "9999")
        done = store.list(status="done")

        lines = [_GENERATED_HEADER.rstrip(), "",
                 "# STORY.md — the story ledger", "",
                 "Every piece of work in this project is a story: what the "
                 "problem is, how we'll approach it (the spine — written "
                 "during planning), and how we'll know it's done. "
                 "Done stories accumulate below as the brag board.", ""]

        def section(title: str, stories: list[dict], empty_note: str):
            lines.append(f"## {title}")
            lines.append("")
            if not stories:
                lines.append(empty_note)
                lines.append("")
                return
            for s in stories:
                lines.extend(_render_story_block(s))
                lines.append("")

        section("Doing", doing, "Nothing in progress.")
        section("Open", opened, "No open stories.")
        section("Blocked", blocked, "Nothing blocked.")
        section("Parked ideas", parked,
                "No parked ideas. Park one with story_update(id, "
                "status='parked') — it resurfaces on its revisit date.")

        lines.append("## Brag board")
        lines.append("")
        lines.append("Finished stories, with closed date, outcome, and "
                     "time dedicated.")
        lines.append("")
        if not done:
            lines.append("No finished stories yet.")
            lines.append("")
        for s in sorted(done, key=lambda s: s.get("closed_ts", 0),
                        reverse=True):
            spent = sum(_session_seconds(e) for e in (s.get("sessions") or []))
            lines.append(
                f"### `{s['id']}` — {s['title']} [{s['type']}] — "
                f"closed {_day(s.get('closed_ts'))}")
            lines.append(f"- **Outcome:** {s.get('outcome') or '—'}")
            lines.append(f"- **Time dedicated:** {format_duration(spent)}")
            lines.append(f"- **Problem:** {s.get('problem') or '—'}")
            lines.append(f"- **Approach:** {s.get('approach') or '—'}")
            crit = s.get("done_criteria") or []
            if crit:
                lines.append("- **Done criteria:**")
                lines.extend(f"  - [x] {c}" for c in crit)
            seeds = s.get("seeds") or []
            if seeds:
                lines.append(f"- **Seeds:** {', '.join(seeds)}")
            lines.append("")

        text = "\n".join(lines)
        target = awino_dir.parent / "STORY.md"
        try:
            _atomic_write(target, text)
        except OSError:
            pass  # read-only project root: registry stays the truth
        return text
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# rituals
# ---------------------------------------------------------------------------

def _journal(awino_dir: Path, kind: str, note: str,
             mission_id: str = "story-ledger") -> None:
    """Registry breadcrumb — the durable audit trail. Never raises."""
    try:
        from registry import Registry  # local: keep story import-light
        reg = Registry(awino_dir)
        reg.ensure()
        reg.add_breadcrumb(mission_id, f"[{kind}] {note}")
    except Exception:
        pass


def story_start(awino_dir: str | Path, title: str,
                type: str = "story", **fields) -> dict:
    """Start a story: registry entry + STORY.md render + git branch.

    Branch creation is best-effort: git missing, not a repo, or branch
    creation failing journals a warning and never fails the mission.

    Stale-story rule: if other stories are still doing/open when this one
    starts, a `stale_stories` event is journaled and a plain-language
    warning is returned — the session must surface "we started this new
    thing, but X is still open — what's up?" before proceeding. Blocked
    and done stories don't count.
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    store.ensure()
    story = store.create(title, type=type, **fields)
    branch_note = _ensure_branch(awino_dir.parent, story["branch"])
    _journal(awino_dir, "story_start",
             f"Started story '{story['title']}' ({story['id']}, "
             f"type={story['type']}). {branch_note}")
    stale_warning = _check_stale_stories(awino_dir, store, story)
    touch_session(awino_dir, story["id"], "story started")
    story = store.get(story["id"])
    story["branch_note"] = branch_note
    story["stale_warning"] = stale_warning
    return story


def _check_stale_stories(awino_dir: Path, store: StoryStore,
                         current: dict | None = None) -> str:
    """The stale-story rule. Returns a warning string ('' when none).

    Fires when new work (a story, or a mission) starts while others are
    still doing/open. Journals a durable `stale_stories` event either way.
    Blocked and done stories don't count.
    """
    current_id = (current or {}).get("id")
    stale = [s for s in store.open_stories()
             if s["id"] != current_id
             and s.get("status") in ("doing", "open")]
    if not stale:
        return ""
    names = ", ".join(f"'{s['title']}' ({s['id']})" for s in stale)
    new_thing = (f"'{current['title']}' ({current['id']})"
                 if current else "a new mission")
    warning = (f"We started this new thing, but {names} "
               f"{'is' if len(stale) == 1 else 'are'} still open — "
               f"what's up? Finish, block, or close "
               f"{'it' if len(stale) == 1 else 'them'} before it goes quiet.")
    _journal(awino_dir, "stale_stories",
             f"New work started ({new_thing}) while {names} still open. "
             f"{warning}")
    return warning


def check_stale_on_mission(awino_dir: str | Path) -> str:
    """Stale-story rule for a newly arrived mission. Returns '' when none.

    Never raises — a journal hiccup must not block the mission.
    """
    try:
        awino_dir = Path(awino_dir)
        store = StoryStore(awino_dir)
        if not store.exists:
            return ""
        return _check_stale_stories(awino_dir, store, None)
    except Exception:
        return ""


def _ensure_branch(project_root: Path, branch: str) -> str:
    """Create (or reuse) git branch `branch`. Returns a note. Never raises."""
    try:
        if shutil.which("git") is None:
            return "git not found — no branch created (warning only)."
        r = subprocess.run(["git", "rev-parse", "--git-dir"],
                           cwd=project_root, capture_output=True, timeout=15)
        if r.returncode != 0:
            return "not a git repo — no branch created (warning only)."
        # Reuse if it already exists (e.g. story restarted).
        r = subprocess.run(["git", "rev-parse", "--verify", branch],
                           cwd=project_root, capture_output=True, timeout=15)
        if r.returncode == 0:
            subprocess.run(["git", "checkout", branch], cwd=project_root,
                           capture_output=True, timeout=15)
            return f"reused existing branch {branch}."
        r = subprocess.run(["git", "checkout", "-b", branch],
                           cwd=project_root, capture_output=True, timeout=15)
        if r.returncode == 0:
            return f"created branch {branch}."
        err = (r.stderr or b"").decode(errors="replace").strip()[:200]
        return f"branch creation failed ({err or 'unknown error'}) — warning only."
    except Exception as e:  # noqa: BLE001 — best-effort by contract
        return f"branch creation skipped ({type(e).__name__}) — warning only."


def _classify_git_failure(stderr: bytes) -> str:
    """Credential-safe failure label for git/gh errors.

    Raw stderr can echo credential-bearing remote URLs (usernames,
    tokens embedded in the URL), so we NEVER journal or return the raw
    text — only a coarse category derived from it. The full stderr
    stays in the local process memory, never in the registry/journal.
    """
    text = (stderr or b"").decode(errors="replace").lower()
    if ("could not resolve" in text or "network is unreachable" in text
            or "temporary failure in name resolution" in text
            or "name or service not known" in text):
        return "offline / DNS unreachable"
    if ("authentication failed" in text or "permission denied" in text
            or "could not read from remote" in text or " 401" in text
            or " 403" in text or "invalid credentials" in text):
        return "auth failed (no/expired credential)"
    if ("connection refused" in text or "timed out" in text
            or "connection reset" in text):
        return "connection failed"
    if ("has no upstream" in text or "no such remote" in text
            or "'origin' does not appear" in text):
        return "no remote configured"
    return "git push failed (details withheld — raw remote output is " \
        "never journaled)"


def _best_effort_push(project_root: Path, branch: str) -> str:
    """Push the story branch and open a PR where `gh` exists. Never raises.

    WINDOWS-safe: argument-list subprocess calls, no shell. Offline-safe:
    GIT_TERMINAL_PROMPT=0 so a missing credential fails fast instead of
    hanging; any failure is journaled as a warning and the mission
    continues. Credential-safe: failure output is classified, never
    echoed (see _classify_git_failure). No auto-merge — the human merges
    the PR.
    """
    try:
        git = shutil.which("git")
        if git is None:
            return "push skipped: git not installed (warning only)."
        r = subprocess.run([git, "rev-parse", "--show-toplevel"],
                           cwd=project_root, capture_output=True, timeout=15)
        if r.returncode != 0:
            return "push skipped: not a git repo (warning only)."
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        r = subprocess.run([git, "push", "-u", "origin", branch],
                           cwd=project_root, capture_output=True,
                           timeout=90, env=env)
        if r.returncode != 0:
            why = _classify_git_failure(r.stderr)
            return (f"push failed ({why}) — no remote, no auth, or offline "
                    f"(warning only).")
        note = f"pushed {branch} to origin."
        gh = shutil.which("gh")
        if gh is None:
            return note + " gh not found — open the PR by hand."
        r = subprocess.run(
            [gh, "pr", "create", "--fill", "--base", "main",
             "--head", branch],
            cwd=project_root, capture_output=True, timeout=90, env=env)
        if r.returncode == 0:
            url = (r.stdout or b"").decode(errors="replace").strip()
            return note + f" PR opened: {url or '(see gh pr list)'}."
        return note + " PR not opened (gh failed — warning only)."
    except Exception as e:  # noqa: BLE001 — best-effort by contract
        return f"push skipped ({type(e).__name__} — warning only)."


def _validate_stances(stances: list[str] | tuple[str, ...] | None) -> list[str]:
    """The user can override the planning stances at any point; the ids
    must still be real role lenses (typos fail fast, loudly)."""
    if stances is None:
        return [role for role, _ in PLAN_STANCES]
    from modes import ROLE_IDS  # local: keep story import-light
    bad = [s for s in stances if s not in ROLE_IDS]
    if bad:
        raise ValueError(
            f"unknown stance(s) {bad}; pick from {', '.join(ROLE_IDS)}")
    if not stances:
        raise ValueError("need at least one stance for story planning")
    return list(stances)


def _render_steps_md(steps: list[dict]) -> str:
    """Subsection (e): ordered, prioritized steps with dependencies.

    Each step carries its own success AND failure criteria —
    fail-fast thinking: we know what "worked" looks like and what
    "kill it now" looks like before we start.
    """
    lines = ["### (e) Ordered steps", ""]
    for i, st in enumerate(steps):
        deps = st.get("depends_on") or []
        dep = (f" (depends on step {', '.join(str(d + 1) for d in deps)})"
               if deps else "")
        lines.append(f"{i + 1}. **{st['title']}**{dep}")
        lines.append(f"   - success: {st['success']}")
        lines.append(f"   - fail fast: {st['failure']}")
        if st.get("forecast"):
            lines.append(f"   - forecast: {st['forecast']}")
    return "\n".join(lines)


def _render_bugatti_md(brief: str, full: str = "") -> str:
    """Subsection (f): the Bugatti proposal. Brief-first: the full
    step-by-step breakdown is written only when the user asks
    (expand_bugatti). IRON RULE: pitched, never built unasked."""
    lines = ["### (f) Bugatti proposal", "",
             f"> {BUGATTI_IRON_RULE}", "",
             "**The brief:**", "", (brief or "").strip(), ""]
    if (full or "").strip():
        lines += ["**Full breakdown (expanded on request):**", "",
                  full.strip()]
    else:
        lines += ["**Full breakdown:** — not expanded yet. Ask and the "
                  "harness will flesh out the step-by-step; it is never "
                  "built unasked."]
    return "\n".join(lines)


def _compose_approach(parts: dict, steps: list[dict],
                      bugatti_brief: str, bugatti_full: str = "") -> str:
    """Assemble the full approach markdown: (a)-(d) prose, (e) steps,
    (f) Bugatti. The structured steps/bugatti live on the story too
    (story["steps"], story["bugatti_brief"]/["bugatti_full"]) — this
    text is the rendered view."""
    headers = {key: header for key, header in APPROACH_SECTIONS}
    chunks = [f"{headers[key]}\n\n{parts[key].strip()}"
              for key in ("breakdown", "surveyed", "user_guidance",
                          "proposal")]
    chunks.append(_render_steps_md(steps))
    chunks.append(_render_bugatti_md(bugatti_brief, bugatti_full))
    return "\n\n".join(chunks)


def _normalize_steps(steps: list[dict] | None) -> list[dict]:
    """Validate/normalize (e) ordered steps at write time.

    Every step needs title + success + failure (fail-fast thinking).
    depends_on lists EARLIER step indexes (0-based); omitted means
    "after the previous step" — the chain stays acyclic by construction.
    """
    if not isinstance(steps, list) or not steps:
        raise ValueError(
            "story plan needs (e) ordered steps: a non-empty list of "
            "{title, success, failure, depends_on?} — 'if we do X then Y "
            "then Z', each step with its own success AND failure criteria.")
    norm = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            raise ValueError(f"step {i + 1} must be a mapping "
                             f"{{title, success, failure, depends_on?}}")
        title = (st.get("title") or "").strip()
        success = (st.get("success") or "").strip()
        failure = (st.get("failure") or "").strip()
        missing = [k for k, v in (("title", title), ("success", success),
                                  ("failure", failure)) if not v]
        if missing:
            raise ValueError(
                f"step {i + 1} ('{title or '?'}') is missing "
                f"{', '.join(missing)} — every step carries its own success "
                f"AND failure criteria (fail-fast thinking).")
        deps = st.get("depends_on")
        if deps is None:
            deps = [i - 1] if i > 0 else []
        else:
            deps = list(deps)
        for d in deps:
            if not isinstance(d, int) or d < 0 or d >= i:
                raise ValueError(
                    f"step {i + 1} has bad dependency {d!r}: depends_on "
                    f"lists earlier step indexes (0-based) — the chain "
                    f"stays acyclic by construction.")
        step = {"title": title, "success": success,
                "failure": failure, "depends_on": deps}
        # Optional time forecast ("45m", "2h"): the receipt compares it
        # with the actual time when the story closes.
        forecast = str(st.get("forecast") or "").strip()
        if forecast:
            from receipt import parse_forecast  # local: keep story light
            secs = parse_forecast(forecast)
            if secs is None:
                raise ValueError(
                    f"step {i + 1} ('{title}') has forecast {forecast!r}; "
                    f"use a duration like '30m', '2h' or '1h30m'.")
            step["forecast"] = forecast
            step["forecast_s"] = secs
        norm.append(step)
    return norm


def seed_dag_from_story(awino_dir: str | Path, story_id: str) -> int:
    """Seed the registry task DAG from the story's (e) ordered steps.

    Each step becomes a DAG task preserving order and depends_on; the
    step's success criterion becomes the task's done_criteria and the
    failure criterion rides along for fail-fast checks. Idempotent:
    steps already seeded (same story_id + step_index) are skipped.
    Returns the number of tasks created. Never raises.
    """
    try:
        from registry import Registry  # local: keep story import-light
        awd = Path(awino_dir)
        store = StoryStore(awd)
        story = store.get(story_id)
        steps = story.get("steps") or []
        if not steps:
            return 0
        reg = Registry(awd)
        reg.ensure()
        index_to_id: dict[int, str] = {}
        for t in reg.tasks():
            if (t.get("story_id") == story_id
                    and isinstance(t.get("step_index"), int)):
                index_to_id[t["step_index"]] = t["id"]
        n = 0
        for i, st in enumerate(steps):
            if i in index_to_id:
                continue
            deps = [index_to_id[d] for d in (st.get("depends_on") or [])
                    if d in index_to_id]
            task = reg.add_task(
                st["title"], source=f"story:{story_id}",
                depends_on=deps, done_criteria=st.get("success", ""),
                story_id=story_id, step_index=i,
                failure_criteria=st.get("failure", ""))
            index_to_id[i] = task["id"]
            n += 1
        if n:
            _journal(awd, "story_dag_seeded",
                     f"Seeded {n} DAG task(s) from story "
                     f"'{story['title']}' ({story_id}) ordered steps.")
        return n
    except Exception:
        return 0


def plan_story(awino_dir: str | Path, story_id: str, *,
               breakdown: str, surveyed: str, user_guidance: str,
               proposal: str, steps: list[dict] | None,
               bugatti_brief: str,
               stances: list[str] | None = None,
               note: str = "") -> dict:
    """Write the story's approach (the spine) during PLAN, with the
    REQUIRED six-part shape, and cycle the planning stances.

    Shape is enforced AT WRITE TIME (ValueError naming what's missing):
      (a) first-principles breakdown
      (b) existing approaches surveyed
      (c) user guidance incorporated
      (d) proposed approach — the pitch: A/B/C options + Bugatti in
          BRIEF form, with explicit reasoning for why the pick beats the
          alternatives; claims carry [Certain] / [Likely] / [Guessing]
          calibration (the contract calibration convention).
          The Honda — the committed scope, what was asked — is the
          recommendation; the Bugatti is the inventive option.
      (e) ordered steps — "if we do X then Y then Z", each with its own
          success AND failure criteria (fail-fast thinking). The DAG
          seeding consumes these: steps become DAG tasks preserving
          order and dependencies (seed_dag_from_story, automatic here).
      (f) Bugatti proposal — the inventive option in brief form at plan
          time. Brief-first: the full step-by-step breakdown is written
          only when the user asks (expand_bugatti). IRON RULE: the
          Bugatti is pitched, never built unasked.

    Multi-stance: the flow cycles PLAN_STANCES (architect -> researcher
    -> engineer, the mode router's own PLAN affinities) and journals a
    `story_plan_stance` breadcrumb per contribution; `stances` overrides
    the cycle (validated role ids). Returns the updated story with a
    "planned_stances" key.

    The hard BUILD gate stays deliberately simple (approach non-empty);
    the shape discipline lives here, at write time, where the authoring
    happens.
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    story = store.get(story_id)  # KeyError on unknown — fail loud
    parts = {"breakdown": breakdown, "surveyed": surveyed,
             "user_guidance": user_guidance, "proposal": proposal,
             "bugatti_brief": bugatti_brief}
    missing = [k for k, v in parts.items() if not (v or "").strip()]
    if missing:
        raise ValueError(
            f"story approach needs all six parts (a)-(f); missing: "
            f"{', '.join(missing)}. Write the spine during planning — "
            f"the BUILD gate will refuse without it.")
    _check_calibration(proposal)
    norm_steps = _normalize_steps(steps)
    stances = _validate_stances(stances)
    approach = _compose_approach(parts, norm_steps, bugatti_brief)
    store.update(story_id, approach=approach, approach_parts=dict(parts),
                 steps=norm_steps, bugatti_brief=bugatti_brief.strip(),
                 bugatti_full="")
    seeded = seed_dag_from_story(awino_dir, story_id)
    # Cycle the stances deliberately; journal each contribution.
    owned = [owns for _, owns in PLAN_STANCES]
    for i, stance in enumerate(stances):
        contribution = owned[i % len(owned)]
        _journal(awino_dir, "story_plan_stance",
                 f"Story '{story['title']}' ({story_id}): "
                 f"{stance} contributed the {contribution}.")
    session_begin(awino_dir, story_id,
                  note or f"planned the approach ({', '.join(stances)}; "
                  f"{seeded} DAG task(s) seeded)")
    story = store.get(story_id)
    story["planned_stances"] = stances
    story["dag_seeded"] = seeded
    render_story_md(awino_dir)
    return story


def expand_bugatti(awino_dir: str | Path, story_id: str,
                   bugatti_full: str) -> dict:
    """Expand the Bugatti proposal into the full step-by-step breakdown.

    Brief-first: plan_story writes only the brief; the full breakdown is
    written here, and ONLY when the user asks for it. IRON RULE: the
    Bugatti is pitched, never built unasked — this function writes
    words, never work.
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    story = store.get(story_id)  # KeyError on unknown — fail loud
    if not (bugatti_full or "").strip():
        raise ValueError("nothing to expand — provide the full Bugatti "
                         "breakdown (what we'd do step by step, what we'd "
                         "get).")
    parts = story.get("approach_parts") or {}
    if not parts:
        raise ValueError("story has no planned approach yet — run "
                         "plan_story before expanding the Bugatti.")
    steps = story.get("steps") or []
    brief = story.get("bugatti_brief") or ""
    approach = _compose_approach(parts, steps, brief, bugatti_full)
    store.update(story_id, approach=approach,
                 bugatti_full=bugatti_full.strip())
    _journal(awino_dir, "story_bugatti_expanded",
             f"Story '{story['title']}' ({story_id}): Bugatti expanded to "
             f"the full breakdown on the user's request. "
             f"{BUGATTI_IRON_RULE}")
    touch_session(awino_dir, story_id, "expanded the Bugatti")
    story = store.get(story_id)
    render_story_md(awino_dir)
    return story


def parked_due_summary(awino_dir: str | Path) -> list[dict]:
    """Parked ideas whose revisit date has arrived (revisit_on <= today).

    [] when the store is absent or nothing is due. Never raises.
    """
    try:
        store = StoryStore(awino_dir)
        if not store.exists:
            return []
        due = [s for s in store.list(status="parked")
               if _revisit_due(s.get("revisit_on"))]
        return sorted(due, key=lambda s: s.get("revisit_on") or "9999")
    except Exception:
        return []


def convert_parked_to_spike(awino_dir: str | Path, story_id: str,
                            title: str | None = None,
                            done_criteria: list[str] | None = None) -> dict:
    """Convert a parked idea into a spike story (a time-boxed probe).

    The parked idea keeps its home; the link is preserved both ways
    (spike.converted_from, parked.converted_to). Journals
    `story_parked_converted` and renders. Returns the new spike.
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    store.ensure()
    parked = store.get(story_id)  # KeyError on unknown — fail loud
    if parked.get("status") != "parked":
        raise ValueError(
            f"story '{parked['title']}' is not parked "
            f"(status={parked.get('status')}) — only parked ideas convert "
            f"to spikes.")
    spike = store.create(
        (title or f"Spike: {parked['title']}").strip(),
        type="spike", status="open",
        problem=parked.get("problem", ""),
        description=(f"Time-boxed investigation of parked idea "
                     f"'{parked['title']}' ({story_id})."),
        done_criteria=list(done_criteria or []),
        converted_from=story_id)
    branch_note = _ensure_branch(awino_dir.parent, spike["branch"])
    store.update(story_id, converted_to=spike["id"])
    _journal(awino_dir, "story_parked_converted",
             f"Parked idea '{parked['title']}' ({story_id}) converted to "
             f"spike '{spike['title']}' ({spike['id']}). {branch_note}")
    touch_session(awino_dir, spike["id"],
                  f"converted from parked idea {story_id}")
    render_story_md(awino_dir)
    spike = store.get(spike["id"])
    spike["branch_note"] = branch_note
    return spike


def story_update(awino_dir: str | Path, story_id: str, **fields) -> dict:
    """Update story fields (problem/description/approach/done_criteria/
    blockers/status/seeds). Re-renders STORY.md."""
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    story = store.update(story_id, **fields)
    render_story_md(awino_dir)
    return story


def session_begin(awino_dir: str | Path, story_id: str, note: str = "",
                  started_ts: float | None = None) -> dict:
    """Open a session-log entry: records the start timestamp.

    Call session_end when the work stops; the pair's wall time accumulates
    into the story's "time dedicated". `started_ts` is injectable so tests
    can use fake sessions deterministically.
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    story = store.get(story_id)
    sessions = list(story.get("sessions") or [])
    sessions.append({"started_ts": started_ts if started_ts is not None else _now(),
                     "ended_ts": None, "note": note})
    story = store.update(story_id, sessions=sessions)
    render_story_md(awino_dir)
    return story


def session_end(awino_dir: str | Path, story_id: str, note: str = "",
                ended_ts: float | None = None) -> dict:
    """Close the latest open session-log entry, stamping its end time.

    When nothing is open, logs a zero-duration touch so the contact is
    still recorded. Re-renders STORY.md.
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    story = store.get(story_id)
    sessions = list(story.get("sessions") or [])
    now = ended_ts if ended_ts is not None else _now()
    for sess in reversed(sessions):
        if sess.get("ended_ts") is None and sess.get("started_ts"):
            sess["ended_ts"] = now
            if note:
                prev = sess.get("note", "")
                sess["note"] = f"{prev} / {note}" if prev else note
            break
    else:
        sessions.append({"started_ts": now, "ended_ts": now, "note": note})
    story = store.update(story_id, sessions=sessions)
    render_story_md(awino_dir)
    return story


def touch_session(awino_dir: str | Path, story_id: str, note: str) -> dict:
    """Log a session's touch on a story (zero-duration contact). Re-renders.

    For real work sessions use session_begin/session_end so wall time
    accumulates; touch_session is for brief contacts (reviews, filing).
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    story = store.get(story_id)
    sessions = list(story.get("sessions") or [])
    now = _now()
    sessions.append({"started_ts": now, "ended_ts": now, "note": note})
    story = store.update(story_id, sessions=sessions)
    render_story_md(awino_dir)
    return story


def story_close(awino_dir: str | Path, story_id: str, outcome: str,
                *, events: list[dict] | None = None,
                chain_ok: bool | None = None) -> dict:
    """Close a story: stamps closed date + outcome into the brag board.

    Close authority stays with the human — the harness only ever ASKS
    (story_ready_to_close); this is the human's answer.

    Git lifecycle on close (best-effort, never fails the mission):
    commit early and often LOCALLY during the work; on close the harness
    pushes the story branch to origin and, where `gh` exists, opens a PR.
    No remote / no auth / no network journals a warning and the mission
    continues — offline posture is never broken, nothing is published.
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    story = store.update(story_id, status="done", closed_ts=_now(),
                         outcome=outcome, ready_to_close=False)
    session_end(awino_dir, story_id, "story closed")
    story = store.get(story_id)
    push_note = _best_effort_push(awino_dir.parent, story["branch"])
    _journal(awino_dir, "story_close",
             f"Closed story '{story['title']}' ({story['id']}). "
             f"Outcome: {outcome} Git: {push_note}")
    try:
        from registry import Registry  # local: keep story import-light
        reg = Registry(awino_dir)
        reg.ensure()
        reg.add_milestone("completion",
                          f"Story done: {story['title']} — {outcome}")
    except Exception:
        pass
    render_story_md(awino_dir)
    story["push_note"] = push_note
    # Receipt: promise -> proof -> lesson, from the journal (best-effort;
    # a receipt failure never blocks the close).
    try:
        from receipt import build_receipt, write_receipt
        rc = build_receipt(awino_dir, story_id, events=events,
                           chain_ok=chain_ok)
        story["receipt_path"] = str(write_receipt(awino_dir, rc))
        story["receipt_status"] = rc["status"]
        _journal(awino_dir, "story_receipt",
                 f"Receipt for '{story['title']}' ({story_id}): "
                 f"{rc['status']}.")
    except Exception as ex:  # noqa: BLE001
        story["receipt_error"] = f"{type(ex).__name__}: {ex}"
    return story


def mark_ready_to_close(awino_dir: str | Path, story_id: str) -> dict:
    """Flag a story ready-to-close after a passing verifier verdict.

    The harness ASKS the user to close it — it does not close it.
    """
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    story = store.update(story_id, ready_to_close=True)
    _journal(awino_dir, "story_ready_to_close",
             f"Story '{story['title']}' ({story['id']}) passed verification — "
             f"ask the user to close it (story_close).")
    render_story_md(awino_dir)
    return story


def doing_stories(awino_dir: str | Path) -> list[dict]:
    """The active story/stories: status == doing. [] when the store is absent."""
    try:
        store = StoryStore(awino_dir)
        if not store.exists:
            return []
        return store.list(status="doing")
    except Exception:
        return []


def open_stories_summary(awino_dir: str | Path) -> list[dict]:
    """Compact open/doing/blocked list for session-start review. Never raises."""
    try:
        store = StoryStore(awino_dir)
        if not store.exists:
            return []
        return [{"id": s["id"], "title": s["title"], "type": s["type"],
                 "status": s["status"],
                 "ready_to_close": bool(s.get("ready_to_close")),
                 "blockers": list(s.get("blockers") or [])}
                for s in store.open_stories()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# seeds: `story:` frontmatter + inbox auto-file (never orphaned)
# ---------------------------------------------------------------------------

def find_or_create_for_seed(awino_dir: str | Path, seed_file: str,
                            story_ref: str | None) -> dict:
    """Resolve a seed's `story:` frontmatter ref to a story, creating it when
    needed. Unlinked seeds file under the Inbox story. Never raises."""
    awino_dir = Path(awino_dir)
    store = StoryStore(awino_dir)
    store.ensure()
    if story_ref:
        found = store.get_by_ref(story_ref)
        if found is not None:
            return found
        story = store.create(
            story_ref, type="story", status="open",
            description=f"Created from seed '{seed_file}' "
                        f"(`story:` frontmatter). Fill in problem/approach "
                        f"during planning.")
        _journal(awino_dir, "seed_story",
                 f"Seed '{seed_file}' created story '{story['title']}' "
                 f"({story['id']}).")
        return story
    inbox = store.get_by_ref("inbox")
    if inbox is None:
        inbox = store.create(
            "Inbox", type="chore", status="open",
            description="Unlinked seeds land here — never orphaned. "
                        "Triage into real stories at session start.")
        _journal(awino_dir, "seed_story",
                 f"Created Inbox story ({inbox['id']}) for unlinked seeds.")
    return inbox


def _stamp_tasks_with_story(awino_dir: Path, seed_name: str,
                            story_id: str) -> None:
    """Stamp story_id onto tasks imported from a seed. Never raises."""
    try:
        p = awino_dir / "registry" / "tasks.json"
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            return
        changed = False
        for k, t in data.items():
            if k == "schema_version" or not isinstance(t, dict):
                continue
            if t.get("source") == seed_name and not t.get("story_id"):
                t["story_id"] = story_id
                changed = True
        if changed:
            _atomic_write(p, json.dumps(data, indent=2, sort_keys=True))
    except (OSError, json.JSONDecodeError, ValueError):
        pass


def file_seeds(awino_dir: str | Path, seed_tasks: list[dict] | None) -> int:
    """File every seed under its story (frontmatter `story:` or Inbox).

    Links the seed name onto the story, stamps story_id on the seed's
    tasks, re-renders STORY.md. Returns the number of seeds filed.
    Never raises — seed filing must not break init.
    """
    try:
        awino_dir = Path(awino_dir)
        by_seed: dict[str, str | None] = {}
        for t in seed_tasks or []:
            src = t.get("source") or ""
            seed_file = t.get("seed_file")
            if not seed_file and src.startswith("seed:"):
                seed_file = src[len("seed:"):]
            if not seed_file:
                continue
            # first ref wins; all tasks from one seed agree anyway
            by_seed.setdefault(seed_file, t.get("story"))
        if not by_seed:
            return 0
        store = StoryStore(awino_dir)
        store.ensure()
        n = 0
        for seed_file in sorted(by_seed):
            story = find_or_create_for_seed(awino_dir, seed_file,
                                            by_seed[seed_file])
            seed_name = f"seed:{seed_file}"
            seeds = list(story.get("seeds") or [])
            if seed_name not in seeds:
                seeds.append(seed_name)
                store.update(story["id"], seeds=seeds)
            _stamp_tasks_with_story(awino_dir, seed_name, story["id"])
            n += 1
        render_story_md(awino_dir)
        return n
    except Exception:
        return 0
