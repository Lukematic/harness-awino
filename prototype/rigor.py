"""Awino Rigor Coach — background engineering-practice auditor.

Reads a mission's journal (events.jsonl) and scores engineering rigor from
evidence, never from claims. The rubric is agent-rigor's Five Non-Negotiable
Laws, adapted: in Awino the laws bind the *model's* conduct (harness
guardrails), not the user. The user is the top principal — an explicit user
override is journaled by the coach as an informational finding, never a
penalty and never a lecture.

The coach is READ-ONLY. It never modifies code and never executes project
commands to build a report (an optional, bounded `git log` probe exists for
commit evidence and degrades to "unknown" when unavailable). Its single
write is appending a `rigor_report` event to the journal — the report itself
becomes evidence.

Adapted from agent-rigor (MIT, MeherBhaskar): the Five Laws, the operational
state machine's ROLLBACK & RETHINK path, the Error Recovery Protocol
(STOP → DIAGNOSE → ISOLATE → ROLLBACK → LOG → RETRY), the file-convention
*functions* (ordered task list, append-only decision log, persisted ADRs —
scored via Awino's journal/plan/learnings equivalents, never forced
filenames), and layered skill loading (the coach never bulk-loads skills;
it only reads the journal).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------
PASS, PARTIAL, FAIL, UNKNOWN, INFO = "pass", "partial", "fail", "unknown", "info"
_SCORE_VALUE = {PASS: 1.0, PARTIAL: 0.5, FAIL: 0.0}

# check name -> (weight, law, title)
CHECKS: dict[str, tuple[float, str, str]] = {
    "observable_proof":   (3.0, "Law 1 — Observable Proof",
                           "completion claims carry verifiable evidence"),
    "atomic_transitions": (2.0, "Law 2 — Atomic State Transitions",
                           "no broken-state or over-failure ships"),
    "tests_green":        (2.0, "Law 1 — Observable Proof",
                           "tests/verification green at mission end"),
    "three_strike":       (2.0, "Error Recovery Protocol",
                           "no doom loop (3 identical failures) without rethink"),
    "error_recovery":     (2.0, "Error Recovery Protocol",
                           "failures follow STOP→DIAGNOSE→ROLLBACK→LOG→RETRY"),
    "minimal_authority":  (1.5, "Law 5 — Minimal Authority",
                           "scope containment: files touched vs declared scope"),
    "regression_test":    (1.5, "Law 1 — Observable Proof",
                           "bugfix missions add a regression test"),
    "preserved_intent":   (1.0, "Law 3 — Preserved Intent",
                           "modifications carry a stated rationale"),
    "declared_uncertainty": (1.0, "Law 4 — Declared Uncertainty",
                             "blocked → asked the user, not guessed"),
    "tests_run":          (1.0, "Law 1 — Observable Proof",
                           "tests were run during the mission"),
    "lint_evidence":      (1.0, "Law 2 — Atomic State Transitions",
                           "lint clean before ship"),
    "task_list":          (1.0, "File conventions → PLAN.md function",
                           "ordered task list exists (plan / done criteria)"),
    "learnings":          (1.0, "File conventions → learned_rules function",
                           "learnings persisted (journal → synthesis)"),
    "decision_log":       (0.5, "File conventions → progress_log.md function",
                           "append-only decision log kept"),
}

_TEST_CMD = re.compile(r"\b(pytest|jest|vitest|mocha|go test|npm test|"
                       r"pytest|\btest\b|tsc\b|make test)\b", re.I)
_LINT_CMD = re.compile(r"\b(ruff|flake8|eslint|pylint|tsc --noEmit|"
                       r"mypy|clippy)\b", re.I)
_TEST_PATH = re.compile(r"(test_|_test\.|tests?/|spec/)", re.I)


def _finding(name: str, status: str, evidence: list[str],
             nudge: str | None = None) -> dict:
    weight, law, title = CHECKS[name]
    return {"check": name, "law": law, "title": title, "weight": weight,
            "status": status,
            "value": _SCORE_VALUE.get(status),
            "evidence": evidence, "nudge": nudge}


# ---------------------------------------------------------------------------
# Journal loading & mission slicing
# ---------------------------------------------------------------------------
def load_events(project_dir: str | Path) -> list[dict]:
    """Read a project's events.jsonl (read-only)."""
    path = Path(project_dir) / "events.jsonl"
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def mission_windows(events: list[dict]) -> list[dict]:
    """Slice the journal into per-mission windows.

    Each window: mission_set -> next mission_set (exclusive). The last window
    runs to the end of the journal.
    """
    windows, cur = [], None
    for i, ev in enumerate(events):
        if ev.get("type") == "mission_set":
            if cur is not None:
                cur["end_idx"] = i
                windows.append(cur)
            m = (ev.get("data") or {}).get("mission") or {}
            cur = {"mission_id": m.get("id"), "mission": m,
                   "start_idx": i, "end_idx": len(events),
                   "start_seq": ev.get("seq"), "mission_seq": ev.get("seq")}
    if cur is not None:
        windows.append(cur)
    for w in windows:
        w["events"] = events[w["start_idx"]:w["end_idx"]]
        end_ev = events[w["end_idx"] - 1] if w["end_idx"] > w["start_idx"] else None
        w["end_seq"] = end_ev.get("seq") if end_ev else w["start_seq"]
    return windows


