"""Native tool application: delegated writes batch into one extension
round-trip, run_command streams through the extension terminal, git
checkpoints guard build-mode writes, and the extension-RPC wait loop
defers (never loses) unrelated commands.

These tests use a fake delegation / fake extension: they prove the loop
and sidecar sides of the protocol. The extension side (WorkspaceEdit,
vscode.diff, terminal shell integration) is covered by the node protocol
test and the Windows GUI assertions.
"""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import awino_sidecar as sc_mod
from awino_sidecar import Sidecar, ExtensionDelegation
from loop import Loop
from tests.common import make_loop


PATCH_B = """--- a/b.txt
+++ b/b.txt
@@ -1,2 +1,2 @@
-line1
+line1-changed
 line2
"""


class FakeDelegation:
    """Stands in for the VS Code extension: honest by default."""

    def __init__(self, loop=None):
        self.loop = loop
        self.batches = []
        self.forced_results = None
        self.terminal_calls = []

    def tool_fn(self, name):
        if name == "run_command":
            return lambda cmd="", **kw: {"cmd": cmd, "exit_code": 0,
                                         "stdout": "fake-out", "stderr": ""}
        if name in ("write_file", "patch_file"):
            return None  # the drain batches writes; single is fail-safe
        return None

    def apply_batch(self, edits):
        self.batches.append(list(edits))
        if self.forced_results is not None:
            return self.forced_results
        return [{"call_id": e["call_id"], "ok": True,
                 "result": {"path": e["path"], "digest": e["digest"],
                            "bytes": len(e["content"])}}
                for e in edits]


def grant(loop, *pcs):
    """Install pending calls + granted approvals directly in the snapshot."""
    snap = loop.state.snapshot
    snap["pending_calls"] = list(pcs)
    snap["approvals"] = [
        {"id": pc["approval_id"], "status": "granted"} for pc in pcs]
    loop.state.persist_snapshot()


def pc(call_id, tool, args, approval_id=None, idem_key=None):
    return {"call_id": call_id, "tool": tool, "args": args,
            "idem_key": idem_key or f"idem-{call_id}",
            "approval_id": approval_id or f"ap-{call_id}"}


class TestComputeDelegatedEdit(unittest.TestCase):
    def test_write_new_file(self):
        loop, _ = make_loop()
        edit = loop._compute_delegated_edit(
            "write_file", {"path": "new.txt", "content": "hello\n"})
        self.assertEqual(edit["content"], "hello\n")
        self.assertIsNone(edit["old_digest"])
        self.assertEqual(edit["digest"],
                         hashlib.sha256(b"hello\n").hexdigest())
        # Nothing was written.
        self.assertFalse((loop.sandbox.root / "new.txt").exists())

    def test_write_existing_file_carries_old_digest(self):
        loop, _ = make_loop()
        (loop.sandbox.root / "e.txt").write_text("old\n")
        edit = loop._compute_delegated_edit(
            "write_file", {"path": "e.txt", "content": "new\n"})
        self.assertEqual(edit["old_digest"],
                         hashlib.sha256(b"old\n").hexdigest())
        self.assertEqual((loop.sandbox.root / "e.txt").read_text(), "old\n")

    def test_patch_computes_content_without_writing(self):
        loop, _ = make_loop()
        (loop.sandbox.root / "b.txt").write_text("line1\nline2\n")
        edit = loop._compute_delegated_edit(
            "patch_file", {"path": "b.txt", "diff": PATCH_B})
        self.assertEqual(edit["content"], "line1-changed\nline2\n")
        self.assertEqual(edit["old_digest"],
                         hashlib.sha256(b"line1\nline2\n").hexdigest())
        # Disk untouched.
        self.assertEqual((loop.sandbox.root / "b.txt").read_text(),
                         "line1\nline2\n")

    def test_patch_refusal_is_error_data(self):
        loop, _ = make_loop()
        (loop.sandbox.root / "b.txt").write_text("unrelated\n")
        edit = loop._compute_delegated_edit(
            "patch_file", {"path": "b.txt", "diff": PATCH_B})
        self.assertIn("error", edit)
        self.assertEqual(edit["error_code"], "CONTEXT_MISMATCH")

    def test_path_escape_refuses(self):
        loop, _ = make_loop()
        edit = loop._compute_delegated_edit(
            "write_file", {"path": "../evil.txt", "content": "x"})
        self.assertIn("error", edit)
        self.assertEqual(edit["error_code"], "delegated_path_escape")


