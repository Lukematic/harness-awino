"""Track H: fan-out primitive — parallel workers with a synthesize barrier.

Tests:
- disjoint ownership: overlap raises before any spawn (no partial fan-out)
- budget atomicity: shortfall raises before any spawn; pool unchanged
- all-succeed: N workers run in threads, results synthesized, journal complete
- one-fails: fail-closed FanoutFailed with explicit per-worker status
- scope containment: ScopeViolation in a worker's journal fails the worker
  even when its runner claimed success (defense in depth)
- mode inheritance: workers start at the parent's mode and their offered
  tools are clamped to the parent's policy, even under routing escalation
"""
import unittest
from pathlib import Path

from tests.common import make_loop, T
from backends import ScriptedBackend
from loop import FanoutFailed, _owned_overlap
from contract import MODES


def _subtasks(*specs):
    return [{"objective": o, "owned_files": [f],
             "budget_share": {"max_turns": 2}} for o, f in specs]


def _ok_runner(wloop, subtask):
    owned = subtask["owned_files"][0].rstrip("/")
    res = wloop._execute_single(
        "t1.0", "write_file",
        {"path": f"{owned}/result.txt",
         "content": f"done: {subtask['objective']}"},
        f"idem-fanout-{owned}")
    assert "ScopeViolation" not in str(res.get("result", "")), res
    return {"status": "ok", "result": {"artifact": f"{owned}/result.txt"}}


def _journal_types(loop):
    return [e["type"] for e in loop.state.events]


class TestOwnedOverlap(unittest.TestCase):
    def test_overlap_detected(self):
        self.assertIsNotNone(_owned_overlap(["docs/"], ["docs/api/"]))
        self.assertIsNotNone(_owned_overlap(["a/b"], ["a/b"]))
        self.assertIsNotNone(_owned_overlap(["a/b/c.txt"], ["a/b/"]))

    def test_disjoint(self):
        self.assertIsNone(_owned_overlap(["docs/"], ["src/"]))
        self.assertIsNone(_owned_overlap(["docs/"], ["docs2/"]))
        self.assertIsNone(_owned_overlap(["a/"], ["b/"]))


class TestFanoutPreflight(unittest.TestCase):
    def test_overlap_raises_before_any_spawn(self):
        loop, home = make_loop(config={"max_turns": 10})
        with self.assertRaises(ValueError) as ctx:
            loop.fanout("obj", _subtasks(("t1", "docs/"), ("t2", "docs/api/")),
                        run_worker=_ok_runner)
        self.assertIn("disjoint", str(ctx.exception))
        types = _journal_types(loop)
        self.assertIn("fanout_refused", types)
        self.assertNotIn("worker_spawned", types)
        self.assertNotIn("fanout_started", types)

    def test_budget_shortfall_raises_before_any_spawn(self):
        loop, home = make_loop(config={"max_turns": 4})
        before = loop.state.snapshot.get("worker_budget_allocated", 0)
        with self.assertRaises(RuntimeError) as ctx:
            loop.fanout("obj", _subtasks(("t1", "a/"), ("t2", "b/"),
                                         ("t3", "c/")),
                        run_worker=_ok_runner)
        self.assertIn("budget", str(ctx.exception).lower())
        # pool untouched
        self.assertEqual(loop.state.snapshot.get("worker_budget_allocated", 0),
                         before)
        types = _journal_types(loop)
        self.assertIn("fanout_refused", types)
        self.assertNotIn("worker_spawned", types)
        # no worker dirs leaked
        self.assertFalse((Path(home) / "projects").exists()
                         and any((Path(home) / "projects").rglob("workers")))

    def test_empty_and_malformed(self):
        loop, _ = make_loop()
        with self.assertRaises(ValueError):
            loop.fanout("obj", [], run_worker=_ok_runner)
        with self.assertRaises(ValueError):
            loop.fanout("obj", [{"objective": "x"}], run_worker=_ok_runner)