def find_project_for_mission(home: str | Path, mission_id: str) -> Path | None:
    """Locate the harness project dir whose journal contains mission_id."""
    home = Path(home)
    projs = home / "projects"
    if not projs.is_dir():
        return None
    for pdir in sorted(projs.iterdir()):
        jf = pdir / "events.jsonl"
        if not jf.is_file():
            continue
        try:
            with open(jf) as f:
                for line in f:
                    line = line.strip()
                    if '"mission_set"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") != "mission_set":
                        continue
                    m = (ev.get("data") or {}).get("mission") or {}
                    if m.get("id") == mission_id:
                        return pdir
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Signal extractors (all journal-pure)
# ---------------------------------------------------------------------------
def _of_type(evs: list[dict], *types: str) -> list[dict]:
    return [e for e in evs if e.get("type") in types]


def _tool_calls(evs: list[dict], name: str | None = None) -> list[dict]:
    out = []
    for e in _of_type(evs, "tool_called"):
        d = e.get("data") or {}
        # Journal key is "tool" (loop.py tool_called events).
        if name is None or d.get("tool") == name:
            out.append(e)
    return out


def _tool_results(evs: list[dict]) -> dict[str, dict]:
    """call_id -> tool_result event data (carries the nested "result")."""
    res: dict[str, dict] = {}
    for e in _of_type(evs, "tool_result"):
        d = e.get("data") or {}
        cid = d.get("call_id")
        if cid:
            res[cid] = d
    return res


def _result_ok(result_data: dict) -> bool | None:
    """True/False/None(unknown) for a tool_result's nested result."""
    r = (result_data or {}).get("result") or {}
    if "exit_code" in r:
        return r["exit_code"] == 0
    if "ok" in r:
        return bool(r["ok"])
    if r.get("error"):
        return False
    return None


def _files_written(evs: list[dict]) -> list[tuple[int, str]]:
    out = []
    for e in _tool_calls(evs, "write_file"):
        d = e.get("data") or {}
        args = d.get("args") or {}
        p = args.get("path") or d.get("path")
        if p:
            out.append((e.get("seq"), str(p)))
    return out


def _iter_run_cmds(evs: list[dict]):
    """Yield (seq, cmd, event) for run_command tool calls."""
    for e in _tool_calls(evs, "run_command"):
        d = e.get("data") or {}
        args = d.get("args") or {}
        c = args.get("cmd") or d.get("cmd")
        if c:
            yield e.get("seq"), str(c), e


def _run_cmds(evs: list[dict]) -> list[tuple[int, str]]:
    return [(s, c) for s, c, _ in _iter_run_cmds(evs)]


def _fail_signature(source: str, parts) -> str:
    norm = sorted({str(p).strip()[:160] for p in parts if str(p).strip()})
    return source + ":" + "|".join(norm)


_NEUTRAL = {"tokens_charged", "egress", "harness_check", "skills_routed",
            "stance_routed", "mode_routed", "tool_called", "progress_recorded",
            "doom_loop_detected", "rigor_report", "learning_recorded",
            "assumption_recorded", "questions_asked", "plan_updated"}
_SUCCESS = {"turn_validated", "verify_passed", "mission_done", "operator_resumed"}


def failure_clusters(evs: list[dict]) -> list[dict]:
    """Group consecutive same-signature failures (turn_rejected/verify_failed).

    Mirrors the loop's doom-loop detector so the coach audits the same
    pattern the harness enforces live: bookkeeping events are skipped,
    success events break the chain, a new signature starts a new cluster.
    """
    clusters: list[dict] = []
    cur: dict | None = None

    def _sig(ev: dict) -> tuple[str, str] | None:
        t = ev.get("type")
        d = ev.get("data") or {}
        if t == "turn_rejected":
            return t, _fail_signature(t, d.get("errors", []))
        if t == "verify_failed":
            failed = [e.get("criterion") or e.get("label") or "?"
                      for e in (d.get("verdict") or [])
                      if e.get("accomplished") != "yes"]
            return t, _fail_signature(t, failed or [d.get("reason", "?")])
        return None

    for ev in evs:
        t = ev.get("type")
        if t in _SUCCESS:
            if cur and len(cur["seqs"]) >= 2:
                clusters.append(cur)
            cur = None
            continue
        if t in _NEUTRAL:
            continue
        parsed = _sig(ev)
        if parsed is None:
            if cur and len(cur["seqs"]) >= 2:
                clusters.append(cur)
            cur = None
            continue
        source, sig = parsed
        if cur is not None and cur["signature"] == sig:
            cur["seqs"].append(ev.get("seq"))
        else:
            if cur and len(cur["seqs"]) >= 2:
                clusters.append(cur)
            cur = {"source": source, "signature": sig, "seqs": [ev.get("seq")]}
    if cur and len(cur["seqs"]) >= 2:
        clusters.append(cur)
    return clusters

