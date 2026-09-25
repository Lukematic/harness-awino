"""Inline autocomplete (`complete` command): ephemeral, never a turn.

Covers:
- echo/scripted providers never fake completions (empty ghost text)
- complete never touches the journal / turn pipeline
- openai-compatible round-trip against a stub HTTP server:
  prompt caps, compact contract summary, num_predict=128, model override,
  fenced/refusal normalization, backend errors -> "" (never an exception)
- in-process unit tests for _strip_completion, contract summary compactness,
  and num_predict/timeout/last_egress save-restore hygiene.
"""
import http.server
import json
import re
import socketserver
import threading
import unittest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_sidecar import SidecarClient  # noqa: E402 (subprocess harness)
import awino_sidecar  # noqa: E402
from awino_sidecar import Sidecar, _strip_completion  # noqa: E402


class StubServer:
    """Records OpenAI-compatible chat-completions requests; replies canned."""

    def __init__(self, reply_text="x = 1", status=200):
        self.reply_text = reply_text
        self.status = status
        self.bodies = []
        self.paths = []

        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.bodies.append(body)
                outer.paths.append(self.path)
                payload = json.dumps(
                    {"choices": [{"message": {"content": outer.reply_text}}]}
                ).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if outer.status == 200:
                    self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self._srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        t.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self._srv.server_address[1]}"

    def shutdown(self):
        self._srv.shutdown()


def _complete(client, **args):
    r = client.cmd("complete", args, timeout=60)
    assert r["event"] == "command_result", r
    assert r["name"] == "complete", r
    assert r["ok"] is True, r  # best-effort: never an error to the UI
    return r["result"]


class CompleteProtocolTest(unittest.TestCase):
    def test_echo_never_fakes_completions(self):
        c = SidecarClient()
        try:
            e = c.hello(provider="echo")
            self.assertEqual(e["event"], "ready")
            r = _complete(c, prefix="def foo():\n    ", suffix="",
                          file="a.py", language="python")
            self.assertEqual(r["completion"], "")
        finally:
            c.close()

    def test_scripted_never_fakes_completions(self):
        c = SidecarClient()
        try:
            e = c.hello(provider="scripted", script=[])
            self.assertEqual(e["event"], "ready")
            r = _complete(c, prefix="def foo():\n    ", suffix="",
                          file="a.py", language="python")
            self.assertEqual(r["completion"], "")
        finally:
            c.close()

    def test_complete_never_touches_journal(self):
        c = SidecarClient()
        try:
            c.hello(provider="echo")
            j0 = c.cmd("journal")["result"]["journal"]
            _complete(c, prefix="x = ", suffix="", file="a.py",
                      language="python")
            _complete(c, prefix="y = ", suffix="", file="a.py",
                      language="python")
            j1 = c.cmd("journal")["result"]["journal"]
            self.assertEqual(j0, j1)
        finally:
            c.close()

    def test_empty_prefix_yields_empty(self):
        c = SidecarClient()
        try:
            c.hello(provider="echo")
            r = _complete(c, prefix="   \n  ", suffix="x",
                          file="a.py", language="python")
            self.assertEqual(r["completion"], "")
        finally:
            c.close()


class CompleteRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.stub = StubServer()
        self.addCleanup(self.stub.shutdown)
        self.c = SidecarClient()
        self.addCleanup(self.c.close)
        e = self.c.hello(provider="openai", model="base-model",
                         endpoint=self.stub.url)
        self.assertEqual(e["event"], "ready")

    def test_roundtrip_returns_completion(self):
        r = _complete(self.c, prefix="def foo():\n    ", suffix="\n",
                      file="/w/a.py", language="python")
        self.assertEqual(r["completion"], "x = 1")
        body = self.stub.bodies[-1]
        # tiny prompt: not the turn contract
        user = next(m["content"] for m in body["messages"]
                    if m["role"] == "user")
        self.assertIn("Mission context:", user)
        self.assertIn("code before cursor", user)
        self.assertNotIn("## MISSION", user)
        print(f"\n    [complete prompt bytes: {len(user.encode())}]")

    def test_prompt_caps(self):
        big_prefix = "p" * 5000
        big_suffix = "s" * 5000
        _complete(self.c, prefix=big_prefix, suffix=big_suffix,
                  file="a.py", language="python")
        user = next(m["content"] for m in self.stub.bodies[-1]["messages"]
                    if m["role"] == "user")
        # prefix cap 1500, suffix cap 750 (+ prompt scaffolding)
        self.assertLess(len(user), 1500 + 750 + 1200)
        self.assertIn("p" * 100, user)  # tail of prefix kept
        self.assertNotIn("p" * 2000, user)  # head truncated

    def test_small_sampling_budget(self):
        _complete(self.c, prefix="x = ", suffix="", file="a.py",
                  language="python")
        body = self.stub.bodies[-1]
        self.assertEqual(body["max_tokens"], 128)

    def test_contract_summary_compact_and_present(self):
        _complete(self.c, prefix="x = ", suffix="", file="a.py",
                  language="python")
        user = next(m["content"] for m in self.stub.bodies[-1]["messages"]
                    if m["role"] == "user")
        m = re.search(r"Mission context: ([^\n]*)\n", user)
        self.assertIsNotNone(m)
        summary = m.group(1)
        self.assertLessEqual(len(summary), 400)
        self.assertIn("phase=", summary)

    def test_fenced_response_unfenced(self):
        self.stub.reply_text = "```python\nx = 1\n```"
        r = _complete(self.c, prefix="x = ", suffix="", file="a.py",
                      language="python")
        self.assertEqual(r["completion"], "x = 1")

    def test_refusal_maps_to_empty(self):
        self.stub.reply_text = ("I cannot complete this: writing ship-phase "
                                "code during DEFINE would be wrong.")
        r = _complete(self.c, prefix="x = ", suffix="", file="a.py",
                      language="python")
        self.assertEqual(r["completion"], "")

    def test_backend_error_yields_empty_not_exception(self):
        self.stub.status = 500
        r = _complete(self.c, prefix="x = ", suffix="", file="a.py",
                      language="python")
        self.assertEqual(r["completion"], "")
        self.assertIn("reason", r)

    def test_model_override_uses_transient_backend(self):
        _complete(self.c, prefix="x = ", suffix="", file="a.py",
                  language="python", model="override-model")
        self.assertEqual(self.stub.bodies[-1]["model"], "override-model")
        # session backend untouched: next call without override uses base
        _complete(self.c, prefix="x = ", suffix="", file="a.py",
                  language="python")
        self.assertEqual(self.stub.bodies[-1]["model"], "base-model")

    def test_egress_consumed_never_journaled(self):
        j0 = self.c.cmd("journal")["result"]["journal"]
        _complete(self.c, prefix="x = ", suffix="", file="a.py",
                  language="python")
        j1 = self.c.cmd("journal")["result"]["journal"]
        self.assertEqual(j0, j1)
        blob = json.dumps(j1)
        self.assertNotIn("127.0.0.1", blob)