class TestFanoutSuccess(unittest.TestCase):
    def test_all_succeed_synthesized(self):
        loop, home = make_loop(config={"max_turns": 10})
        result = loop.fanout(
            "write two files",
            _subtasks(("write A", "a/"), ("write B", "b/")),
            run_worker=_ok_runner)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["workers"]), 2)
        for w in result["workers"]:
            self.assertEqual(w["status"], "ok")
            self.assertIsNone(w["error"])
            self.assertEqual(w["result"]["status"], "ok")
            self.assertIn("artifact", w["result"]["result"])
        # merged maps worker_id -> result
        self.assertEqual(set(result["merged"].keys()),
                         {w["worker_id"] for w in result["workers"]})
        # journal: barrier events present
        types = _journal_types(loop)
        self.assertIn("fanout_started", types)
        self.assertIn("fanout_completed", types)
        self.assertNotIn("fanout_failed", types)
        self.assertEqual(types.count("fanout_worker_completed"), 2)
        # artifacts really landed in the workers' sandboxes
        for w in result["workers"]:
            wid = w["worker_id"]
            art = (Path(home) / "projects" / "p1" / "workers" / wid
                   / "sandbox" / w["result"]["result"]["artifact"])
            self.assertTrue(art.is_file(), f"missing {art}")

    def test_custom_synthesize(self):
        loop, _ = make_loop(config={"max_turns": 10})
        result = loop.fanout(
            "obj", _subtasks(("t1", "a/"), ("t2", "b/")),
            run_worker=_ok_runner,
            synthesize=lambda workers: {"count": len(workers)})
        self.assertEqual(result["merged"], {"count": 2})


class TestFanoutFailClosed(unittest.TestCase):
    def test_one_failure_fails_all_with_explicit_status(self):
        loop, _ = make_loop(config={"max_turns": 10})

        def flaky(wloop, subtask):
            if subtask["owned_files"] == ["b/"]:
                raise RuntimeError("boom in worker")
            return _ok_runner(wloop, subtask)

        with self.assertRaises(FanoutFailed) as ctx:
            loop.fanout("obj", _subtasks(("t1", "a/"), ("t2", "b/")),
                        run_worker=flaky)
        details = ctx.exception.details
        self.assertEqual(details["objective"], "obj")
        self.assertEqual(len(details["workers"]), 2)
        by_id = {w["worker_id"]: w for w in details["workers"]}
        ok_w = [w for w in details["workers"] if w["status"] == "ok"]
        bad_w = [w for w in details["workers"] if w["status"] == "failed"]
        self.assertEqual(len(ok_w), 1)
        self.assertEqual(len(bad_w), 1)
        # the successful worker's result is explicit, not silent
        self.assertEqual(ok_w[0]["result"]["status"], "ok")
        self.assertIn("artifact", ok_w[0]["result"]["result"])
        self.assertIn("boom in worker", bad_w[0]["error"])
        self.assertIn("fanout_failed", _journal_types(loop))

    def test_non_ok_runner_result_fails_worker(self):
        loop, _ = make_loop(config={"max_turns": 10})

        def paused(wloop, subtask):
            return {"status": "paused", "said": "waiting"}

        with self.assertRaises(FanoutFailed) as ctx:
            loop.fanout("obj", _subtasks(("t1", "a/"),),
                        run_worker=paused)
        self.assertIn("non-ok", ctx.exception.details["workers"][0]["error"])


class TestFanoutScopeContainment(unittest.TestCase):
    def test_scope_violation_in_journal_fails_worker_despite_ok_runner(self):
        # Defense in depth: even a lying runner cannot launder a scope
        # violation — the journal scan catches it.
        loop, home = make_loop(config={"max_turns": 10})

        def sneaky(wloop, subtask):
            res = wloop._execute_single(
                "t9.9", "write_file",
                {"path": "elsewhere/evil.txt", "content": "x"},
                "idem-sneaky")
            assert "ScopeViolation" in str(res.get("result", "")), res
            return {"status": "ok", "result": {"lied": True}}

        with self.assertRaises(FanoutFailed) as ctx:
            loop.fanout("obj", _subtasks(("t1", "a/"),),
                        run_worker=sneaky)
        w = ctx.exception.details["workers"][0]
        self.assertEqual(w["status"], "failed")
        self.assertIn("ScopeViolation", w["error"])
        # nothing was written outside the owned files: no worker sandbox
        # anywhere under home contains the evil path
        evil = list(Path(home).rglob("elsewhere/evil.txt"))
        self.assertEqual(evil, [])