# ---------------------------------------------------------------------------
# Checks — each returns a finding dict (evidence-linked, never a guess)
# ---------------------------------------------------------------------------
def _check_observable_proof(evs: list[dict], mission: dict,
                          git_commits: list[tuple[int, str]] | None = None
                          ) -> dict:
    """Timestamp-ordered: the last code change vs the last verification.

    Journal writes (tool_called/write_file) and optional git commits (via
    the read-only probe) both count as code changes; verify_passed events
    and test-run commands count as verification. PASS only when verification
    provably came after the last code change.
    """
    done = _of_type(evs, "mission_done")
    if not done:
        return _finding("observable_proof", UNKNOWN,
                        [f"mission not finished yet (no mission_done; "
                         f"{len(evs)} events so far)"],
                        "Nothing claimed yet — nothing to prove. This check "
                        "activates at mission_done.")
    done_seq = done[-1].get("seq")
    change_ts: list[int] = []
    sources: list[str] = []
    writes = _tool_calls(evs, "write_file")
    if writes:
        change_ts += [e.get("ts", 0) for e in writes]
        sources.append(f"{len(writes)} journal file write(s)")
    if git_commits:
        change_ts += [ts for ts, _ in git_commits]
        sources.append(f"{len(git_commits)} git commit(s)")
    verif_ts: list[int] = []
    for e in _of_type(evs, "verify_passed"):
        verif_ts.append(e.get("ts", 0))
    test_runs = [(e.get("ts", 0), c) for _, c, e in _iter_run_cmds(evs)
                 if _TEST_CMD.search(c)]
    verif_ts += [ts for ts, _ in test_runs]
    code = " + ".join(sources) if sources else "no code changes"
    last_change = max(change_ts) if change_ts else None
    last_verif = max(verif_ts) if verif_ts else None
    if last_change is None and last_verif is None:
        return _finding("observable_proof", UNKNOWN,
                        [f"mission_done seq {done_seq}; no code written and "
                         f"no test/verification evidence — proof rests on "
                         f"the done criteria themselves"],
                        None)
    if last_verif is not None and (last_change is None
                                   or last_verif >= last_change):
        return _finding("observable_proof", PASS,
                        [f"mission_done seq {done_seq}; verification at "
                         f"{last_verif} covers the last code change "
                         f"({code} at {last_change})"])
    if last_change is not None and last_verif is None:
        return _finding("observable_proof", FAIL,
                        [f"mission_done seq {done_seq} after {code}, zero "
                         f"test/verification evidence in the journal"],
                        "Mission claimed done with no verifiable evidence. "
                        "Run the tests / verifier and re-close.")
    return _finding("observable_proof", PARTIAL,
                    [f"mission_done seq {done_seq}; tests ran at {last_verif} "
                     f"but code changed after ({code} at {last_change}) "
                     f"without re-verifying"],
                    "Tests ran, then more code changed. Re-run verification "
                    "after the final change.")


def _check_atomic_transitions(evs: list[dict], mission: dict) -> dict:
    done = _of_type(evs, "mission_done")
    fails = _of_type(evs, "verify_failed")
    passes = _of_type(evs, "verify_passed")
    if done and fails:
        last_fail = fails[-1].get("seq")
        later_pass = [e.get("seq") for e in passes if e.get("seq") > last_fail]
        results = _tool_results(evs)
        for s, c, e in _iter_run_cmds(evs):
            if _TEST_CMD.search(c) and (e.get("seq") or 0) > last_fail:
                d = e.get("data") or {}
                if _result_ok(results.get(d.get("call_id", ""), {})) is True:
                    later_pass.append(e.get("seq"))
        done_seq = done[-1].get("seq")
        if not later_pass and last_fail < done_seq:
            return _finding("atomic_transitions", FAIL,
                            [f"verify_failed seq {last_fail} with no later "
                             f"green verification/test before mission_done "
                             f"seq {done_seq}"],
                            "Shipped over a failing verification — the tree "
                            "moved from broken to 'done' without passing "
                            "through known-good.")
    if _of_type(evs, "rollback"):
        seqs = [e.get("seq") for e in _of_type(evs, "rollback")]
        return _finding("atomic_transitions", PASS,
                        [f"rollback event(s) at seqs {seqs}: broken states "
                         f"were reverted, not committed through"])
    if done and fails:
        return _finding("atomic_transitions", PASS,
                        [f"verify_failed seq {fails[-1].get('seq')} healed by "
                         f"a later green verification/test before "
                         f"mission_done — known-good restored"])
    return _finding("atomic_transitions", UNKNOWN,
                    ["no verify_failed-before-done conflict in the journal; "
                     "commit-level atomicity needs the optional git probe"],
                    None)


def _check_tests_green(evs: list[dict], mission: dict) -> dict:
    """Latest conclusive test/verification outcome decides.

    A stale verify_passed must not mask a later failed test run: the
    newest dated evidence wins, whatever its source.
    """
    passes = _of_type(evs, "verify_passed")
    fails = _of_type(evs, "verify_failed")
    results = _tool_results(evs)
    test_runs = []
    for s, c, e in _iter_run_cmds(evs):
        if _TEST_CMD.search(c):
            d = (e.get("data") or {})
            test_runs.append(
                (s, c, _result_ok(results.get(d.get("call_id", ""), {}))))
    outcomes: list[tuple[int, bool, str]] = []  # (seq, green, source)
    if passes and (not fails or passes[-1].get("seq") > fails[-1].get("seq")):
        outcomes.append((passes[-1].get("seq"), True, "verify_passed"))
    elif fails and (not passes
                    or fails[-1].get("seq") > passes[-1].get("seq")):
        outcomes.append((fails[-1].get("seq"), False, "verify_failed"))
    for s, c, ok in test_runs:
        if ok is not None:
            outcomes.append((s, bool(ok), f"test run '{c[:40]}'"))
    if outcomes:
        seq, green, src_name = max(outcomes)
        if green:
            return _finding("tests_green", PASS,
                            [f"latest conclusive outcome green: {src_name} "
                             f"at seq {seq}"])
        return _finding("tests_green", FAIL,
                        [f"latest conclusive outcome failed: {src_name} "
                         f"at seq {seq}"],
                        "Latest test/verification outcome failed. Nothing "
                        "is green until a green result lands after it.")
    if test_runs:
        return _finding("tests_green", PARTIAL,
                        [f"{len(test_runs)} test command(s) ran, but no "
                         f"journaled verify_passed/verdict and no conclusive "
                         f"exit code"],
                        "Tests ran outside the verifier. Route them through "
                        "verification so the result is journaled evidence.")
    return _finding("tests_green", UNKNOWN,
                    ["no test or verification evidence in the journal"], None)