class TestDelegatedDrain(unittest.TestCase):
    def _loop_with_delegation(self):
        loop, _ = make_loop()
        loop.set_mission("Write things", ["manual"])
        (loop.sandbox.root / "b.txt").write_text("line1\nline2\n")
        delegation = FakeDelegation(loop)
        loop.delegation = delegation
        return loop, delegation

    def test_batch_single_round_trip_and_order(self):
        loop, delegation = self._loop_with_delegation()
        grant(loop,
              pc("t1.0", "write_file",
                 {"path": "a.txt", "content": "aaa\n"}),
              pc("t1.1", "patch_file",
                 {"path": "b.txt", "diff": PATCH_B}),
              pc("t1.2", "write_file",
                 {"path": "c.txt", "content": "ccc\n"}))
        r = loop._drain_pending()
        self.assertEqual(r["status"], "ok")
        # ONE batch for all three writes (one WorkspaceEdit / one undo).
        self.assertEqual(len(delegation.batches), 1)
        self.assertEqual(len(delegation.batches[0]), 3)
        # Results come back in pending_calls order.
        self.assertEqual([x["tool"] for x in r["results"]],
                         ["write_file", "patch_file", "write_file"])
        for x in r["results"]:
            self.assertNotIn("error", x["result"], x)
        # Journaling is identical to the inline path.
        kinds = [e["type"] for e in loop.state.events]
        self.assertEqual(kinds.count("tool_called"), 3)
        self.assertEqual(kinds.count("tool_result"), 3)
        self.assertEqual(kinds.count("patch_applied"), 1)
        # The sandbox wrote NOTHING — the extension owns the bytes.
        self.assertFalse((loop.sandbox.root / "a.txt").exists())
        self.assertEqual((loop.sandbox.root / "b.txt").read_text(),
                         "line1\nline2\n")
        # ...but the tamper manifest knows about the delegated writes.
        manifest = loop.sandbox.manifest()
        self.assertEqual(manifest["a.txt"],
                         hashlib.sha256(b"aaa\n").hexdigest())

    def test_non_write_tools_still_execute_inline(self):
        loop, delegation = self._loop_with_delegation()
        grant(loop,
              pc("t1.0", "write_file",
                 {"path": "a.txt", "content": "aaa\n"}),
              pc("t1.1", "read_file", {"path": "b.txt"}))
        r = loop._drain_pending()
        self.assertEqual(len(delegation.batches), 1)
        self.assertEqual(len(delegation.batches[0]), 1)  # only the write
        read_result = r["results"][1]["result"]
        self.assertEqual(read_result["content"], "line1\nline2\n")

    def test_delegation_failure_is_error_data(self):
        loop, delegation = self._loop_with_delegation()
        delegation.forced_results = [
            {"call_id": "t1.0", "ok": False, "error": "applyEdit failed"}]
        grant(loop, pc("t1.0", "write_file",
                       {"path": "a.txt", "content": "aaa\n"}))
        r = loop._drain_pending()
        res = r["results"][0]["result"]
        self.assertIn("error", res)
        self.assertEqual(res["error_code"], "delegated_apply_failed")

    def test_digest_mismatch_is_error_data(self):
        loop, delegation = self._loop_with_delegation()
        delegation.forced_results = [
            {"call_id": "t1.0", "ok": True,
             "result": {"path": "a.txt",
                        "digest": "0" * 64,  # not what the loop computed
                        "bytes": 4}}]
        grant(loop, pc("t1.0", "write_file",
                       {"path": "a.txt", "content": "aaa\n"}))
        r = loop._drain_pending()
        res = r["results"][0]["result"]
        self.assertEqual(res["error_code"], "delegated_digest_mismatch")

    def test_idempotency_reused_without_batch(self):
        loop, delegation = self._loop_with_delegation()
        grant(loop, pc("t1.0", "write_file",
                       {"path": "a.txt", "content": "aaa\n"}))
        loop._drain_pending()
        self.assertEqual(len(delegation.batches), 1)
        # Same idempotency key again: the cached result is reused, no new
        # extension round-trip.
        grant(loop, pc("t1.1", "write_file",
                       {"path": "a.txt", "content": "aaa\n"},
                       idem_key="idem-t1.0"))
        r = loop._drain_pending()
        self.assertEqual(len(delegation.batches), 1)
        self.assertTrue(r["results"][0].get("reused"))

    def test_no_delegation_means_sandbox_writes(self):
        loop, _ = make_loop()
        loop.set_mission("Write things", ["manual"])
        grant(loop, pc("t1.0", "write_file",
                       {"path": "a.txt", "content": "aaa\n"}))
        r = loop._drain_pending()
        self.assertNotIn("error", r["results"][0]["result"])
        self.assertEqual((loop.sandbox.root / "a.txt").read_text(), "aaa\n")

    def test_drain_defers_until_round_complete(self):
        """Approving one of two pending approvals must NOT execute yet —
        otherwise the change set would split across undo units. The drain
        (and the single delegated batch) happens on the final decision."""
        loop, delegation = self._loop_with_delegation()
        snap = loop.state.snapshot
        snap["pending_calls"] = [
            pc("t1.0", "write_file", {"path": "a.txt", "content": "aaa\n"},
               approval_id="ap-1"),
            pc("t1.1", "write_file", {"path": "c.txt", "content": "ccc\n"},
               approval_id="ap-2"),
        ]
        snap["approvals"] = [
            {"id": "ap-1", "status": "pending", "call_id": "t1.0",
             "revision": loop._revision(),
             "scope_epoch": snap.get("scope_epoch", 0)},
            {"id": "ap-2", "status": "pending", "call_id": "t1.1",
             "revision": loop._revision(),
             "scope_epoch": snap.get("scope_epoch", 0)},
        ]
        loop.state.persist_snapshot()
        r1 = loop.approve("ap-1")
        self.assertEqual(r1["status"], "awaiting_approval")
        self.assertEqual(delegation.batches, [],
                         "no delegated batch while an approval is pending")
        self.assertFalse((loop.sandbox.root / "a.txt").exists())
        r2 = loop.approve("ap-2")
        self.assertEqual(r2["status"], "ok")
        self.assertEqual(len(delegation.batches), 1,
                         "one batch for the whole round")
        self.assertEqual(len(delegation.batches[0]), 2)


