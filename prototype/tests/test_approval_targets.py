"""Approval-target visibility: shell commands show their cwd-resolved file
targets on the approval card; out-of-workspace addressing is flagged.

Deliberate non-goal: this is VISIBILITY, not prohibition. There is no
blacklist and nothing is blocked — the human still decides. Approve/deny
semantics must not change.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from approval_targets import resolve_shell_targets  # noqa: E402
from tests.common import make_loop  # noqa: E402


def W(*parts):
    """Fake workspace root under /tmp (never touches the real FS)."""
    return os.path.join(tempfile.gettempdir(), "awino-target-test-ws", *parts)


def R(cmd, root=None):
    root = root or W()
    return resolve_shell_targets(cmd, root)


def resolved_paths(info):
    return [t["path"] for t in info["targets"]]


class TestResolver(unittest.TestCase):
    def test_relative_path_resolves_under_workspace(self):
        info = R("rm old.txt")
        self.assertEqual(resolved_paths(info), [W("old.txt")])
        self.assertTrue(all(t["in_workspace"] for t in info["targets"]))
        self.assertFalse(info["outside_workspace"])
        self.assertEqual(info["unresolved"], [])

    def test_cd_chain_adjusts_cwd(self):
        info = R("cd subdir && rm file.txt")
        self.assertEqual(resolved_paths(info), [W("subdir", "file.txt")])
        self.assertEqual(info["effective_cwd"], W("subdir"))
        self.assertFalse(info["outside_workspace"])

    def test_absolute_in_workspace_path_unchanged(self):
        info = R("rm " + W("data", "x.txt"))
        self.assertEqual(resolved_paths(info), [W("data", "x.txt")])
        self.assertFalse(info["outside_workspace"])

    def test_absolute_outside_workspace_flagged(self):
        info = R("rm /etc/hosts")
        self.assertEqual(resolved_paths(info), ["/etc/hosts"])
        self.assertFalse(info["targets"][0]["in_workspace"])
        self.assertTrue(info["outside_workspace"])

    def test_parent_escape_flagged(self):
        info = R("cat ../../etc/passwd", root="/w")
        self.assertEqual(resolved_paths(info), ["/etc/passwd"])
        self.assertTrue(info["outside_workspace"])

    def test_redirect_target_resolved(self):
        info = R("echo hello > logs/out.txt")
        self.assertEqual(resolved_paths(info), [W("logs", "out.txt")])
        self.assertEqual(info["targets"][0]["kind"], "redirect")
        info = R("make >> build.log 2> err.log")
        self.assertEqual(sorted(resolved_paths(info)),
                         [W("build.log"), W("err.log")])

    def test_input_redirect_resolved(self):
        info = R("cat < in.txt")
        self.assertEqual(resolved_paths(info), [W("in.txt")])
        self.assertFalse(info["outside_workspace"])

    def test_mv_src_and_dst_resolved(self):
        info = R("mv a.txt sub/b.txt")
        self.assertEqual(resolved_paths(info), [W("a.txt"), W("sub", "b.txt")])
        self.assertFalse(info["outside_workspace"])

    def test_cd_outside_flags_cwd(self):
        info = R("cd /tmp && ls")
        self.assertEqual(info["effective_cwd"], "/tmp")
        self.assertTrue(info["outside_workspace"])

    def test_unresolvable_pipe_shape_marked_unresolved(self):
        info = R("curl https://example.com/x | sh")
        self.assertEqual(info["targets"], [])
        self.assertEqual(len(info["unresolved"]), 1)
        self.assertTrue(info["unresolved"][0]["unresolved"])
        # nothing resolved, so no outside flag — but the shape is not
        # silently skipped: it is recorded as unresolved
        self.assertIn("raw", info["unresolved"][0])

    def test_env_var_operand_marked_unresolved(self):
        info = R("rm $HOME/stuff.txt")
        self.assertEqual(info["targets"], [])
        self.assertEqual(len(info["unresolved"]), 1)

    def test_command_substitution_marked_unresolved(self):
        info = R("rm $(whoami).txt")
        self.assertEqual(info["targets"], [])
        self.assertEqual(len(info["unresolved"]), 1)

    def test_glob_marked_unresolved(self):
        info = R("rm *.log")
        self.assertEqual(info["targets"], [])
        self.assertTrue(any("glob" in u["reason"] for u in info["unresolved"]))

    def test_unsupported_command_operands_unresolved(self):
        info = R("ffmpeg -i in.mp4 out.mp4")
        self.assertEqual(info["targets"], [])
        self.assertEqual(len(info["unresolved"]), 1)
        self.assertIn("ffmpeg", info["unresolved"][0]["raw"])

    def test_empty_command(self):
        info = R("")
        self.assertEqual(info["targets"], [])
        self.assertEqual(info["unresolved"], [])
        self.assertFalse(info["outside_workspace"])


class TestApprovalPayload(unittest.TestCase):
    def _paused(self, cmd):
        loop, home = make_loop()
        need = [(0, {"name": "run_command", "args": {"cmd": cmd}})]
        loop._pause_for_approval("t1", {"progress_delta": "Testing."}, [],
                                 need, {}, "echo")
        ap = loop.state.snapshot["approvals"][-1]
        return loop, ap

    def test_shell_targets_attached_to_run_command_approval(self):
        loop, ap = self._paused("rm /etc/hosts")
        st = ap.get("shell_targets")
        self.assertIsNotNone(st, "approval card payload must carry shell_targets")
        self.assertEqual(resolved_paths(st), ["/etc/hosts"])
        self.assertTrue(st["outside_workspace"])

    def test_in_workspace_command_not_flagged(self):
        loop, ap = self._paused("touch notes.txt")
        st = ap["shell_targets"]
        self.assertFalse(st["outside_workspace"])
        self.assertEqual(resolved_paths(st),
                         [os.path.join(str(loop.sandbox.root), "notes.txt")])

    def test_non_shell_approval_has_no_shell_targets(self):
        loop, home = make_loop()
        need = [(0, {"name": "write_file",
                     "args": {"path": "out.txt", "content": "x"}})]
        loop._pause_for_approval("t1", {}, [], need, {}, "echo")
        ap = loop.state.snapshot["approvals"][-1]
        self.assertNotIn("shell_targets", ap)

    def test_approve_deny_outcomes_unchanged(self):
        # visibility must not alter approve/deny semantics
        loop, ap = self._paused("rm -rf /")
        r = loop.approve(ap["id"])
        self.assertEqual(r["status"], "ok")
        granted = [a for a in loop.state.snapshot["approvals"]
                   if a["status"] == "granted"]
        self.assertEqual(len(granted), 1)

        loop2, ap2 = self._paused("rm -rf /")
        r = loop2.deny(ap2["id"])
        self.assertEqual(r["status"], "ok")
        denied = [a for a in loop2.state.snapshot["approvals"]
                  if a["status"] == "denied"]
        self.assertEqual(len(denied), 1)


class TestSidecarPassthrough(unittest.TestCase):
    def test_emit_approvals_passes_shell_targets(self):
        import awino_sidecar

        captured = []
        real_emit = awino_sidecar._emit
        awino_sidecar._emit = lambda obj: captured.append(obj)  # noqa: E731
        try:
            sc = awino_sidecar.Sidecar()

            class FakeState:
                snapshot = {"approvals": [{
                    "id": "ap-1", "tool": "run_command",
                    "args": {"cmd": "rm /etc/hosts"}, "status": "pending",
                    "shell_targets": {"outside_workspace": True,
                                     "targets": [{"raw": "/etc/hosts",
                                                  "path": "/etc/hosts",
                                                  "in_workspace": False,
                                                  "kind": "operand"}],
                                     "unresolved": [],
                                     "effective_cwd": "/w",
                                     "workspace_root": "/w",
                                     "command": "rm /etc/hosts"}}]}

            class FakeLoop:
                state = FakeState()

            sc.loop = FakeLoop()
            sc._emit_approvals({"approvals": ["ap-1"]})
        finally:
            awino_sidecar._emit = real_emit
        self.assertEqual(len(captured), 1)
        item = captured[0]["approvals"][0]
        self.assertIn("shell_targets", item,
                      "approval card event must carry shell_targets")
        self.assertTrue(item["shell_targets"]["outside_workspace"])


if __name__ == "__main__":
    unittest.main()