def _check_tests_run(evs: list[dict], mission: dict) -> dict:
    if (mission.get("kind") or "") == "research":
        return _finding("tests_run", UNKNOWN,
                        ["research mission: tests not applicable"], None)
    test_runs = [(s, c) for s, c in _run_cmds(evs) if _TEST_CMD.search(c)]
    verify = _of_type(evs, "verify_started", "verify_passed", "verify_failed")
    if test_runs or verify:
        return _finding("tests_run", PASS,
                        [f"{len(test_runs)} test command(s), "
                         f"{len(verify)} verification event(s)"])
    if not _tool_calls(evs):
        return _finding("tests_run", UNKNOWN,
                        ["no tool calls at all in this mission"], None)
    return _finding("tests_run", FAIL,
                    [f"{len(_tool_calls(evs))} tool call(s), none of them a "
                     f"test run or verification"],
                    "No tests were run. Add a test step before the next commit.")


def _check_lint(evs: list[dict], mission: dict) -> dict:
    results = _tool_results(evs)
    lint_runs = []
    for s, c, e in _iter_run_cmds(evs):
        if _LINT_CMD.search(c):
            d = e.get("data") or {}
            lint_runs.append(
                (s, c, _result_ok(results.get(d.get("call_id", ""), {}))))
    if not lint_runs:
        if not _files_written(evs):
            return _finding("lint_evidence", UNKNOWN,
                            ["no code written; nothing to lint"], None)
        return _finding("lint_evidence", UNKNOWN,
                        [f"{len(_files_written(evs))} file write(s), no lint "
                         f"command in the journal"],
                        "No lint evidence. Consider a lint step before commit.")
    last_seq, last_cmd, last_ok = lint_runs[-1]
    if last_ok is False:
        return _finding("lint_evidence", FAIL,
                        [f"lint '{last_cmd[:60]}' at seq {last_seq} exited "
                         f"non-zero"],
                        "Lint last failed — clean it before ship.")
    if last_ok is None:
        return _finding("lint_evidence", PARTIAL,
                        [f"lint '{last_cmd[:60]}' at seq {last_seq} ran, "
                         f"but no journaled result (exit code unknown)"],
                        "Journal the lint result so green is provable.")
    return _finding("lint_evidence", PASS,
                    [f"lint '{last_cmd[:60]}' at seq {last_seq} (exit 0)"])


def _check_regression(evs: list[dict], mission: dict) -> dict:
    if (mission.get("kind") or "") != "bugfix":
        return _finding("regression_test", UNKNOWN,
                        [f"mission kind '{mission.get('kind')}' is not bugfix; "
                         f"check not applicable"], None)
    touched = [p for _, p in _files_written(evs)]
    test_files = [p for p in touched if _TEST_PATH.search(p)]
    if test_files:
        return _finding("regression_test", PASS,
                        [f"regression test file(s) touched: "
                         f"{', '.join(test_files[:3])}"])
    return _finding("regression_test", FAIL,
                    [f"bugfix mission touched {len(touched)} file(s), none a "
                     f"test file"],
                    "Bugfix without a regression test. Add one that fails "
                    "before the fix and passes after.")


def _check_three_strike(evs: list[dict], mission: dict) -> dict:
    dooms = _of_type(evs, "doom_loop_detected")
    clusters = [c for c in failure_clusters(evs) if len(c["seqs"]) >= 3]
    if not dooms and not clusters:
        return _finding("three_strike", PASS,
                        ["no 3-identical-failure sequence in the journal"])
    src = dooms if dooms else clusters
    first = src[0]
    if isinstance(first, dict) and "data" in first:  # event form
        d = first.get("data") or {}
        sig, seq = d.get("signature", ""), first.get("seq")
        count = d.get("consecutive", 3)
    else:  # cluster form (pre-breaker journals)
        sig, first_seq, last_seq, count = (first["signature"],
                                          first["seqs"][0], first["seqs"][-1],
                                          len(first["seqs"]))
        seq = last_seq
    # Recovered per protocol? rollback + a later validated turn.
    doom_seq = seq
    rolled_back = any(e.get("seq", 0) > doom_seq
                      for e in _of_type(evs, "rollback"))
    recovered = any(e.get("seq", 0) > doom_seq
                    for e in _of_type(evs, "turn_validated", "verify_passed",
                                      "mission_done"))
    if rolled_back and recovered:
        return _finding("three_strike", PARTIAL,
                        [f"doom loop at seq {doom_seq} ({count}x "
                         f"{sig[:70]}), then rollback + recovery — the "
                         f"protocol was followed after the fact"],
                        "The loop was caught, but only after 3 identical "
                        "failures. Next time: stop at the second repeat.")
    return _finding("three_strike", FAIL,
                    [f"doom loop at seq {doom_seq}: {count}x identical "
                     f"failure ({sig[:70]})"
                     + ("" if recovered else "; never recovered in-journal")],
                    "Same failure 3+ times without rethink. Revert to "
                    "known-good, diagnose in one paragraph, re-approach.")


