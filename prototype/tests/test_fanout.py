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
from backends import ScriptedBackend, ModelBackend
from loop import FanoutFailed, UnknownBackendError, _owned_overlap
from contract import MODES, HARNESS_TOOLS


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
        # v0.6: the authoritative offered set adds the harness tools to
        # every mode's base list.
        self.assertEqual(seen["gate"],
                         MODES["build"]["tools"] + list(HARNESS_TOOLS))
        self.assertEqual(seen["parent_tools"],
                         MODES["build"]["tools"] + list(HARNESS_TOOLS))

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
        # v0.6: the authoritative offered set adds the harness tools to
        # every mode's base list; the gate stays clamped to observe.
        # 0.5.3 hotfix port: set_mission is interview-only and explicitly
        # excluded from the worker's parent policy — a worker's mission is
        # fixed by its parent, so it must never reach a worker even from
        # an observe-mode parent.
        # story_plan is excluded the same way: the parent owns the story.
        expected = ([t for t in MODES["observe"]["tools"]
                     if t not in ("set_mission", "story_plan")]
                    + list(HARNESS_TOOLS))
        self.assertEqual(seen["gate"], expected)
        self.assertNotIn("set_mission", seen["gate"])
        self.assertNotIn("story_plan", seen["gate"])

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


class _NamedBackend(ModelBackend):
    """Recording backend for routing tests: reports its own name and
    records every generate() call so tests can prove which backend
    served each worker."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def generate(self, contract_block, history, feedback=None,
                 temperature=None):
        self.calls.append({"contract_block": contract_block})
        return {"backend": self.name}


def _routed_runner(wloop, subtask):
    res = wloop.backend.generate("contract", [], None)
    return {"status": "ok", "result": {"served_by": res["backend"]}}


class TestFanoutModelRouting(unittest.TestCase):
    def test_workers_routed_to_different_backends(self):
        # Each worker is served by the backend its subtask named in the
        # code-owned routing map.
        loop, _ = make_loop(config={"max_turns": 10})
        fast = _NamedBackend("fast")
        strong = _NamedBackend("strong")
        subtasks = _subtasks(("draft", "a/"), ("verify", "b/"))
        subtasks[0]["backend"] = "fast"
        subtasks[1]["backend"] = "strong"
        result = loop.fanout(
            "draft then verify",
            subtasks,
            run_worker=_routed_runner,
            model_routes={"fast": fast, "strong": strong})
        self.assertEqual(result["status"], "ok")
        served = {w["result"]["result"]["served_by"]
                  for w in result["workers"]}
        self.assertEqual(served, {"fast", "strong"})
        by_subtask = {}
        for st, w in zip(subtasks, result["workers"]):
            by_subtask[st["owned_files"][0]] = w["result"]["result"][
                "served_by"]
        self.assertEqual(by_subtask, {"a/": "fast", "b/": "strong"})
        types = _journal_types(loop)
        self.assertIn("fanout_started", types)
        self.assertIn("fanout_completed", types)
        self.assertNotIn("fanout_failed", types)

    def test_default_runner_uses_routed_backend(self):
        # Integration: the real default worker runner drives its turn
        # through the routed backend, not the parent's.
        loop, _ = make_loop(config={"max_turns": 10},
                            backend=_NamedBackend("parent"))
        fast = ScriptedBackend([T(done_claim=True,
                                  progress_delta="drafted")])
        subtasks = _subtasks(("draft", "a/"),)
        subtasks[0]["backend"] = "fast"
        result = loop.fanout("obj", subtasks,
                             model_routes={"fast": fast})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(fast.calls, "routed backend was never called")

    def test_unrouted_subtask_keeps_parent_backend(self):
        # A subtask that names no backend runs on the parent's backend.
        loop, _ = make_loop(config={"max_turns": 10},
                            backend=_NamedBackend("parent"))
        seen = {}

        def capture(wloop, subtask):
            seen["name"] = getattr(wloop.backend, "name", None)
            return {"status": "ok", "result": {}}

        loop.fanout("obj", _subtasks(("t1", "a/"),),
                    run_worker=capture,
                    model_routes={"fast": _NamedBackend("fast")})
        self.assertEqual(seen["name"], "parent")

    def test_unknown_backend_name_fails_closed_before_spawn(self):
        loop, _ = make_loop(config={"max_turns": 10})
        subtasks = _subtasks(("draft", "a/"), ("verify", "b/"))
        subtasks[1]["backend"] = "strong"  # not in the routing map
        before = loop.state.snapshot.get("worker_budget_allocated", 0)
        with self.assertRaises(UnknownBackendError) as ctx:
            loop.fanout("obj", subtasks,
                        run_worker=_routed_runner,
                        model_routes={"fast": _NamedBackend("fast")})
        self.assertIn("strong", str(ctx.exception))
        self.assertIn("fanout refused", str(ctx.exception))
        # named error, and it is a ValueError like other fan-out refusals
        self.assertIsInstance(ctx.exception, ValueError)
        types = _journal_types(loop)
        self.assertIn("fanout_refused", types)
        self.assertNotIn("fanout_started", types)
        self.assertNotIn("worker_spawned", types)
        # budget pool untouched, no partial fan-out
        self.assertEqual(loop.state.snapshot.get("worker_budget_allocated", 0),
                         before)

    def test_unknown_backend_without_any_routing_map(self):
        # Naming a backend with no model_routes at all is the same
        # fail-closed refusal.
        loop, _ = make_loop(config={"max_turns": 10})
        subtasks = _subtasks(("t1", "a/"),)
        subtasks[0]["backend"] = "fast"
        with self.assertRaises(UnknownBackendError):
            loop.fanout("obj", subtasks, run_worker=_routed_runner)
        self.assertNotIn("worker_spawned", _journal_types(loop))

    def test_routing_does_not_weaken_barrier(self):
        # A routed worker that fails still fails the whole fan-out.
        loop, _ = make_loop(config={"max_turns": 10})
        fast = _NamedBackend("fast")

        def flaky(wloop, subtask):
            if subtask["owned_files"] == ["b/"]:
                raise RuntimeError("boom in routed worker")
            return _routed_runner(wloop, subtask)

        subtasks = _subtasks(("t1", "a/"), ("t2", "b/"))
        for st in subtasks:
            st["backend"] = "fast"
        with self.assertRaises(FanoutFailed) as ctx:
            loop.fanout("obj", subtasks, run_worker=flaky,
                        model_routes={"fast": fast})
        bad = [w for w in ctx.exception.details["workers"]
               if w["status"] == "failed"]
        self.assertEqual(len(bad), 1)
        self.assertIn("boom in routed worker", bad[0]["error"])
        self.assertIn("fanout_failed", _journal_types(loop))


if __name__ == "__main__":
    unittest.main()
