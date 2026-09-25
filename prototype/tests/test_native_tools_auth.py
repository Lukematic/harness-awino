"""v0.6.1 regression: the native-tools chat path must authenticate and
route exactly like the ordinary chat path.

v0.6.0 bug: OpenAICompatibleBackend inherited OllamaBackend._chat_tools,
which (a) sent no Authorization header -> HTTP 401 on keyed endpoints,
(b) posted to host + /v1/chat/completions, ignoring chat_url (an endpoint
ending in /v1 became /v1/v1/...), (c) read self.temperature, which the
subclass never set, and (d) let HTTPError escape unmapped. Bedrock SigV4
inherited the same unsigned request.

Each test drives backend.generate(tools=...) end to end against a local
HTTP server, so the header/URL actually on the wire is asserted.
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

import awino_sidecar as s

TOOLS = [{"name": "read_file", "description": "Read a file.",
          "parameters": {"type": "object",
                         "properties": {"path": {"type": "string"}},
                         "required": ["path"]}}]
CONTRACT = "HEADER-1\nobjective: test"


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        _Handler.seen.append({"path": self.path,
                              "headers": {k.lower(): v for k, v
                                          in self.headers.items()},
                              "body": body})
        if _Handler.status != 200:
            self.send_response(_Handler.status)
            self.end_headers()
            return
        turn = {"header": "HEADER-1", "objective": "o", "tool_calls": []}
        payload = {"choices": [{"message": {
            "content": json.dumps(turn),
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": "a.txt"})}}]}}]}
        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class NativeToolsAuthTest(unittest.TestCase):
    def setUp(self):
        _Handler.seen = []
        _Handler.status = 200
        self.srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        self.port = self.srv.server_address[1]
        t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AWINO_API_KEY", None)

    def _backend(self, endpoint_suffix="/v1", api_key="K-native"):
        return s.OpenAICompatibleBackend(
            model="m", endpoint=f"http://127.0.0.1:{self.port}{endpoint_suffix}",
            api_key=api_key)

    def test_native_path_sends_bearer(self):
        turn = self._backend().generate(CONTRACT, [], tools=TOOLS)
        self.assertEqual(len(_Handler.seen), 1)
        req = _Handler.seen[0]
        self.assertEqual(req["headers"].get("authorization"), "Bearer K-native")
        self.assertIn("tools", req["body"])
        self.assertNotIn("backend error", json.dumps(turn))

    def test_native_path_uses_chat_url_not_host(self):
        # Endpoint ending in /v1 must not become /v1/v1/chat/completions.
        self._backend("/v1").generate(CONTRACT, [], tools=TOOLS)
        self.assertEqual(_Handler.seen[0]["path"], "/v1/chat/completions")

    def test_native_path_matches_plain_chat_auth(self):
        b = self._backend()
        b.generate(CONTRACT, [])               # ordinary path
        b.generate(CONTRACT, [], tools=TOOLS)  # native path
        plain, native = _Handler.seen
        self.assertEqual(plain["path"], native["path"])
        self.assertEqual(plain["headers"].get("authorization"),
                         native["headers"].get("authorization"))

    def test_native_calls_are_merged(self):
        turn = self._backend().generate(CONTRACT, [], tools=TOOLS)
        names = [c.get("name") for c in turn.get("tool_calls") or []]
        self.assertIn("read_file", names)
        self.assertNotIn("_native_tool_errors", turn)

    def test_default_temperature_without_override(self):
        self._backend().generate(CONTRACT, [], tools=TOOLS)
        self.assertEqual(_Handler.seen[0]["body"]["temperature"], 0.2)

    def test_no_key_sends_no_authorization(self):
        self._backend(api_key=None).generate(CONTRACT, [], tools=TOOLS)
        self.assertNotIn("authorization", _Handler.seen[0]["headers"])

    def test_http_401_is_mapped_without_key_material(self):
        _Handler.status = 401
        turn = self._backend().generate(CONTRACT, [], tools=TOOLS)
        blob = json.dumps(turn)
        self.assertIn("endpoint HTTP 401", blob)
        self.assertNotIn("K-native", blob)

    def test_bedrock_native_path_is_sigv4_signed(self):
        os.environ["AWS_ACCESS_KEY_ID"] = "AKIDEXAMPLE"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
        os.environ["AWINO_API_KEY"] = "SHOULD-NEVER-BE-SENT"
        b = s.BedrockSigV4Backend(
            model="m",
            endpoint="https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1")
        b.chat_url = f"http://127.0.0.1:{self.port}/openai/v1/chat/completions"
        b.generate(CONTRACT, [], tools=TOOLS)
        auth = _Handler.seen[0]["headers"].get("authorization", "")
        self.assertTrue(auth.startswith("AWS4-HMAC-SHA256 "), auth[:40])
        self.assertNotIn("SHOULD-NEVER-BE-SENT", auth)


if __name__ == "__main__":
    unittest.main()