def _check_error_recovery(evs: list[dict], mission: dict) -> dict:
    clusters = failure_clusters(evs)
    if not clusters:
        return _finding("error_recovery", PASS,
                        ["no failure clusters (2+ consecutive same-signature "
                         "failures) to recover from"])
    step_hits: dict[str, int] = {"STOP": 0, "DIAGNOSE": 0, "ROLLBACK": 0,
                                 "LOG": 0, "RETRY": 0}
    notes = []
    for ci, c in enumerate(clusters):
        seqs = c["seqs"]
        first = seqs[0]
        # Recovery is everything after the cluster up to the next cluster
        # (or the mission end): STOP/DIAGNOSE/ROLLBACK/LOG/RETRY all happen
        # after the failure, not inside it.
        end = (clusters[ci + 1]["seqs"][0] if ci + 1 < len(clusters)
               else 10**18)
        window = [e for e in evs if first <= e.get("seq", 0) < end]
        types = {e.get("type") for e in window}
        # STOP: breaker, escalation, or stall between cluster and retry.
        if types & {"doom_loop_detected", "turn_escalated", "stalled"}:
            step_hits["STOP"] += 1
        # DIAGNOSE+LOG: a learning/progress/assumption record after failure.
        logged = [e for e in window
                  if e.get("type") in ("learning_recorded",
                                       "progress_recorded",
                                       "assumption_recorded")]
        if logged:
            step_hits["DIAGNOSE"] += 1
            step_hits["LOG"] += 1
        else:
            notes.append(f"cluster seqs {seqs}: no diagnosis logged")
        # ROLLBACK: revert to known-good before retrying.
        if "rollback" in types:
            step_hits["ROLLBACK"] += 1
        else:
            notes.append(f"cluster seqs {seqs}: retried without rollback "
                         f"(fix-forward)")
        # RETRY: a validated turn / passed verification afterwards.
        if types & {"turn_validated", "verify_passed", "mission_done"}:
            step_hits["RETRY"] += 1
    n = len(clusters)
    score = sum(step_hits.values()) / (5 * n)
    status = PASS if score >= 0.8 else (PARTIAL if score >= 0.4 else FAIL)
    ev = ([f"{n} failure cluster(s); recovery steps evidenced: "
           + ", ".join(f"{k} {v}/{n}" for k, v in step_hits.items())]
          + notes[:2])
    nudge = None
    if status != PASS:
        missing = [k for k, v in step_hits.items() if v < n]
        nudge = ("Error Recovery Protocol gaps: "
                 + ", ".join(missing)
                 + ". The full loop is STOP → DIAGNOSE → ROLLBACK → LOG → "
                   "RETRY — rollback to known-good before retrying, and "
                   "journal the diagnosis.")
    f = _finding("error_recovery", status, ev, nudge)
    f["value"] = score  # continuous, not bucketed
    return f


def _check_scope(evs: list[dict], mission: dict) -> dict:
    writes = _files_written(evs)
    paths = sorted({p for _, p in writes})
    scope_ev = _of_type(evs, "scope_changed", "contract_approved")
    declared: set[str] = set()
    for e in scope_ev:
        d = e.get("data") or {}
        s = d.get("scope")
        if isinstance(s, list):
            declared.update(str(x) for x in s)
    if not paths:
        return _finding("minimal_authority", PASS,
                        ["no files written; nothing to contain"])
    outside = [p for p in paths
               if declared and not any(p.startswith(d) or d in p
                                       for d in declared)]
    if declared and not outside:
        return _finding("minimal_authority", PASS,
                        [f"{len(paths)} file(s) written, all within declared "
                         f"scope ({len(declared)} scope entries)"])
    if outside:
        ev = [f"{len(paths)} file(s) written; {len(outside)} outside "
              f"declared scope: {', '.join(outside[:4])}"]
        if len(outside) > 5 or len(paths) > 15:
            return _finding("minimal_authority", FAIL, ev,
                            "Minimal Authority: most of the change is "
                            "outside the declared scope. Re-scope the "
                            "contract or narrow the change.")
        return _finding("minimal_authority", PARTIAL, ev,
                        "Files touched outside the declared scope. Either "
                        "widen the scope via contract approval or leave "
                        "those files alone.")
    # No declared scope at all — judge by breadth.
    if len(paths) > 15:
        return _finding("minimal_authority", FAIL,
                        [f"{len(paths)} files touched with no declared scope "
                         f"in the journal: {', '.join(paths[:5])}…"],
                        "Minimal Authority: declare the scope (contract "
                        "approval with a scope list) before touching the "
                        "tree — or narrow the change.")
    if len(paths) > 8:
        return _finding("minimal_authority", PARTIAL,
                        [f"{len(paths)} files touched, no declared scope"],
                        "Wide change with no declared scope. Confirm every "
                        "file is needed; split the rest out.")
    return _finding("minimal_authority", PASS,
                    [f"{len(paths)} file(s) touched"])


def _check_preserved_intent(evs: list[dict], mission: dict) -> dict:
    writes = _files_written(evs)
    if not writes:
        return _finding("preserved_intent", PASS,
                        ["no code modified; nothing to justify"])
    rationale = _of_type(evs, "assumption_recorded", "progress_recorded",
                         "learning_recorded")
    paths = {p for _, p in writes}
    mentioned = set()
    for e in rationale:
        txt = json.dumps(e.get("data") or "").lower()
        for p in paths:
            if Path(p).name.lower() in txt or p.lower() in txt:
                mentioned.add(p)
    if len(mentioned) >= len(paths) / 2:
        return _finding("preserved_intent", PASS,
                        [f"{len(paths)} file(s) modified; rationale recorded "
                         f"for {len(mentioned)}"])
    if not rationale:
        return _finding("preserved_intent", PARTIAL,
                        [f"{len(paths)} file(s) modified, zero rationale "
                         f"records (assumptions/progress/learnings) in the "
                         f"journal"],
                        "Preserved Intent: state what each change does and "
                        "why it is safe — record assumptions before "
                        "modifying code you didn't write.")
    return _finding("preserved_intent", PARTIAL,
                    [f"{len(paths)} file(s) modified; rationale covers "
                     f"{len(mentioned)}/{len(paths)}"],
                    "Some modifications lack a stated rationale. One line "
                    "per file in the journal is enough.")


