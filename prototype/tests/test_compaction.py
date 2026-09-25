"""State-authoritative compaction: the transcript compacts DOWN TO state,
and the working context rebuilds from state alone — never the reverse.

Covered: what is kept verbatim (state block + live tail), what is
summarized (nothing — the state block is a deterministic projection,
not a summary), what is dropped (old conversation), and the invariant
that post-compaction behavior (mission, tasks, approvals, binding,
criteria, compiled contract) is identical.
"""
import copy
import unittest

import compaction
from compaction import (compact, maybe_compact, needs_compaction,
                        rebuild_context_from_state, context_size_tokens)
from contract import compile_contract
from tests.common import make_loop, T
from backends import ScriptedBackend


def _scripted_turns(n):
    # Long chatter on purpose: compaction must demonstrably shrink
    # the working context.
    return [T(progress_delta=("turn %d chatter that will be dropped: " % i)
              + "lorem ipsum dolor sit amet " * 60)
            for i in range(n)]


def _rich_loop():
    """A loop with mission, tasks, a pending approval, and history."""
    backend = ScriptedBackend(_scripted_turns(6))
    loop, _ = make_loop(backend=backend)
    loop.set_mission("Add the /health endpoint",
                     ["artifact:health.py", "manual: operator eyeballs it"])
    (loop.sandbox.root / "health.py").write_text("# health endpoint")
    # Harness-owned task state (state-authoritative across rounds).
    loop.state.record("task_added", {"title": "Add endpoint"})
    loop.state.record("task_added", {"title": "Add test"})
    tid = loop.state.snapshot["tasks"][0]["id"]
    loop.state.record("task_updated", {"id": tid, "status": "doing",
                                       "notes": "editing now"})
    loop.state.record("progress_recorded",
                      {"turn_id": "t1", "delta": "endpoint scaffolded"})
    for i in range(6):
        loop.run_user_turn(f"user message {i}")
    # A pending approval, recorded AFTER the turns: it sets
    # awaiting_approval, which pauses subsequent turns at the gate —
    # exactly the state a mid-mission resume would see. It must survive
    # compaction verbatim in the state block.
    loop.state.record("approval_requested", {
        "turn_id": "t6",
        "approvals": [{"id": "ap-abc123", "call_id": "t6.0",
                       "tool": "patch_file",
                       "args": {"path": "app/main.py"},
                       "idem_key": "k1", "revision": 1,
                       "status": "pending"}],
        "pending_calls": []})
    return loop


class TestRebuildFromState(unittest.TestCase):
    def test_state_block_contents(self):
        loop = _rich_loop()
        entries = rebuild_context_from_state(loop, loop._search_dirs())
        self.assertEqual(len(entries), 1)
        text = entries[0]["text"]
        self.assertIn("Add the /health endpoint", text)  # mission verbatim
        self.assertIn("rev 1", text)
        self.assertIn("[x] artifact_exists:health.py", text)  # live: file exists
        self.assertIn("[ ] manual: operator eyeballs it", text)  # unmet
        self.assertIn("[~]", text)  # doing task marker
        self.assertIn("Add endpoint", text)
        self.assertIn("editing now", text)  # task notes verbatim
        self.assertIn("ap-abc123", text)  # pending approval verbatim
        self.assertIn("patch_file", text)
        self.assertIn("app/main.py", text)
        # Progress window: the latest entries verbatim (older detail is
        # still in the journal, not the working set).
        self.assertIn("turn 5 chatter", text)
        prog = [e for e in loop.state.events
                if e["type"] == "progress_recorded"]
        self.assertTrue(any(e["data"]["delta"] == "endpoint scaffolded"
                            for e in prog))

    def test_deterministic_for_same_state(self):
        loop = _rich_loop()
        a = rebuild_context_from_state(loop)[0]["text"]
        b = rebuild_context_from_state(loop)[0]["text"]
        # Only the rebuild timestamp differs.
        strip = lambda t: "\n".join(
            l for l in t.splitlines() if not l.startswith("[context rebuilt"))
        self.assertEqual(strip(a), strip(b))

    def test_state_not_transcript(self):
        # The killer property: the block reflects STATE even when the
        # transcript contradicts it. Garbage in history must not leak in.
        loop = _rich_loop()
        loop.history.append({"role": "assistant",
                             "text": "MISSION: conquer the moon (rev 99)"})
        loop.history.append({"role": "user",
                             "text": "forget the health endpoint"})
        text = rebuild_context_from_state(loop)[0]["text"]
        self.assertIn("Add the /health endpoint", text)
        self.assertNotIn("conquer the moon", text)