class TestWaitForExtension(unittest.TestCase):
    def test_unrelated_commands_are_deferred_not_lost(self):
        sc = Sidecar()
        sc.inbox.put({"cmd": "command", "name": "status", "args": {}})
        sc.inbox.put({"cmd": "apply_result", "request_id": "ext-1",
                      "results": []})
        got = sc._wait_for_extension("apply_result", "ext-1", timeout=5)
        self.assertEqual(got["results"], [])
        # The status command was deferred for normal dispatch.
        self.assertEqual(len(sc.deferred), 1)
        self.assertEqual(sc.deferred[0]["name"], "status")
        self.assertEqual(sc._next_command()["name"], "status")

    def test_timeout_raises(self):
        sc = Sidecar()
        with self.assertRaises(TimeoutError):
            sc._wait_for_extension("apply_result", "ext-9", timeout=0.2)

    def test_eof_raises(self):
        sc = Sidecar()
        sc.inbox.put(None)
        with self.assertRaises(EOFError):
            sc._wait_for_extension("apply_result", "ext-1", timeout=5)


class TestExtensionDelegationTerminal(unittest.TestCase):
    def _sidecar(self):
        sc = Sidecar()
        loop, _ = make_loop()
        loop.set_mission("Run things", ["manual"])
        sc.loop = loop
        sc.workspace = loop.sandbox.root
        return sc

    def test_streaming_chunks_accumulate_into_result(self):
        sc = self._sidecar()
        emitted = []
        orig_emit = sc_mod._emit
        sc_mod._emit = emitted.append
        try:
            delegation = ExtensionDelegation(sc)
            sc.inbox.put({"cmd": "terminal_output", "request_id": "ext-1",
                          "data": "hello "})
            sc.inbox.put({"cmd": "terminal_output", "request_id": "ext-1",
                          "data": "world\n"})
            sc.inbox.put({"cmd": "terminal_result", "request_id": "ext-1",
                          "exit_code": 0, "timed_out": False,
                          "killed": False})
            result = delegation.run_terminal("echo hello world")
        finally:
            sc_mod._emit = orig_emit
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "hello world\n")
        self.assertFalse(result["timed_out"])
        # The extension got exactly one terminal_requested event.
        reqs = [e for e in emitted
                if isinstance(e, dict)
                and e.get("event") == "terminal_requested"]
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["request_id"], "ext-1")
        self.assertEqual(reqs[0]["cmd"], "echo hello world")

    def test_cancel_during_terminal_kills_and_defers(self):
        sc = self._sidecar()
        emitted = []
        orig_emit = sc_mod._emit
        sc_mod._emit = emitted.append
        try:
            delegation = ExtensionDelegation(sc)
            sc.inbox.put({"cmd": "cancel"})
            sc.inbox.put({"cmd": "terminal_result", "request_id": "ext-1",
                          "exit_code": None, "timed_out": False,
                          "killed": True})
            result = delegation.run_terminal("sleep 999")
        finally:
            sc_mod._emit = orig_emit
        self.assertTrue(result["killed"])
        kills = [e for e in emitted
                 if isinstance(e, dict) and e.get("event") == "terminal_kill"]
        self.assertEqual(len(kills), 1)
        self.assertEqual(kills[0]["request_id"], "ext-1")
        # The cancel was deferred for normal dispatch afterwards.
        self.assertEqual(len(sc.deferred), 1)
        self.assertEqual(sc.deferred[0]["cmd"], "cancel")