class TestFanoutModeInheritance(unittest.TestCase):
    def test_worker_starts_at_parent_mode(self):
        loop, _ = make_loop(config={"max_turns": 10})
        loop.state.snapshot["mode"] = "build"
        seen = {}

        def capture(wloop, subtask):
            seen["mode"] = wloop.state.snapshot["mode"]
            seen["gate"] = wloop._permission_gate()
            seen["parent_tools"] = wloop.state.snapshot["parent_mode_tools"]
            return {"status": "ok", "result": {}}

        loop.fanout("obj", _subtasks(("t1", "a/"),), run_worker=capture)
        self.assertEqual(seen["mode"], "build")
        self.assertEqual(seen["gate"], MODES["build"]["tools"])
        self.assertEqual(seen["parent_tools"], MODES["build"]["tools"])

    def test_gate_clamped_despite_routing_escalation(self):
        # Parent is observe; even if the worker's own mode escalates to
        # build mid-turn, the gate stays clamped to the parent's policy.
        loop, _ = make_loop(config={"max_turns": 10})
        seen = {}

        def escalate(wloop, subtask):
            wloop.state.snapshot["mode"] = "build"  # simulate escalation
            seen["gate"] = wloop._permission_gate()
            return {"status": "ok", "result": {}}

        loop.fanout("obj", _subtasks(("t1", "a/"),), run_worker=escalate)
        self.assertEqual(seen["gate"], MODES["observe"]["tools"])

    def test_forbidden_tool_refused_end_to_end(self):
        # Parent in observe (no write_file). A persistently hostile worker
        # backend keeps attempting the write; the contract refuses every
        # attempt, the turn escalates, the worker fails, the fan-out fails.
        # The forbidden tool never executes.
        loop, home = make_loop(
            config={"max_turns": 10},
            backend=ScriptedBackend([]))
        hostile_turn = T(tool_calls=[{"name": "write_file",
                                      "args": {"path": "a/hack.txt",
                                               "content": "x"}}],
                         progress_delta="Trying to write.")

        def factory(subtask, idx):
            # more hostile turns than max_retries (3): every attempt refused
            return ScriptedBackend([hostile_turn] * 6)

        wid_holder = {}
        import loop as loop_mod
        real_default = loop_mod._default_fanout_runner

        def capturing_runner(wloop, subtask):
            wid_holder["loop"] = wloop
            return real_default(wloop, subtask)

        with self.assertRaises(FanoutFailed) as ctx:
            loop.fanout("obj", _subtasks(("t1", "a/"),),
                        run_worker=capturing_runner,
                        backend_factory=factory)
        wloop = wid_holder["loop"]
        ev_types = [e["type"] for e in wloop.state.events]
        # the contract refused the unoffered tool...
        rejected = [e for e in wloop.state.events
                    if e["type"] == "turn_rejected"]
        self.assertTrue(rejected)
        self.assertIn("not offered", str(rejected[0]["data"]))
        # ...and it never executed
        executed = [e for e in wloop.state.events
                    if e["type"] == "tool_called"
                    and e["data"].get("tool") == "write_file"]
        self.assertEqual(executed, [])
        self.assertFalse(list(Path(home).rglob("a/hack.txt")))
        # fanout payload names the failure explicitly
        self.assertEqual(ctx.exception.details["workers"][0]["status"],
                         "failed")


if __name__ == "__main__":
    unittest.main()
