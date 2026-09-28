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


class TokenEfficiencyTest(unittest.TestCase):
    """09-28: skills in the cached system prompt, real usage, turn budget."""

    def test_anthropic_system_is_one_cacheable_block_with_skills(self):
        _Anthropic.seen = []
        srv = socketserver.TCPServer(("127.0.0.1", 0), _Anthropic)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        b = s.AnthropicBackend(model="claude-x", api_key="k",
                               endpoint=f"http://127.0.0.1:{srv.server_address[1]}")
        b.skill_context = "## SKILLS\n### debug\nFind the cause first."
        b.generate(CONTRACT, [], tools=TOOLS)
        system = _Anthropic.seen[0]["body"]["system"]
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Find the cause first.", system[0]["text"])
        self.assertNotIn("HEADER-1", system[0]["text"])

    def test_contract_names_skills_when_backend_holds_the_bodies(self):
        from contract import compile_contract, skill_context_text
        from common import make_loop
        loop, _ = make_loop(project="skills-sys")
        loop.state.snapshot["skills"] = ["debug"]
        inline = compile_contract(loop.state)
        named = compile_contract(loop.state, skills_in_system=True)
        body = skill_context_text(["debug"])
        self.assertIn("### debug", inline)
        self.assertNotIn("### debug", named)
        self.assertIn("Routed this turn: debug", named)
        self.assertLess(len(named), len(inline) - 200)
        self.assertIn("### debug", body)

    def test_usage_parsing(self):
        from backends import usage_from_payload
        self.assertEqual(usage_from_payload({"usage": {
            "input_tokens": 100, "output_tokens": 50,
            "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}}),
            {"input": 1000, "output": 50, "cached": 900, "cache_write": 0})
        self.assertEqual(usage_from_payload({"usage": {
            "prompt_tokens": 800, "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 600}}}),
            {"input": 800, "output": 20, "cached": 600, "cache_write": 0})
        self.assertIsNone(usage_from_payload({}))

    def test_meter_uses_real_counts_and_budget_stops_the_turn(self):
        from common import make_loop
        loop, _ = make_loop(project="meter")
        loop.backend.last_usage = {"input": 1000, "output": 50, "cached": 900}
        loop._charge_tokens("contract", {}, "t1")
        m = loop.token_meter("t1")
        self.assertEqual((m["turn_tokens"], m["cached_pct"], m["measured"]),
                         (1050, 90, True))
        loop.config["turn_token_budget"] = 1000
        r = loop._halt_turn("token_budget", "t1", 1, 3)
        self.assertEqual(r["status"], "budget_exhausted")
        self.assertIn("1,050 tokens", r["said"])


class WrapperForwardsTurnStateTest(unittest.TestCase):
    def test_skill_context_and_usage_reach_the_real_backend(self):
        inner = s.AnthropicBackend(model="m", api_key="k")
        w = s._ModeAwareBackend(inner, sidecar=None)
        w.skill_context = "## SKILLS x"
        self.assertEqual(inner.skill_context, "## SKILLS x")
        inner.last_usage = {"input": 1, "output": 1, "cached": 0}
        self.assertEqual(w.last_usage["input"], 1)
        w.last_usage = None
        self.assertIsNone(inner.last_usage)
        self.assertTrue(w.supports_skill_context)