def _check_uncertainty(evs: list[dict], mission: dict) -> dict:
    blocked = _of_type(evs, "stalled", "turn_escalated", "drift_flagged",
                       "stance_rubric_failed")
    questions = _of_type(evs, "questions_asked")
    n_q = sum(len((e.get("data") or {}).get("questions", [])) for e in questions)
    if not blocked:
        return _finding("declared_uncertainty", PASS,
                        ["no stalls, escalations, or drift flags; nothing "
                         "blocked"])
    if n_q == 0:
        return _finding("declared_uncertainty", FAIL,
                        [f"{len(blocked)} blocked signal(s) "
                         f"({', '.join(sorted({e.get('type') for e in blocked}))}) "
                         f"with zero questions asked"],
                        "Declared Uncertainty: blocked 3+ times without "
                        "asking the user anything. Ask one question instead "
                        "of guessing forward.")
    return _finding("declared_uncertainty", PASS,
                    [f"{len(blocked)} blocked signal(s), {n_q} question(s) "
                     f"asked — uncertainty was declared"])


def _check_task_list(evs: list[dict], mission: dict) -> dict:
    plans = _of_type(evs, "plan_updated")
    criteria = mission.get("done_criteria") or []
    has_plan = any((e.get("data") or {}).get("plan") for e in plans)
    real_criteria = [c for c in criteria
                     if not (isinstance(c, str) and c.strip() == "manual")]
    if has_plan or len(real_criteria) >= 2:
        return _finding("task_list", PASS,
                        [f"ordered task list: "
                         f"{'plan_updated with tasks' if has_plan else ''}"
                         f"{' + ' if has_plan and len(real_criteria) >= 2 else ''}"
                         f"{len(real_criteria)} done criteria"
                         if real_criteria else "plan_updated with tasks"])
    if criteria == ["manual"] or (len(criteria) == 1
                                  and str(criteria[0]).startswith("manual")):
        return _finding("task_list", FAIL,
                        ["done criteria are a single manual checkbox; no "
                         "plan_updated with tasks"],
                        "PLAN.md function: an ordered task list with status. "
                        "Break the mission into checkable tasks.")
    return _finding("task_list", PARTIAL,
                    ["no plan_updated with tasks; done criteria are thin"],
                    "Record the task breakdown so progress is checkable.")


def _check_learnings(evs: list[dict], mission: dict) -> dict:
    learned = _of_type(evs, "learning_recorded", "synthesis_admitted")
    done = _of_type(evs, "mission_done")
    if learned:
        return _finding("learnings", PASS,
                        [f"{len(learned)} learning/synthesis record(s) "
                         f"(seqs {[e.get('seq') for e in learned][:3]})"])
    if done:
        return _finding("learnings", PARTIAL,
                        ["mission_done with zero learnings recorded"],
                        "learned_rules function: persist what this mission "
                        "taught (learning_recorded) so the next one is "
                        "cheaper.")
    return _finding("learnings", UNKNOWN,
                    ["mission in flight; learnings usually land at the end"],
                    None)


def _check_decision_log(evs: list[dict], mission: dict) -> dict:
    notes = _of_type(evs, "progress_recorded")
    # The journal itself IS the append-only log — it always exists.
    base = [f"journal append-only by construction ({len(evs)} events)"]
    if notes:
        return _finding("decision_log", PASS,
                        base + [f"{len(notes)} progress note(s)"])
    if len(evs) > 30:
        return _finding("decision_log", PARTIAL,
                        base + ["no progress_recorded in a long mission"],
                        "progress_log.md function: leave decision breadcrumbs "
                        "(progress_recorded) so the trail is resumable.")
    return _finding("decision_log", PASS, base)


def _check_user_overrides(evs: list[dict]) -> list[dict]:
    """User overrides: evidence-linked, informational, never penalized.

    The user is the top principal. When the user explicitly overrides a
    guardrail, the coach logs it as a finding — not a lecture, not a score
    hit.
    """
    out = []
    denied: dict[str, int] = {}
    for e in evs:
        t, d = e.get("type"), e.get("data") or {}
        # approval_denied/granted carry the approval id — a later grant for
        # the same id after a denial is an explicit principal override.
        if t == "approval_denied" and d.get("id"):
            denied[str(d["id"])] = e.get("seq")
        elif t == "approval_granted" and str(d.get("id")) in denied:
            s = denied[str(d["id"])]
            out.append({
                "seq": e.get("seq"),
                "kind": "approval_override",
                "detail": (f"user granted approval {d.get('id')} at seq "
                           f"{e.get('seq')} after denying it at seq {s} — "
                           f"principal override, logged not penalized"),
            })
        elif t == "operator_resumed":
            prior = [x for x in evs
                     if x.get("type") in ("turn_escalated", "stalled")
                     and x.get("seq", 0) < e.get("seq", 0)]
            if prior:
                out.append({
                    "seq": e.get("seq"),
                    "kind": "operator_takeover",
                    "detail": (f"user resumed at seq {e.get('seq')} after "
                               f"{prior[-1].get('type')} seq "
                               f"{prior[-1].get('seq')} — principal override, "
                               f"logged not penalized"),
                })
    return out

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_mission(events: list[dict], mission: dict,
                 repo: str | Path | None = None) -> dict:
    """Score one mission window. Returns the full RigorReport dict."""
    git_commits = None
    if repo is not None:
        git_commits, _unpushed = _git_commits_in_window(repo, events)
    checks = [
        _check_observable_proof(events, mission, git_commits=git_commits),
        _check_atomic_transitions(events, mission),
        _check_tests_green(events, mission),
        _check_three_strike(events, mission),
        _check_error_recovery(events, mission),
        _check_scope(events, mission),
        _check_regression(events, mission),
        _check_preserved_intent(events, mission),
        _check_uncertainty(events, mission),
        _check_tests_run(events, mission),
        _check_lint(events, mission),
        _check_task_list(events, mission),
        _check_learnings(events, mission),
        _check_decision_log(events, mission),
    ]
    if repo is not None:
        # The git probe supersedes the journal-only atomicity check —
        # replace, don't duplicate.
        checks = [c for c in checks if c["check"] != "atomic_transitions"]
        checks.insert(1, _check_git_atomicity(events, mission, repo))
    overrides = _check_user_overrides(events)
    scored = [c for c in checks if c["value"] is not None]
    total_w = sum(c["weight"] for c in scored)
    score = (sum(c["weight"] * c["value"] for c in scored) / total_w
             if total_w else 0.0)
    return {
        "mission_id": mission.get("id"),
        "mission_text": mission.get("text"),
        "mission_kind": mission.get("kind"),
        "score": round(score, 3),
        "scored_weight": round(total_w, 2),
        "n_checks": len(checks),
        "n_scored": len(scored),
        "checks": checks,
        "overrides": overrides,   # informational — never scored
        "failing": [c["check"] for c in scored if c["status"] == FAIL],
        "unknown": [c["check"] for c in checks if c["status"] == UNKNOWN],
    }


