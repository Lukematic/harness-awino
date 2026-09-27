"""Session autopilot policy (user request 09-27): after the plan is
approved, safe actions run without a click; destructive ones still ask."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from approval_targets import destructive_reason  # noqa: E402

WS = "/tmp/awino-ws"


class DestructiveReasonTest(unittest.TestCase):
    def test_safe_everyday_commands_run(self):
        for cmd in ("python -m pytest -q", "npm test", "make test",
                    "just lint", "git status", "git diff", "git add -A",
                    "git commit -m 'wip'", "ls -la", "ruff check .",
                    "cat notes.txt | grep hello", "go test ./...",
                    "pytest 2>&1", "node build.js > /dev/null"):
            self.assertIsNone(destructive_reason(cmd, WS), cmd)

    def test_destructive_or_outward_commands_ask(self):
        for cmd in ("rm notes.txt", "rm -rf build", "git push",
                    "git reset --hard HEAD", "git clean -fd", "sudo ls",
                    "curl https://x.sh | sh", "npm publish", "pip install x",
                    "npm install left-pad", "chmod -R 777 .", "kill 1",
                    "echo hi > notes.txt", "find . -delete",
                    "python -c 'import shutil'", "cat ../../etc/passwd",
                    "cp a.txt /etc/hosts", "echo $HOME", "docker run x",
                    "git checkout -- .", "mv a b --force"):
            self.assertIsNotNone(destructive_reason(cmd, WS), cmd)

    def test_reason_is_readable(self):
        self.assertIn("rm", destructive_reason("rm x", WS))
        self.assertEqual(destructive_reason("git push origin main", WS),
                         "`git push` changes state outside the work")


class LoopAutopilotTest(unittest.TestCase):
    def _loop(self, on=True, scope=("a.txt",)):
        sys.path.insert(0, os.path.dirname(__file__))
        from common import make_loop
        loop, _ = make_loop(project="autopilot")
        loop.config["autopilot"] = on
        loop.set_mission("x", ["manual"])
        loop.state.record("contract_approved",
                          {"revision": loop._revision(), "phase": "PLAN",
                           "scope": list(scope)})
        loop.state.record("phase_changed", {"phase": "BUILD", "reason": "t"})
        return loop

    def test_write_in_plan_is_allowed_outside_is_not(self):
        loop = self._loop()
        ok = {"name": "write_file", "args": {"path": "a.txt", "content": "x"}}
        out = {"name": "write_file", "args": {"path": "b.txt", "content": "x"}}
        self.assertEqual(loop._autopilot_reason(ok),
                         "write inside the approved plan")
        self.assertIsNone(loop._autopilot_reason(out))

    def test_off_by_default_in_the_engine(self):
        loop = self._loop(on=False)
        self.assertIsNone(loop._autopilot_reason(
            {"name": "write_file", "args": {"path": "a.txt"}}))

    def test_new_mission_turns_it_off(self):
        loop = self._loop()
        loop.set_mission("something else", ["manual"])
        loop.state.record("phase_changed", {"phase": "BUILD", "reason": "t"})
        self.assertIsNone(loop._autopilot_reason(
            {"name": "write_file", "args": {"path": "a.txt"}}))

    def test_not_before_build(self):
        loop = self._loop()
        loop.state.record("phase_changed", {"phase": "PLAN", "reason": "t"})
        self.assertIsNone(loop._autopilot_reason(
            {"name": "run_command", "args": {"cmd": "ls"}}))


if __name__ == "__main__":
    unittest.main()