class FakeBackend:
    """Chat backend recording how _chat was invoked."""

    def __init__(self):
        self.num_predict = 1024
        self.timeout = 180
        self.last_egress = {"destination": "http://x", "bytes_out": 1,
                            "bytes_in": 1}
        self.seen = None

    def _chat(self, prompt, system):
        self.seen = (prompt, system, self.num_predict, self.timeout)
        self.last_egress = {"destination": "http://x/y",
                            "bytes_out": 9, "bytes_in": 9}
        return "  done  "


class FakeLoop:
    def __init__(self, backend, snapshot):
        self.backend = backend
        self.state = type("S", (), {"snapshot": snapshot})()


def _sidecar_with(backend, snapshot):
    s = Sidecar.__new__(Sidecar)
    s.loop = FakeLoop(backend, snapshot)
    s.provider = "openai"
    s._binding = {"provider": "openai", "model": "m",
                  "endpoint": "http://e"}
    return s


class CompleteUnitTest(unittest.TestCase):
    def test_strip_completion(self):
        self.assertEqual(_strip_completion(None), "")
        self.assertEqual(_strip_completion("   "), "")
        self.assertEqual(_strip_completion("x = 1"), "x = 1")
        self.assertEqual(_strip_completion("```python\nx = 1\n```"), "x = 1")
        self.assertEqual(_strip_completion("```\nx = 1\n```"), "x = 1")
        self.assertEqual(_strip_completion("I cannot complete this"), "")
        self.assertEqual(_strip_completion(
            "Refusing: wrong phase for this code"), "")

    def test_contract_summary_compact(self):
        snap = {"phase": "BUILD", "mode": "build",
                "mission": {"text": "Fix the login bug " * 20,
                            "done_criteria": ["c1 " * 30, "c2", "c3",
                                              "c4", "c5"]},
                "skills": ["s1", "s2", "s3", "s4", "s5"]}
        s = _sidecar_with(FakeBackend(), snap)
        summary = s._completion_contract_summary()
        self.assertLessEqual(len(summary), 400)
        self.assertIn("phase=BUILD", summary)
        self.assertIn("mode=build", summary)
        self.assertIn("Fix the login bug", summary)

    def test_contract_summary_no_mission(self):
        s = _sidecar_with(FakeBackend(), {"phase": "IDLE"})
        summary = s._completion_contract_summary()
        self.assertIn("phase=IDLE", summary)

    def test_sampling_hygiene_save_restore(self):
        b = FakeBackend()
        s = _sidecar_with(b, {"phase": "BUILD"})
        r = s._cmd_complete({"prefix": "x = ", "suffix": "",
                             "file": "a.py", "language": "python"})
        self.assertEqual(r["completion"], "done")
        # small budget DURING the call
        self.assertEqual(b.seen[2], 128)
        self.assertEqual(b.seen[3], 15)
        # restored AFTER the call
        self.assertEqual(b.num_predict, 1024)
        self.assertEqual(b.timeout, 180)
        # egress consumed, never left for the next turn to journal
        self.assertIsNone(b.last_egress)

    def test_no_chat_backend_empty(self):
        class NoChat:
            pass
        s = _sidecar_with(NoChat(), {"phase": "IDLE"})
        r = s._cmd_complete({"prefix": "x = ", "suffix": ""})
        self.assertEqual(r["completion"], "")

    def test_model_override_builds_transient(self):
        seen_models = []

        class Transient(FakeBackend):
            def __init__(self, model):
                super().__init__()
                self.model = model
                seen_models.append(model)

        b = FakeBackend()
        s = _sidecar_with(b, {"phase": "BUILD"})
        orig_make = s._make_backend
        s._make_backend = lambda p, cmd, api_key=None: Transient(
            cmd.get("model"))
        try:
            s._cmd_complete({"prefix": "x = ", "suffix": "",
                             "model": "tiny-fast"})
        finally:
            s._make_backend = orig_make
        self.assertEqual(seen_models, ["tiny-fast"])
        # session backend was not replaced or mutated
        self.assertEqual(b.num_predict, 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)
