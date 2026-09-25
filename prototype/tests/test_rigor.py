"""Rigor coach: journal-evidence scoring, circuit breaker, judge rules."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parent.parent)

import rigor
from rigor import (score_project_events, mission_windows, failure_clusters,
                   rigor_extra_rules)
from skills import SkillStore, SkillIntegrityError


def _journal(home, project, spec, ts_base=None):
    """Write a synthetic events.jsonl. spec = [(type, data), ...]."""
    pdir = Path(home) / "projects" / project
    pdir.mkdir(parents=True, exist_ok=True)
    base = ts_base if ts_base is not None else 1700000000.0
    with open(pdir / "events.jsonl", "w") as f:
        for i, (t, d) in enumerate(spec):
            f.write(json.dumps({"seq": i, "id": f"e{i}",
                                "ts": base + i, "type": t,
                                "data": d}) + "\n")
    return pdir


def _mission(mid, kind="build", criteria=None, text=None):
    return ("mission_set",
            {"mission": {"id": mid, "text": text or f"mission {mid}",
                         "kind": kind, "revision": 1,
                         "done_criteria": criteria if criteria is not None
                         else ["criterion a", "criterion b"]}})


def _call(cid, tool, args):
    return ("tool_called", {"call_id": cid, "tool": tool, "args": args,
                            "idem_key": "k" + cid})


def _res(cid, tool="run_command", exit_code=0):
    return ("tool_result",
            {"call_id": cid, "tool": tool, "args": {},
             "result": {"cmd": "x", "exit_code": exit_code,
                        "stdout": "", "stderr": ""}})


def _perfect(mid="m-perfect"):
    return [
        _mission(mid),
        ("plan_updated", {"plan": ["write code", "test", "ship"]}),
        ("contract_approved", {"scope": ["src/"]}),
        ("assumption_recorded",
         {"assumption": "src/app.py holds the login handler; safe to extend"}),
        _call("c1", "write_file", {"path": "src/app.py", "content": "x"}),
        _res("c1", "write_file"),
        _call("c2", "run_command", {"cmd": "pytest -q"}),
        _res("c2"),
        _call("c3", "run_command", {"cmd": "ruff check src/"}),
        _res("c3"),
        ("verify_passed", {"worker_id": "w1", "verdict": []}),
        # Live-State-compatible shapes (state.apply_event requires them).
        ("learning_recorded", {"kind": "note", "text": "pytest needs -q"}),
        ("progress_recorded", {"turn_id": "t1", "delta": "all green"}),
        ("mission_done", {"mission_id": mid}),
    ]


def _check(report, name):
    for c in report["checks"]:
        if c["check"] == name:
            return c
    raise AssertionError(f"check {name} missing")


class TestRigorSkills(unittest.TestCase):
    def test_pins_load(self):
        st = SkillStore.default()
        names = st.names()
        self.assertEqual(len(names), 53)  # 29 + 14 osmani-* + definition-of-done + lifecycle-sequence + critical-thinking + completion-summary + incident-response + dora-metrics + durable-memory + debug + rpi + layered-loading
        for n in ("rigor-laws", "rigor-distillation", "rigor-interrogation",
                  "rigor-decomposition", "rigor-iteration",
                  "rigor-checkpoint", "rigor-proof-cycles",
                  "rigor-pentagonal-audit", "rigor-entropy",
                  "rigor-three-strike", "rigor-scope"):
            self.assertIn(n, names)
            body = st.get_verified(n)
            self.assertIn("agent-rigor", body)

    def test_tampered_rigor_skill_fails_closed(self):
        from skills import STORE_DIR
        p = (Path(__file__).resolve().parent.parent
             / "skills" / "rigor-scope.md")
        original = p.read_bytes()
        try:
            p.write_bytes(original + b"\n# tampered")
            # Fresh store instance: the packaged default() is a verified
            # singleton, so re-verification needs a new load.
            with self.assertRaises(SkillIntegrityError):
                SkillStore(STORE_DIR)
        finally:
            p.write_bytes(original)
        # Restored bytes load cleanly again.
        self.assertIn("rigor-scope", SkillStore(STORE_DIR).names())

    def test_five_laws_skill_content(self):
        body = SkillStore.default().get_verified("rigor-laws")
        for law in ("Observable Proof", "Atomic State Transitions",
                    "Preserved Intent", "Declared Uncertainty",
                    "Minimal Authority"):
            self.assertIn(law, body)
        # The Awino adaptation: user is top principal, override is journaled.
        self.assertIn("top principal", body)


class TestRigorScoring(unittest.TestCase):
    def test_perfect_mission_high_score(self):
        home = tempfile.mkdtemp(prefix="rigor-test-")
        pdir = _journal(home, "p1", _perfect())
        rep = score_project_events(pdir)
        self.assertGreaterEqual(rep["score"], 0.80, rep)
        self.assertEqual(rep["failing"], [])
        self.assertEqual(_check(rep, "observable_proof")["status"], "pass")
        self.assertEqual(_check(rep, "regression_test")["status"], "unknown")

    def test_sloppy_mission_low_score_with_exact_nudges(self):
        evs = [_mission("m-sloppy", criteria=["manual"])]
        for i in range(12):
            evs.append(_call(f"w{i}", "write_file",
                             {"path": f"src/mod{i}.py", "content": "x"}))
            evs.append(_res(f"w{i}", "write_file"))
        evs.append(("mission_done", {"mission_id": "m-sloppy"}))
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        # Sloppy but failure-free: no doom-loop/recovery penalties apply, so
        # the score lands low rather than at rock bottom.
        self.assertLess(rep["score"], 0.60, rep)
        self.assertEqual(sorted(rep["failing"]),
                         ["observable_proof", "task_list", "tests_run"])
        op = _check(rep, "observable_proof")
        self.assertEqual(op["status"], "fail")
        self.assertIn("no verifiable evidence", op["nudge"])
        tr = _check(rep, "tests_run")
        self.assertEqual(tr["status"], "fail")
        tl = _check(rep, "task_list")
        self.assertEqual(tl["status"], "fail")
        self.assertIn("task list", tl["nudge"])

    def test_doom_loop_detected(self):
        evs = [_mission("m-doom")]
        for i in range(3):
            evs.append(("turn_rejected",
                        {"turn_id": "t1", "attempt": i,
                         "errors": ["header mismatch"]}))
            evs.append(("tokens_charged", {"tokens": 10}))  # neutral: skipped
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        ts = _check(rep, "three_strike")
        self.assertEqual(ts["status"], "fail")
        self.assertIn("3x", ts["evidence"][0])
        er = _check(rep, "error_recovery")
        self.assertIn(er["status"], ("fail", "partial"))
        self.assertIn("ROLLBACK", er["nudge"])

    def test_bugfix_without_regression_test(self):
        evs = [_mission("m-bug", kind="bugfix"),
               _call("c1", "write_file",
                     {"path": "src/app.py", "content": "fixed"}),
               _res("c1", "write_file"),
               ("mission_done", {"mission_id": "m-bug"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        rt = _check(rep, "regression_test")
        self.assertEqual(rt["status"], "fail")
        self.assertIn("regression test", rt["nudge"])

    def test_bugfix_with_regression_test_passes(self):
        evs = [_mission("m-bug2", kind="bugfix"),
               _call("c1", "write_file",
                     {"path": "src/app.py", "content": "fixed"}),
               _res("c1", "write_file"),
               _call("c2", "write_file",
                     {"path": "tests/test_app.py", "content": "t"}),
               _res("c2", "write_file"),
               ("mission_done", {"mission_id": "m-bug2"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        self.assertEqual(_check(rep, "regression_test")["status"], "pass")

    def test_scope_outside_declared(self):
        evs = [_mission("m-scope"),
               ("contract_approved", {"scope": ["src/"]}),
               _call("c1", "write_file", {"path": "src/a.py", "content": "x"}),
               _res("c1", "write_file"),
               _call("c2", "write_file",
                     {"path": "other/b.py", "content": "x"}),
               _res("c2", "write_file")]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        sc = _check(rep, "minimal_authority")
        self.assertEqual(sc["status"], "partial")
        self.assertIn("other/b.py", sc["evidence"][0])

    def test_error_recovery_rollback_vs_fixforward(self):
        def _fails(mid):
            v = [_mission(mid)]
            for i in range(2):
                v.append(("verify_failed",
                          {"worker_id": "w", "verdict":
                           [{"criterion": "tests green",
                             "accomplished": "no"}]}))
            return v
        home = tempfile.mkdtemp(prefix="rigor-test-")
        # Fix-forward: failures then success, no rollback, no diagnosis.
        evs = _fails("m-ff") + [("verify_passed",
                                 {"worker_id": "w", "verdict": []})]
        rep = score_project_events(_journal(home, "p1", evs))
        er = _check(rep, "error_recovery")
        self.assertEqual(er["status"], "fail")
        self.assertIn("ROLLBACK", er["nudge"])
        # Protocol: STOP via breaker + rollback + logged diagnosis + retry.
        evs = [_mission("m-proto")]
        for i in range(3):
            evs.append(("verify_failed",
                        {"worker_id": "w", "verdict":
                         [{"criterion": "tests green",
                           "accomplished": "no"}]}))
        evs += [("doom_loop_detected",
                 {"source": "verify_failed", "signature": "s",
                  "consecutive": 3}),
                ("rollback", {"to_seq": 0}),
                ("learning_recorded",
                 {"learning": "diagnosis: the test needs a fixture"}),
                ("verify_passed", {"worker_id": "w", "verdict": []})]
        rep = score_project_events(_journal(home, "p2", evs))
        er = _check(rep, "error_recovery")
        self.assertEqual(er["status"], "pass")
        ts = _check(rep, "three_strike")
        # Recovered via rollback: partial, not fail.
        self.assertEqual(ts["status"], "partial")

    def test_user_override_logged_not_penalized(self):
        evs = [_mission("m-ov"),
               ("approval_denied", {"id": "ap1"}),
               ("approval_granted", {"id": "ap1"}),
               ("mission_done", {"mission_id": "m-ov"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        self.assertEqual(len(rep["overrides"]), 1)
        self.assertEqual(rep["overrides"][0]["kind"], "approval_override")
        # Overrides never enter the scored weight.
        scored_names = [c["check"] for c in rep["checks"]
                        if c["value"] is not None]
        self.assertNotIn("user_overrides", scored_names)

    def test_file_convention_functions(self):
        evs = [_mission("m-fc"),
               ("plan_updated", {"plan": ["a", "b"]}),
               ("progress_recorded", {"note": "did a"}),
               ("learning_recorded", {"learning": "x"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        self.assertEqual(_check(rep, "task_list")["status"], "pass")
        self.assertEqual(_check(rep, "decision_log")["status"], "pass")
        self.assertEqual(_check(rep, "learnings")["status"], "pass")

    def test_recent_and_mission_windows(self):
        home = tempfile.mkdtemp(prefix="rigor-test-")
        pdir = _journal(home, "p1", _perfect("m1") + _perfect("m2"))
        one = score_project_events(pdir, mission_id="m1")
        self.assertEqual(one["mission_id"], "m1")
        many = score_project_events(pdir, recent=2)
        self.assertEqual([r["mission_id"] for r in many], ["m1", "m2"])
        with self.assertRaises(ValueError):
            score_project_events(pdir, mission_id="nope")

    def test_failure_clusters_mirror_loop_detector(self):
        evs = [_mission("m-c")]
        for i in range(2):
            evs.append(("turn_rejected", {"errors": ["same boom"]}))
        evs.append(("turn_validated", {"turn_id": "t"}))  # breaks the chain
        evs.append(("turn_rejected", {"errors": ["same boom"]}))
        home = tempfile.mkdtemp(prefix="rigor-test-")
        pdir = _journal(home, "p1", evs)
        clusters = failure_clusters(rigor.load_events(pdir))
        # The validated turn breaks consecutiveness: only one 2-cluster.
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]["seqs"]), 2)


def _git_repo(with_remote=False):
    d = tempfile.mkdtemp(prefix="rigor-git-")
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if with_remote:
        # A real clone: the branch tracks origin/master, so @{u} resolves.
        bare = tempfile.mkdtemp(prefix="rigor-bare-")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True,
                       env=env)
        seed = tempfile.mkdtemp(prefix="rigor-seed-")
        subprocess.run(["git", "init", "-q", seed], check=True, env=env)
        subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare],
                       check=True, env=env)
        Path(seed, "f.txt").write_text("seed")
        subprocess.run(["git", "-C", seed, "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", seed, "commit", "-qm", "seed"],
                       check=True, env=env)
        subprocess.run(["git", "-C", seed, "push", "-q", "origin",
                        "HEAD:master"], check=True, env=env)
        subprocess.run(["git", "clone", "-q", bare, d], check=True, env=env)
        subprocess.run(["git", "-C", d, "config", "user.name", "t"], env=env)
        subprocess.run(["git", "-C", d, "config", "user.email", "t@t"],
                       env=env)
        return d, env, bare
    subprocess.run(["git", "init", "-q", d], check=True, env=env)
    return d, env, None


def _commit(d, env, msg, when=None):
    Path(d, "f.txt").write_text("x")
    subprocess.run(["git", "-C", d, "add", "."], check=True, env=env)
    cenv = dict(env)
    if when is not None:
        cenv["GIT_COMMITTER_DATE"] = f"@{int(when)}"
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", msg,
                        f"--date=@{int(when)}"], check=True, env=cenv)
    else:
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", msg],
                       check=True, env=cenv)


class TestRigorGitProbe(unittest.TestCase):
    def test_commit_before_green_wip_flagged(self):
        d, env, _ = _git_repo()
        base = 1700000000.0
        _commit(d, env, "WIP stuff", when=base)
        evs = [_mission("m-git"),
               ("mission_done", {"mission_id": "m-git"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        pdir = _journal(home, "p1", evs, ts_base=base)
        rep = score_project_events(pdir, repo=d)
        at = _check(rep, "atomic_transitions")
        self.assertEqual(at["status"], "partial")
        self.assertTrue(any("WIP" in e for e in at["evidence"]), at["evidence"])
        self.assertIn("broken", at["nudge"])
        # And the journal-pure proof check still fires: done, no tests.
        self.assertEqual(_check(rep, "observable_proof")["status"], "fail")

    def test_unpushed_commit_flagged(self):
        d, env, _ = _git_repo(with_remote=True)
        base = 1700000000.0
        _commit(d, env, "add feature", when=base)  # never pushed
        evs = [_mission("m-push"),
               ("mission_done", {"mission_id": "m-push"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        pdir = _journal(home, "p1", evs, ts_base=base)
        rep = score_project_events(pdir, repo=d)
        at = _check(rep, "atomic_transitions")
        self.assertEqual(at["status"], "partial")
        self.assertTrue(any("not pushed" in e for e in at["evidence"]),
                        at["evidence"])

    def test_git_probe_degrades_to_unknown(self):
        evs = [_mission("m-nogit"), ("mission_done", {"mission_id": "m-nogit"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        pdir = _journal(home, "p1", evs)
        rep = score_project_events(pdir, repo="/nonexistent-dir")
        self.assertEqual(_check(rep, "atomic_transitions")["status"],
                         "unknown")

    def test_stale_verify_passed_cannot_mask_failed_test_run(self):
        # verify_passed at seq T, then a test run FAILS after it: the latest
        # conclusive outcome is red → tests_green FAIL, not PASS.
        evs = ([_mission("m-stale")]
               + [_call("c1", "run_command", {"cmd": "pytest -q"}),
                  _res("c1", exit_code=0)]
               + [("verify_passed", {"worker_id": "w1", "verdict": []})]
               + [_call("c2", "run_command", {"cmd": "pytest -q"}),
                  _res("c2", exit_code=1)]
               + [("mission_done", {"mission_id": "m-stale"})])
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        tg = _check(rep, "tests_green")
        self.assertEqual(tg["status"], "fail")
        self.assertIn("latest conclusive", tg["evidence"][0])

    def test_lint_without_journaled_result_is_partial(self):
        evs = ([_mission("m-lint")]
               + [_call("c1", "run_command", {"cmd": "ruff check src/"}),
                  ("tool_result", {"call_id": "c1", "tool": "run_command"})]
               + [("mission_done", {"mission_id": "m-lint"})])
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        self.assertEqual(_check(rep, "lint_evidence")["status"], "partial")

    def test_green_test_run_heals_verify_failed_for_atomicity(self):
        evs = ([_mission("m-heal")]
               + [("verify_failed", {"verdict": [{"criterion": "x",
                                                  "accomplished": "no"}]})]
               + [_call("c1", "run_command", {"cmd": "pytest -q"}),
                  _res("c1", exit_code=0)]
               + [("mission_done", {"mission_id": "m-heal"})])
        home = tempfile.mkdtemp(prefix="rigor-test-")
        rep = score_project_events(_journal(home, "p1", evs))
        at = _check(rep, "atomic_transitions")
        self.assertNotEqual(at["status"], "fail", at)

    def test_tests_after_commit_prove_it(self):
        # Commit at T, test run journaled after T: verification provably
        # covers the commit → observable_proof PASS even with --repo.
        d, env, _ = _git_repo()
        base = 1700000000.0
        evs = [_mission("m-tc"),
               _call("c1", "run_command", {"cmd": "pytest -q"}),
               _res("c1"),
               ("mission_done", {"mission_id": "m-tc"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        pdir = _journal(home, "p1", evs, ts_base=base)
        _commit(d, env, "add feature", when=base + 1)  # before the test run
        rep = score_project_events(pdir, repo=d)
        self.assertEqual(_check(rep, "observable_proof")["status"], "pass")

    def test_tests_before_commit_only_partial(self):
        # Tests journaled before the commit: can't prove they covered it.
        d, env, _ = _git_repo()
        base = 1700000000.0
        evs = [_mission("m-tb"),
               _call("c1", "run_command", {"cmd": "pytest -q"}),
               _res("c1"),
               ("mission_done", {"mission_id": "m-tb"})]
        home = tempfile.mkdtemp(prefix="rigor-test-")
        pdir = _journal(home, "p1", evs, ts_base=base)
        _commit(d, env, "add feature", when=base + 3)  # after the test run
        rep = score_project_events(pdir, repo=d)
        self.assertEqual(_check(rep, "observable_proof")["status"], "partial")


class TestDoomLoopBreaker(unittest.TestCase):
    def test_three_identical_rejections_trip_breaker(self):
        from tests.common import make_loop
        from backends import ScriptedBackend, ScriptedJudge
        loop, _home = make_loop(backend=ScriptedBackend([]),
                                judge=ScriptedJudge())
        loop.set_mission("x", ["a"])
        sig = loop._failure_signature("turn_rejected", ["header mismatch"])
        for i in range(3):
            loop.state.record("turn_rejected",
                              {"turn_id": "t1", "attempt": i,
                               "errors": ["header mismatch"]})
            loop.state.record("tokens_charged", {"tokens": 5})
        tripped = loop._check_doom_loop("turn_rejected", sig)
        self.assertTrue(tripped)
        dooms = [e for e in loop.state.events
                 if e["type"] == "doom_loop_detected"]
        self.assertEqual(len(dooms), 1)
        self.assertEqual(dooms[0]["data"]["consecutive"], 3)
        self.assertTrue(loop.state.snapshot["doom_loop_active"])
        # The next routed turn carries the rethink skill (layered: the ONLY
        # way rigor-three-strike enters context).
        routed = loop._sensor_route("hello", "info")
        self.assertIn("rigor-three-strike", routed["skills"])
        # A validated turn clears the breaker.
        loop.state.record("turn_validated", {"turn_id": "t2", "attempt": 0})
        self.assertFalse(loop.state.snapshot["doom_loop_active"])
        routed = loop._sensor_route("hello", "info")
        self.assertNotIn("rigor-three-strike", routed["skills"])

    def test_different_signature_resets_chain(self):
        from tests.common import make_loop
        from backends import ScriptedBackend, ScriptedJudge
        loop, _home = make_loop(backend=ScriptedBackend([]),
                                judge=ScriptedJudge())
        loop.set_mission("x", ["a"])
        for i in range(2):
            loop.state.record("turn_rejected",
                              {"turn_id": "t1", "attempt": i,
                               "errors": ["boom A"]})
        loop.state.record("turn_rejected",
                          {"turn_id": "t1", "attempt": 2,
                           "errors": ["boom B"]})  # new approach
        sig = loop._failure_signature("turn_rejected", ["boom B"])
        self.assertFalse(loop._check_doom_loop("turn_rejected", sig))

    def test_doom_loop_feedback_text(self):
        from tests.common import make_loop
        from backends import ScriptedBackend, ScriptedJudge
        loop, _home = make_loop(backend=ScriptedBackend([]),
                                judge=ScriptedJudge())
        fb = loop._doom_loop_feedback()
        self.assertIn("STOP", fb)
        self.assertIn("rigor-three-strike", fb)


class TestRigorSidecar(unittest.TestCase):
    def test_report_round_trip_journals_event(self):
        from tests.common import make_loop
        from backends import ScriptedBackend, ScriptedJudge
        import awino_sidecar
        loop, _home = make_loop(backend=ScriptedBackend([]),
                                judge=ScriptedJudge())
        for t, d in _perfect("m-side"):
            loop.state.record(t, d)
        sc = awino_sidecar.Sidecar()
        sc.loop = loop
        out = sc._cmd_rigor_report({})
        self.assertTrue(out.get("ok"), out)
        rep = out["reports"][0]
        self.assertEqual(rep["mission_id"], "m-side")
        self.assertGreaterEqual(rep["score"], 0.80)
        self.assertIn("RigorScore", rep["text"])
        journaled = [e for e in loop.state.events
                     if e["type"] == "rigor_report"]
        self.assertEqual(len(journaled), 1)
        self.assertEqual(journaled[0]["data"]["mission_id"], "m-side")

    def test_no_loop_refused(self):
        import awino_sidecar
        sc = awino_sidecar.Sidecar()
        out = sc._cmd_rigor_report({})
        self.assertEqual(out["code"], "no-loop")


class TestRigorCLI(unittest.TestCase):
    def test_mission_and_recent(self):
        import cli
        home = tempfile.mkdtemp(prefix="rigor-cli-")
        _journal(home, "p1", _perfect("m1") + _perfect("m2"))
        old = os.environ.get("AWINO_HOME")
        os.environ["AWINO_HOME"] = home
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_rigor(["--mission", "m1"])
            self.assertEqual(rc, 0)
            self.assertIn("m1", buf.getvalue())
            self.assertIn("RigorScore", buf.getvalue())
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_rigor(["--recent", "2"])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("m1", out)
            self.assertIn("m2", out)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_rigor(["--mission", "m1", "--json"])
            self.assertEqual(rc, 0)
            parsed = json.loads(buf.getvalue())
            self.assertEqual(parsed["mission_id"], "m1")
            self.assertIn("score", parsed)
            # Unknown mission: clean failure, no traceback.
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_rigor(["--mission", "nope"])
            self.assertEqual(rc, 1)
        finally:
            if old is None:
                del os.environ["AWINO_HOME"]
            else:
                os.environ["AWINO_HOME"] = old


class TestRigorJudge(unittest.TestCase):
    def test_rigor_spec_builds(self):
        from judges import build_judge_panel, DeterministicJudge
        panel = build_judge_panel("rigor")
        self.assertEqual(len(panel.judges), 1)
        j = panel.judges[0]
        self.assertIsInstance(j, DeterministicJudge)
        self.assertEqual(len(j.extra_rules), 2)

    def test_r3_done_claim_without_verification_evidence(self):
        from judges import build_judge_panel
        j = build_judge_panel("rigor").judges[0]
        v = j.judge({"done_claim": True, "progress_delta": "done",
                     "tool_calls": []},
                    "## MISSION\n### DONE CRITERIA\n[x] a\n",
                    {"results_this_session": 0, "verify_events": 0,
                     "test_runs": 0})
        self.assertEqual(v["verdict"], "FAIL")
        self.assertIn("R3", v["reason"])

    def test_r3_passes_with_verification_evidence(self):
        from judges import build_judge_panel
        j = build_judge_panel("rigor").judges[0]
        v = j.judge({"done_claim": True, "progress_delta": "done",
                     "tool_calls": []},
                    "## MISSION\n### DONE CRITERIA\n[x] a\n",
                    {"results_this_session": 1, "verify_events": 2,
                     "test_runs": 1})
        self.assertEqual(v["verdict"], "PASS")

    def test_r4_verified_claim_without_evidence(self):
        from judges import build_judge_panel
        j = build_judge_panel("rigor").judges[0]
        v = j.judge({"done_claim": False,
                     "progress_delta": "All tests pass now",
                     "tool_calls": []},
                    "## MISSION\n### DONE CRITERIA\n[ ] a\n",
                    {"results_this_session": 0, "verify_events": 0,
                     "test_runs": 0})
        self.assertEqual(v["verdict"], "FAIL")
        self.assertIn("R4", v["reason"])

    def test_pentagonal_axes_covered(self):
        # The pentagonal audit is the VERIFY phase's rigor skill: all five
        # axes must be present in the pinned skill, and the floor must route it.
        body = SkillStore.default().get_verified("rigor-pentagonal-audit")
        for axis in ("correctness", "readability", "architecture",
                     "security", "performance"):
            self.assertIn(axis, body.lower())
        from stances import FLOORS
        self.assertIn("rigor-pentagonal-audit", FLOORS["VERIFY"]["skills"])
        # Zero unresolved CRITICAL findings is the PASS bar.
        self.assertIn("CRITICAL", body)

    def test_floor_routing_layered(self):
        # No rigor skill is dumped into every turn: each phase routes only
        # its own, and three-strike is never floor-routed.
        from stances import FLOORS
        for phase, floor in FLOORS.items():
            for s in floor["skills"]:
                self.assertTrue(s in SkillStore.default().names(), s)
        all_routed = {s for f in FLOORS.values() for s in f["skills"]}
        rigor_routed = {s for s in all_routed if s.startswith("rigor-")}
        self.assertNotIn("rigor-three-strike", rigor_routed)
        self.assertIn("rigor-laws", FLOORS["DEFINE"]["skills"])


if __name__ == "__main__":
    unittest.main()