def _git_commits_in_window(
        repo: str | Path,
        evs: list[dict]) -> tuple[list[tuple[int, str]] | None, int | None]:
    """Bounded read-only git probe: commit subjects in the mission window.

    Never executes project code -- `git log` + `git rev-list` only.
    Returns (commits, unpushed_count); commits is None when the probe is
    unavailable. Any failure degrades to (None, None), never raises.
    """
    try:
        start_ts = evs[0].get("ts") if evs else None
        end_ts = evs[-1].get("ts") if evs else None
        cmd = ["git", "-C", str(repo), "log", "--format=%ct %h %s"]
        if start_ts:
            cmd.append(f"--since=@{int(start_ts)}")
        if end_ts:
            cmd.append(f"--until=@{int(end_ts) + 1}")
        env = {"GIT_PAGER": "cat", "PATH": "/usr/bin:/bin",
               "GIT_TERMINAL_PROMPT": "0"}
        # Bounded: cap output, short timeout, no pager.
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           env=env)
        if p.returncode != 0:
            raise RuntimeError(p.stderr[:120])
        commits = []
        for line in p.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            ts = int(parts[0]) if parts and parts[0].isdigit() else 0
            commits.append((ts, parts[2] if len(parts) > 2 else line))
            if len(commits) >= 50:
                break
        # Unpushed commits: read-only rev-list against the upstream.
        up = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--count", "@{u}..HEAD"],
            capture_output=True, text=True, timeout=15, env=env)
        unpushed = int(up.stdout.strip()) if up.returncode == 0 else None
        return commits, unpushed
    except Exception:  # noqa: BLE001 - probe is best-effort by design
        return None, None


def _check_git_atomicity(evs: list[dict], mission: dict,
                        repo: str | Path) -> dict:
    """Optional bounded read-only git probe: commits in the mission window.

    Never executes project code — `git log` only. Any failure degrades to
    UNKNOWN, never FAIL.
    """
    commits, unpushed = _git_commits_in_window(repo, evs)
    if commits is None:
        return _finding("atomic_transitions", UNKNOWN,
                        ["git probe unavailable"], None)
    if not commits:
        return _finding("atomic_transitions", UNKNOWN,
                        ["no commits in the mission window"], None)
    # commits are (committer_ts, "hash subject") pairs.
    bad = [line for _ts, line in commits
           if re.search(r"\b(wip|broken|fixup|tmp|do-not-merge)\b",
                        line, re.I)]
    notes = [f"{len(commits)} commit(s) in window"]
    partial_reasons = []
    if bad:
        partial_reasons.append("suspect subjects: " + "; ".join(bad[:3]))
    if unpushed is None:
        notes.append("no upstream configured — push state unknown")
    elif unpushed > 0:
        partial_reasons.append(f"{unpushed} commit(s) not pushed to remote")
    if partial_reasons:
        return _finding("atomic_transitions", PARTIAL,
                        notes + partial_reasons,
                        "Atomic transitions: " + "; ".join(partial_reasons)
                        + ". Push finished work; never commit broken states.")
    ev = notes + ["no broken-state markers in subjects"]
    if unpushed == 0:
        ev.append("all commits pushed")
    return _finding("atomic_transitions", PASS, ev)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
_STATUS_MARK = {PASS: "✓", PARTIAL: "~", FAIL: "✗", UNKNOWN: "?", INFO: "i"}


def render_text(report: dict) -> str:
    """Concise human report."""
    L = [f"Rigor report — mission {report['mission_id']} "
         f"({report['mission_kind']})",
         f"RigorScore: {report['score']:.2f} "
         f"({report['n_scored']}/{report['n_checks']} checks scored)"]
    for c in report["checks"]:
        mark = _STATUS_MARK[c["status"]]
        L.append(f"  [{mark}] {c['check']} ({c['law']}): {c['status']}")
        for ev in c["evidence"][:2]:
            L.append(f"      · {ev[:150]}")
        if c["nudge"]:
            L.append(f"      → {c['nudge'][:220]}")
    for o in report["overrides"]:
        L.append(f"  [i] user override (seq {o['seq']}): {o['detail'][:160]}")
    return "\n".join(L)