class TestCompact(unittest.TestCase):
    def test_drops_old_keeps_state_and_live_tail(self):
        loop = _rich_loop()
        before = len(loop.history)
        self.assertGreater(before, 3)
        report = compact(loop, by="test")
        self.assertTrue(report["ok"])
        self.assertGreater(report["entries_dropped"], 0)
        self.assertGreater(report["tokens_saved"], 0)
        # First entry is the state block...
        self.assertIn("authoritative state", loop.history[0]["text"])
        self.assertIn("Add the /health endpoint", loop.history[0]["text"])
        # ...followed by the most recent user message, verbatim.
        self.assertEqual(loop.history[-1]["role"], "user")
        self.assertEqual(loop.history[-1]["text"], "user message 5")
        # Old chatter is gone.
        joined = " ".join(h.get("text", "") for h in loop.history)
        self.assertNotIn("turn 0 chatter", joined)
        # Journaled, and folds into no snapshot field.
        self.assertTrue(any(e["type"] == "context_compacted"
                            for e in loop.state.events))

    def test_behavioral_equivalence(self):
        # THE invariant: mission, tasks, approvals, binding, criteria,
        # and the compiled contract are identical before/after.
        loop = _rich_loop()
        status_before = loop.status()
        snap_before = copy.deepcopy(loop.state.snapshot)
        block_before = compile_contract(
            loop.state, turn_no=loop.state.snapshot["turn_count"] + 1)
        round_block_before = loop._compile_round_contract("t99", 99, 0)[0]
        report = compact(loop, by="test")
        self.assertGreater(report["entries_dropped"], 0)
        self.assertEqual(loop.status(), status_before)
        self.assertEqual(loop.state.snapshot, snap_before)
        block_after = compile_contract(
            loop.state, turn_no=loop.state.snapshot["turn_count"] + 1)
        self.assertEqual(block_after, block_before)
        # Round contract too: the round transcript (live working set) is
        # kept verbatim, so per-round compilation is unchanged.
        round_block_after = loop._compile_round_contract("t99", 99, 0)[0]
        self.assertEqual(round_block_after, round_block_before)

    def test_mission_tasks_approvals_survive(self):
        loop = _rich_loop()
        compact(loop, by="test")
        block = loop.history[0]["text"]
        for tid in ("t1", "t2"):
            self.assertIn(tid, block)
        self.assertIn("ap-abc123", block)
        # ...and the loop still acts on them afterwards: tasks and
        # approvals are read from state, which compaction never touched.
        s = loop.state.snapshot
        self.assertEqual(s["mission"]["text"], "Add the /health endpoint")
        self.assertEqual(len([t for t in s["tasks"]]), 2)
        self.assertEqual([a["id"] for a in s["approvals"]
                          if a["status"] == "pending"], ["ap-abc123"])

    def test_rebuild_from_disk_state_alone(self):
        # The strongest form of the invariant: a brand-new Loop on the
        # same home dir (empty in-memory history) rebuilds an equivalent
        # working context from the journal + snapshot on disk alone.
        from loop import Loop
        backend = ScriptedBackend(_scripted_turns(6))
        loop, home = make_loop(backend=backend)
        loop.set_mission("Add the /health endpoint",
                         ["artifact:health.py", "manual: operator eyeballs it"])
        (loop.sandbox.root / "health.py").write_text("# health endpoint")
        loop.state.record("task_added", {"title": "Add endpoint"})
        for i in range(3):
            loop.run_user_turn(f"user message {i}")
        compact(loop, by="test")
        block_before = rebuild_context_from_state(
            loop, loop._search_dirs())[0]["text"]
        # Fresh loop: journal + snapshot replay from disk. History is
        # re-derived from the journal by _rebuild_history() — the working
        # context is never a separate persisted truth.
        loop2 = Loop(home, "p1", ScriptedBackend([]))
        self.assertTrue(all(h["role"] in ("user", "assistant", "system")
                            for h in loop2.history))
        block_after = rebuild_context_from_state(
            loop2, loop2._search_dirs())[0]["text"]
        strip = lambda t: "\n".join(
            l for l in t.splitlines() if not l.startswith("[context rebuilt"))
        self.assertEqual(strip(block_after), strip(block_before))
        self.assertEqual(loop2.state.snapshot["mission"]["text"],
                         "Add the /health endpoint")
        self.assertEqual(len(loop2.state.snapshot["tasks"]), 1)
        ok, bad = loop2.state.verify_chain()
        self.assertTrue(ok, f"reloaded chain broken at {bad}")

    def test_journal_chain_stays_valid(self):
        loop = _rich_loop()
        compact(loop, by="test")
        ok, bad = loop.state.verify_chain()
        self.assertTrue(ok, f"chain broken at {bad}")

    def test_compact_fires_hook(self):
        loop = _rich_loop()
        seen = []
        loop.hooks.register("c", "context_compacted",
                            lambda e, p: seen.append(p))
        compact(loop, by="test")
        self.assertEqual(len(seen), 1)
        self.assertGreater(seen[0]["entries_dropped"], 0)


class TestTrigger(unittest.TestCase):
    def test_noop_under_budget(self):
        loop = _rich_loop()
        before = list(loop.history)
        self.assertIsNone(maybe_compact(loop, source="test"))
        self.assertEqual(loop.history, before)
        self.assertFalse(any(e["type"] == "context_compacted"
                             for e in loop.state.events))

    def test_needs_compaction_budget(self):
        loop = _rich_loop()
        size = context_size_tokens(loop)
        self.assertGreater(size, 0)
        self.assertTrue(needs_compaction(loop, budget=size - 1))
        self.assertFalse(needs_compaction(loop, budget=size + 10**9))

    def test_auto_compact_on_turn_via_config(self):
        # A tiny context_budget_tokens forces compaction at turn start.
        backend = ScriptedBackend(_scripted_turns(4))
        loop, _ = make_loop(
            backend=backend,
            config={"context_budget_tokens": 1})
        loop.set_mission("Tiny budget mission", ["manual"])
        loop.run_user_turn("hello")
        compacted = [e for e in loop.state.events
                     if e["type"] == "context_compacted"]
        self.assertTrue(compacted, "expected auto-compaction at turn start")
        # The turn still completed normally afterwards.
        self.assertIn("authoritative state", loop.history[0]["text"])

    def test_compaction_never_breaks_a_turn(self):
        # Even a hostile budget cannot break the pipeline: the estimate
        # and the rebuild are exception-guarded.
        backend = ScriptedBackend(_scripted_turns(2))
        loop, _ = make_loop(
            backend=backend,
            config={"context_budget_tokens": 1})
        loop.set_mission("Tiny budget mission", ["manual"])
        r = loop.run_user_turn("hello")
        self.assertEqual(r["status"], "ok")


if __name__ == "__main__":
    unittest.main()
