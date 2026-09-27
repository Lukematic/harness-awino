"""The model has to see what it is working on and be able to answer at
length. Regressions found by the 09-27 team review:

- AnthropicBackend inherited the OpenAI native-tools path: every tools
  round posted to /v1/chat/completions with no x-api-key (HTTP 401), or
  crashed on a missing self.temperature.
- max_tokens was hard-coded to 1024 for hosted providers: any file write
  over ~3 KB was cut off mid-JSON.
- After read_file the model was told "N chars read" and never saw the
  file; command output was cut to 200 chars of stdout, stderr dropped.
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import awino_sidecar as s  # noqa: E402
from backends import OllamaBackend, default_max_tokens  # noqa: E402
from loop import Loop  # noqa: E402

TOOLS = [{"name": "read_file", "description": "Read a file.",
          "parameters": {"type": "object",
                         "properties": {"path": {"type": "string"}},
                         "required": ["path"]}}]
CONTRACT = "HEADER-1\nobjective: test"


class _Anthropic(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        _Anthropic.seen.append({"path": self.path, "body": body,
                                "headers": {k.lower(): v for k, v
                                            in self.headers.items()}})
        turn = {"header": "HEADER-1", "objective": "o", "tool_calls": []}
        out = json.dumps({"content": [
            {"type": "text", "text": json.dumps(turn)},
            {"type": "tool_use", "id": "tu1", "name": "read_file",
             "input": {"path": "a.txt"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class AnthropicToolsTest(unittest.TestCase):
    def setUp(self):
        _Anthropic.seen = []
        self.srv = socketserver.TCPServer(("127.0.0.1", 0), _Anthropic)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AWINO_MAX_TOKENS", None)
        self.b = s.AnthropicBackend(
            model="claude-x", api_key="K-anthropic",
            endpoint=f"http://127.0.0.1:{self.srv.server_address[1]}")

    def test_tools_round_uses_messages_api_with_key(self):
        turn = self.b.generate(CONTRACT, [], tools=TOOLS)
        req = _Anthropic.seen[0]
        self.assertEqual(req["path"], "/v1/messages")
        self.assertEqual(req["headers"].get("x-api-key"), "K-anthropic")
        self.assertEqual(req["body"]["tools"][0]["name"], "read_file")
        self.assertIn("input_schema", req["body"]["tools"][0])
        self.assertEqual(req["body"]["temperature"], 0.2)
        self.assertNotIn("backend error", json.dumps(turn))
        self.assertIn("read_file",
                      [c.get("name") for c in turn.get("tool_calls") or []])

    def test_output_limit_is_not_1024(self):
        self.b.generate(CONTRACT, [], tools=TOOLS)
        self.assertEqual(_Anthropic.seen[0]["body"]["max_tokens"], 8192)

    def test_output_limit_env_override(self):
        with mock.patch.dict(os.environ, {"AWINO_MAX_TOKENS": "16000"}):
            self.assertEqual(default_max_tokens(), 16000)
            b = s.OpenAICompatibleBackend(model="m", endpoint="http://x/v1",
                                          api_key="k")
            self.assertEqual(b.num_predict, 16000)
        with mock.patch.dict(os.environ, {"AWINO_MAX_TOKENS": "junk"}):
            self.assertEqual(default_max_tokens(), 8192)


class ToolOutputReachesTheModelTest(unittest.TestCase):
    def test_read_file_content_is_in_the_detail(self):
        body = "def slugify(s):\n    return s.lower()\n"
        d = Loop._result_detail({"tool": "read_file",
                                 "result": {"path": "slug.py", "content": body}})
        self.assertIn("return s.lower()", d)
        self.assertEqual(Loop._result_summary(
            {"tool": "read_file", "result": {"content": body}}),
            f"{len(body)} chars read")  # the short form is unchanged

    def test_run_command_keeps_stderr(self):
        d = Loop._result_detail({"tool": "run_command", "result": {
            "exit_code": 1, "stdout": "collected 3",
            "stderr": "AssertionError: 'a b' != 'a-b'"}})
        self.assertIn("exit=1", d)
        self.assertIn("AssertionError: 'a b' != 'a-b'", d)

    def test_long_file_is_capped_with_a_note(self):
        d = Loop._result_detail({"tool": "read_file", "result": {
            "path": "big.txt", "content": "x" * 50000}})
        self.assertLess(len(d), 13000)
        self.assertIn("more chars", d)

    def test_prompt_carries_tool_output_not_300_chars(self):
        b = OllamaBackend(model="m", host="http://127.0.0.1:9")
        big = "tool read_file -> " + "y" * 9000
        prompt = b._user_prompt(CONTRACT, [{"role": "tool", "text": big}], None)
        self.assertIn("y" * 9000, prompt)

    def test_prompt_history_stays_within_budget(self):
        import backends
        b = OllamaBackend(model="m", host="http://127.0.0.1:9")
        hist = [{"role": "tool", "text": "z" * 30000} for _ in range(12)]
        prompt = b._user_prompt(CONTRACT, hist, None)
        self.assertLess(len(prompt), backends.HISTORY_BUDGET_CHARS + 2000)


if __name__ == "__main__":
    unittest.main()