def render_markdown(report: dict) -> str:
    L = [f"# Rigor report — `{report['mission_id']}`",
         f"**RigorScore: {report['score']:.2f}** "
         f"({report['n_scored']}/{report['n_checks']} checks scored)",
         ""]
    for c in report["checks"]:
        mark = _STATUS_MARK[c["status"]]
        L.append(f"## [{mark}] {c['check']} — {c['status']}")
        L.append(f"*{c['law']}* — {c['title']}")
        for ev in c["evidence"]:
            L.append(f"- {ev}")
        if c["nudge"]:
            L.append(f"> {c['nudge']}")
        L.append("")
    if report["overrides"]:
        L.append("## User overrides (principal — logged, not penalized)")
        for o in report["overrides"]:
            L.append(f"- seq {o['seq']}: {o['detail']}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def score_project_events(project_dir: str | Path, mission_id: str | None = None,
                         recent: int | None = None,
                         repo: str | Path | None = None) -> dict | list[dict]:
    """Read-only scoring from a project's events.jsonl. No writes."""
    events = load_events(project_dir)
    windows = mission_windows(events)
    if not windows:
        raise ValueError("no missions in journal")
    if mission_id:
        wins = [w for w in windows if w["mission_id"] == mission_id]
        if not wins:
            raise ValueError(f"mission {mission_id!r} not in journal")
        return score_mission(wins[-1]["events"], wins[-1]["mission"],
                             repo=repo)
    if recent:
        wins = windows[-recent:]
        return [score_mission(w["events"], w["mission"], repo=repo)
                for w in wins]
    w = windows[-1]
    return score_mission(w["events"], w["mission"], repo=repo)


def report_rigor(state, mission_id: str | None = None,
                recent: int | None = None, record: bool = True,
                repo: str | Path | None = None) -> dict | list[dict]:
    """Score mission(s) from a live State and journal the rigor_report event.

    The journal write is the coach's ONLY write: the report becomes evidence.
    `state` is the harness State (uses .events and .record()).
    """
    events = list(state.events)
    windows = mission_windows(events)
    if not windows:
        raise ValueError("no missions in journal")
    if mission_id:
        wins = [w for w in windows if w["mission_id"] == mission_id]
        if not wins:
            raise ValueError(f"mission {mission_id!r} not in journal")
        targets = wins[-1:]
    elif recent:
        targets = windows[-recent:]
    else:
        targets = windows[-1:]
    reports = [score_mission(w["events"], w["mission"], repo=repo)
               for w in targets]
    if record:
        for rep in reports:
            state.record("rigor_report", {
                "mission_id": rep["mission_id"],
                "score": rep["score"],
                "scored": f"{rep['n_scored']}/{rep['n_checks']}",
                "failing": rep["failing"],
                "unknown": rep["unknown"],
                "overrides": len(rep["overrides"]),
                "checks": {c["check"]: c["status"] for c in rep["checks"]},
            })
    return reports[0] if len(reports) == 1 else reports


# ---------------------------------------------------------------------------
# RigorJudge rules — extra_rules for ScriptedJudge/DeterministicJudge.
# Signature: (turn, summary) -> reason|None. Kept here (not judges.py) so the
# import direction stays one-way: judges.py lazily imports rigor, never the
# reverse.
# ---------------------------------------------------------------------------
_VERIFY_WORDS = ("tests pass", "tests green", "all tests pass", "verified",
                 "verification passed")


def _rule_r3_no_verify_done(turn: dict, summary: dict):
    """R3: done_claim with zero verification evidence this session.

    R2 (in the judge) catches done_claim against visibly unmet criteria; R3
    catches the stronger rigor violation — claiming done when the session
    contains no verify event and no test run at all.
    """
    if not turn.get("done_claim"):
        return None
    if summary.get("verify_events", 0) > 0:
        return None
    if summary.get("test_runs", 0) > 0:
        return None
    return ("R3: done_claim with no verification evidence this session "
            "(no verify events, no test runs)")


def _rule_r4_verify_claim(turn: dict, summary: dict):
    """R4: 'tests pass'/'verified' language with no evidence behind it.

    Extends R1's completion-language rule to verification claims: asserting
    green tests while the session shows no tool results and no verify events
    is an unverifiable claim (Law 1).
    """
    if turn.get("done_claim"):
        return None  # R2/R3 own done_claim
    pd = (turn.get("progress_delta") or "").lower()
    if not any(w in pd for w in _VERIFY_WORDS):
        return None
    if summary.get("results_this_session", 0) > 0:
        return None
    if summary.get("verify_events", 0) > 0:
        return None
    return ("R4: verification claim ('tests pass'/'verified') with no tool "
            "results and no verify events this session")


def rigor_extra_rules() -> list:
    """Extra judge rules implementing Laws 1–2 at turn scope."""
    return [_rule_r3_no_verify_done, _rule_r4_verify_claim]


# ---------------------------------------------------------------------------
# Five Laws rubric — the coach's core, for SPEC/docs reference.
# ---------------------------------------------------------------------------
FIVE_LAWS = [
    ("Law 1 — Observable Proof",
     "Every completion claim needs verifiable evidence. No exceptions.",
     ["observable_proof", "tests_run", "tests_green", "regression_test"]),
    ("Law 2 — Atomic State Transitions",
     "Known-good → known-good. Broken states are reverted, never committed.",
     ["atomic_transitions", "lint_evidence"]),
    ("Law 3 — Preserved Intent",
     "Never change code whose purpose you cannot articulate.",
     ["preserved_intent"]),
    ("Law 4 — Declared Uncertainty",
     "Say 'I don't know' immediately. Fabricating knowledge is critical.",
     ["declared_uncertainty"]),
    ("Law 5 — Minimal Authority",
     "Only the permissions, files, and scope the task needs.",
     ["minimal_authority"]),
]
