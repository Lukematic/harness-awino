"""A.W.I.N.O. gate hook (Claude Code PreToolUse): fail-closed tool boundary.

Adversarial coverage:
  - unoffered tool is blocked (exit 2) with the tool named in the reason
  - missing/malformed/expired contract blocks fail-closed, never crashes open
  - no .awino/ dir -> hook is completely inert (exit 0)
  - garbage stdin -> blocked, not crashed
  - the integrations/claude/hooks wrapper resolves and runs the real hook
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import awino_gate


def _project(contract=None):
    d = tempfile.mkdtemp(prefix="awino-gate-")
    if contract is not None:
        os.makedirs(os.path.join(d, ".awino"), exist_ok=True)
        p = os.path.join(d, ".awino", "contract.json")
        if isinstance(contract, str):
            with open(p, "w") as f:
                f.write(contract)  # raw: for malformed-JSON cases
        else:
            with open(p, "w") as f:
                json.dump(contract, f)
    return d


def _contract(**kw):
    c = {"mission_id": "m-1", "stage": "BUILD",
         "tools_offered": ["Read", "Bash"], "turn": 1, "expires_turn": 5}
    c.update(kw)
    return c


def _payload(d, tool="Bash"):
    return {"tool_name": tool, "tool_input": {"command": "echo hi"},
            "cwd": d, "session_id": "s1", "hook_event_name": "PreToolUse"}


class TestGate(unittest.TestCase):
    def test_not_opted_in_is_inert(self):
        d = _project()  # no .awino/
        self.assertEqual(awino_gate.gate(_payload(d), d), 0)

    def test_offered_tool_passes(self):
        d = _project(_contract())
        self.assertEqual(awino_gate.gate(_payload(d, "Bash"), d), 0)

    def test_unoffered_tool_blocked(self):
        d = _project(_contract())
        with io.StringIO() as buf, redirect_stdout(buf):
            rc = awino_gate.gate(_payload(d, "Write"), d)
            out = buf.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn("Write", out)  # the reason names the tool
        self.assertIn("block", out)

    def test_malformed_contract_blocks(self):
        d = _project("{not valid json")
        with io.StringIO() as buf, redirect_stdout(buf):
            rc = awino_gate.gate(_payload(d), d)
        self.assertEqual(rc, 2)

    def test_non_dict_contract_blocks(self):
        d = _project([1, 2, 3])
        with io.StringIO() as buf, redirect_stdout(buf):
            rc = awino_gate.gate(_payload(d), d)
        self.assertEqual(rc, 2)

    def test_missing_tools_offered_blocks(self):
        d = _project(_contract(tools_offered="Bash"))  # wrong type
        with io.StringIO() as buf, redirect_stdout(buf):
            rc = awino_gate.gate(_payload(d), d)
        self.assertEqual(rc, 2)

    def test_expired_contract_blocks(self):
        d = _project(_contract(turn=9, expires_turn=5))
        with io.StringIO() as buf, redirect_stdout(buf):
            rc = awino_gate.gate(_payload(d, "Bash"), d)
            out = buf.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn("expired", out)

    def test_valid_through_expiry_turn(self):
        d = _project(_contract(turn=5, expires_turn=5))
        self.assertEqual(awino_gate.gate(_payload(d, "Bash"), d), 0)


class TestMain(unittest.TestCase):
    def _run_main(self, stdin_text, d=None):
        payload = _payload(d or tempfile.mkdtemp(prefix="awino-gate-"))
        raw = stdin_text if stdin_text is not None else json.dumps(payload)
        with mock.patch.object(awino_gate.sys, "stdin", io.StringIO(raw)):
            with io.StringIO() as buf, redirect_stdout(buf):
                rc = awino_gate.main()
                out = buf.getvalue()
        return rc, out

    def test_garbage_stdin_blocks_not_crashes(self):
        d = _project(_contract())
        rc, out = self._run_main("{{{{not json", d)
        self.assertEqual(rc, 2)
        self.assertIn("block", out)

    def test_empty_stdin_falls_back_to_process_cwd(self):
        # Empty stdin carries no project dir, so the hook falls back to the
        # process cwd. The test runner's cwd is not opted in -> inert (0).
        # (Claude Code always sends valid JSON; the agent cannot influence
        # the hook's stdin, so this path is not a bypass.)
        d = _project(_contract())
        rc, _ = self._run_main("", d)
        self.assertEqual(rc, 0)

    def test_non_object_stdin_blocks(self):
        d = _project(_contract())
        rc, _ = self._run_main("[1,2]", d)
        self.assertEqual(rc, 2)

    def test_end_to_end_allowed(self):
        d = _project(_contract())
        rc, _ = self._run_main(None, d)
        self.assertEqual(rc, 0)


class TestWrapper(unittest.TestCase):
    WRAPPER = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..",
        "integrations", "claude", "hooks", "awino_gate.py"))

    def _run_wrapper(self, payload):
        p = subprocess.run(
            [sys.executable, self.WRAPPER],
            input=json.dumps(payload), capture_output=True, text=True,
            timeout=30)
        return p.returncode, p.stdout

    def test_wrapper_allows_offered_tool(self):
        d = _project(_contract())
        rc, _ = self._run_wrapper(_payload(d, "Read"))
        self.assertEqual(rc, 0)

    def test_wrapper_blocks_unoffered_tool(self):
        d = _project(_contract())
        rc, out = self._run_wrapper(_payload(d, "Write"))
        self.assertEqual(rc, 2)
        self.assertIn("Write", out)

    def test_wrapper_inert_without_awino_dir(self):
        d = _project()
        rc, _ = self._run_wrapper(_payload(d, "Write"))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
