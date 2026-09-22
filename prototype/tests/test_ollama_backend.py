"""OllamaBackend: real-model backend with fully mocked HTTP.

The suite stays offline: urllib.request.urlopen is patched, so these tests
never touch a server. The LIVE test against a real local model is separate
(see /tmp/awino-live-test.py, run by hand).
"""
import io
import json
import os
import unittest
from unittest.mock import patch

from backends import OllamaBackend, _extract_json

HEADER = "[A.W.I.N.O. | phase: BUILD | mode: build | stance: advisor | test]"


def _good_turn():
    return {
        "header": HEADER,
        "objective": "Summarize the project layout",
        "plan": ["List the directory", "Read the README", "Summarize"],
        "tool_calls": [{"name": "list_dir", "args": {}}],
        "questions": [],
        "assumptions": [],
        "progress_delta": "Listing the directory first.",
        "done_claim": False,
    }


def _resp(content: str):
    """Fake urlopen context manager returning an OpenAI-style payload."""
    payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    class FakeResp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return FakeResp()


def _backend_with(content: str):
    b = OllamaBackend(model="test-model", host="http://127.0.0.1:9")
    patcher = patch("urllib.request.urlopen", return_value=_resp(content))
    return b, patcher


class TestOllamaBackend(unittest.TestCase):
    def test_successful_parse(self):
        b, p = _backend_with(json.dumps(_good_turn()))
        with p:
            turn = b.generate(HEADER + "\nrest of contract", [], None)
        self.assertEqual(turn["header"], HEADER)
        self.assertEqual(turn["tool_calls"], [{"name": "list_dir", "args": {}}])
        self.assertEqual(len(b.calls), 1)

    def test_fenced_json_extraction(self):
        fenced = "```json\n" + json.dumps(_good_turn()) + "\n```"
        b, p = _backend_with("Here is my turn:\n" + fenced)
        with p:
            turn = b.generate(HEADER + "\ncontract", [], None)
        self.assertEqual(turn["objective"], "Summarize the project layout")

    def test_unparseable_output_returns_safe_fallback(self):
        b, p = _backend_with("I am a teapot, not JSON at all")
        with p:
            turn = b.generate(HEADER + "\ncontract", [], None)
        # Safe fallback: schema-valid, header echoed, zero tool calls,
        # carries a question instead of acting.
        self.assertEqual(turn["header"], HEADER)
        self.assertEqual(turn["tool_calls"], [])
        self.assertTrue(turn["questions"])
        self.assertFalse(turn["done_claim"])
        self.assertTrue(turn["progress_delta"].strip())

    def test_backend_error_returns_safe_fallback(self):
        b = OllamaBackend(model="test-model", host="http://127.0.0.1:9")
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            turn = b.generate(HEADER + "\ncontract", [], None)
        self.assertEqual(turn["tool_calls"], [])
        self.assertTrue(turn["questions"])
        self.assertIn("backend error", turn["questions"][0])

    def test_header_passed_through_verbatim(self):
        # The backend must NOT paper over a bad header: the pipeline's
        # position sensor has to see what the model actually emitted.
        t = _good_turn()
        t["header"] = "WRONG HEADER"
        b, p = _backend_with(json.dumps(t))
        with p:
            turn = b.generate(HEADER + "\ncontract", [], None)
        self.assertEqual(turn["header"], "WRONG HEADER")

    def test_missing_fields_get_safe_defaults(self):
        partial = {"header": HEADER, "progress_delta": "Working."}
        b, p = _backend_with(json.dumps(partial))
        with p:
            turn = b.generate(HEADER + "\ncontract", [], None)
        self.assertEqual(turn["plan"], [])
        self.assertEqual(turn["tool_calls"], [])
        self.assertFalse(turn["done_claim"])

    def test_env_config(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://example:8080",
                                     "OLLAMA_MODEL": "mymodel"}):
            b = OllamaBackend()
        self.assertEqual(b.host, "http://example:8080")
        self.assertEqual(b.model, "mymodel")

    def test_posts_to_openai_compatible_endpoint(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            return _resp(json.dumps(_good_turn()))

        b = OllamaBackend(model="m", host="http://h:11434")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            b.generate(HEADER + "\ncontract", [], None)
        self.assertEqual(captured["url"], "http://h:11434/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "m")
        roles = [m["role"] for m in captured["body"]["messages"]]
        self.assertEqual(roles, ["system", "user"])

    def test_system_prompt_embeds_exact_header_for_echo(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _resp(json.dumps(_good_turn()))

        b = OllamaBackend(model="m", host="http://h:11434")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            b.generate(HEADER + "\ncontract", [], None)
        system = captured["body"]["messages"][0]["content"]
        self.assertIn("```\n  " + HEADER + "\n  ```", system)


class TestExtractJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(_extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_surrounding_prose(self):
        self.assertEqual(_extract_json('Sure! {"a": 1} Done.'), {"a": 1})

    def test_garbage(self):
        self.assertIsNone(_extract_json("no braces here"))

    def test_invalid_json(self):
        self.assertIsNone(_extract_json("{not valid json}"))


if __name__ == "__main__":
    unittest.main()