def _git_repo():
    d = tempfile.mkdtemp(prefix="awino-checkpoint-test-")
    subprocess.run(["git", "init", "-q", d], check=True, timeout=30)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"],
                   check=True, timeout=30)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"],
                   check=True, timeout=30)
    with open(os.path.join(d, "tracked.txt"), "w") as f:
        f.write("v1\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", d, "commit", "-qm", "init"], check=True,
                   timeout=30)
    return d


class TestCheckpoints(unittest.TestCase):
    def _sidecar_in_repo(self, d):
        sc = Sidecar()
        loop, _ = make_loop()
        loop.set_mission("Checkpoint things", ["manual"])
        sc.loop = loop
        from pathlib import Path
        sc.workspace = Path(d)
        return sc

    def test_checkpoint_and_revert(self):
        d = _git_repo()
        self.addCleanup(shutil.rmtree, d, True)
        sc = self._sidecar_in_repo(d)
        # Operator's own uncommitted change before the checkpoint.
        with open(os.path.join(d, "tracked.txt"), "w") as f:
            f.write("operator-edit\n")
        with open(os.path.join(d, "mine.txt"), "w") as f:
            f.write("operator-untracked\n")
        edits = [{"call_id": "c1", "tool": "write_file", "path": "gen.txt",
                  "content": "generated\n", "old_digest": None},
                 {"call_id": "c2", "tool": "write_file",
                  "path": "tracked.txt", "content": "awino-edit\n",
                  "old_digest": hashlib.sha256(b"operator-edit\n").hexdigest()}]
        sc._ensure_checkpoint("cs-1", edits)
        cp = sc._checkpoints["cs-1"]
        self.assertEqual(cp["type"], "stash")
        self.assertEqual(cp["new_files"], ["gen.txt"])
        self.assertTrue(any(e["type"] == "checkpoint_created"
                            for e in sc.loop.state.events))
        # Simulate the delegated writes landing on disk.
        with open(os.path.join(d, "gen.txt"), "w") as f:
            f.write("generated\n")
        with open(os.path.join(d, "tracked.txt"), "w") as f:
            f.write("awino-edit\n")
        # Revert: operator's state comes back, Awino's writes are gone.
        r = sc._do_revert_checkpoint({})
        self.assertTrue(r["ok"], r)
        self.assertFalse(os.path.exists(os.path.join(d, "gen.txt")))
        with open(os.path.join(d, "tracked.txt")) as f:
            self.assertEqual(f.read(), "operator-edit\n")
        with open(os.path.join(d, "mine.txt")) as f:
            self.assertEqual(f.read(), "operator-untracked\n")

    def test_revert_unknown_checkpoint_is_error(self):
        d = _git_repo()
        self.addCleanup(shutil.rmtree, d, True)
        sc = self._sidecar_in_repo(d)
        r = sc._do_revert_checkpoint({})
        self.assertEqual(r["status"], "error")

    def test_non_git_workspace_checkpoints_honestly(self):
        d = tempfile.mkdtemp(prefix="awino-nogit-test-")
        self.addCleanup(shutil.rmtree, d, True)
        sc = self._sidecar_in_repo(d)
        sc._ensure_checkpoint("cs-nogit", [])
        cp = sc._checkpoints["cs-nogit"]
        self.assertEqual(cp["type"], "none")
        self.assertIn("not a git", cp["note"])
        r = sc._do_revert_checkpoint({})
        self.assertEqual(r["status"], "error")


if __name__ == "__main__":
    unittest.main()
