"""Secret redaction at the journaling boundary and sidecar log output.

Fail-before-pass workstream test: high-confidence secrets (API keys,
tokens) must never persist in cleartext in journaled events or sidecar
log output. Redaction preserves a short identifiable tail so debugging
stays possible without the secret.

Conservative by design: only known prefixes/shapes are redacted; ordinary
prose is never mangled.
"""
import importlib.util as _ilu
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import secret_redaction as sr
from secret_redaction import redact_text, redact
from state import ProjectState

# Load the sidecar as a module (fast, no subprocess) to exercise _emit.
_spec = _ilu.spec_from_file_location(
    "awino_sidecar_mod",
    str(Path(__file__).resolve().parent.parent / "awino_sidecar.py"))
sc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(sc)

# Distinct, fake-but-well-shaped secrets for each pattern class.
# Built by concatenation so that no contiguous provider-token-shaped
# literal appears in this file: GitHub push protection blocks pushes
# containing such strings even when they are obvious test fixtures.
# The runtime values are unchanged, so every tail assertion below holds.
def _fx(*parts):
    """Join fixture fragments; keeps token-shaped literals out of source."""
    return "".join(parts)


AWS_KEY = _fx("AKIA", "IOSFODNN7EXAMPLE")
OPENAI_KEY = _fx("sk-proj-", "9f2KxQvLmN8pQrT5wXyZ7aBcD4eFgH1iJkL6")
OPENROUTER_KEY = _fx("sk-or-v1-", "9f2KxQvLmN8pQrT5wXyZ7aBcD4eFgH1iJkL")
ANTHROPIC_KEY = _fx("sk-ant-api03-", "9f2KxQvLmN8pQrT5wXyZ7aBcD4eFgH1iJkL6mNoPqR")
GITHUB_PAT = _fx("ghp_", "9f2KxQvLmN8pQrT5wXyZ7aBcD4eFgH1iJ")
GITHUB_OAUTH = _fx("gho_", "9f2KxQvLmN8pQrT5wXyZ7aBcD4eFgH1iJ")
GITHUB_FG_PAT = _fx("github_pat_", "9f2KxQvLmN8pQrT5wXyZ7aBcD4eFgH1iJkL6mN")
BEARER_TOKEN = _fx("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.",
                   "9f2KxQvLmN8pQrT5")
SLACK_BOT = _fx("xoxb-", "123456789012-345678901234-9f2KxQvLmN8pQrT5wXyZ")
SLACK_USER = _fx("xoxp-", "123456789012-345678901234-9f2KxQvLmN8pQrT5wXyZ")
ASSIGN_SECRET = _fx("9f2KxQvLmN8pQrT5wXyZ", "7aBcD4eFgH1iJkL")


class RedactTextPatternsTest(unittest.TestCase):
    """Each pattern class redacts the secret and keeps a short tail."""

    def test_aws_key(self):
        out = redact_text(f"key={AWS_KEY}")
        self.assertNotIn(AWS_KEY, out)
        self.assertIn("AKIA...MPLE", out)

    def test_openai_sk_key(self):
        out = redact_text(OPENAI_KEY)
        self.assertNotIn(OPENAI_KEY, out)
        self.assertIn("sk-...JkL6", out)

    def test_openrouter_sk_or_key(self):
        out = redact_text(f"Authorization: {OPENROUTER_KEY}")
        self.assertNotIn(OPENROUTER_KEY, out)
        self.assertIn("sk-or-v1-...iJkL", out)

    def test_anthropic_sk_ant_key(self):
        out = redact_text(ANTHROPIC_KEY)
        self.assertNotIn(ANTHROPIC_KEY, out)
        self.assertIn("sk-ant-...oPqR", out)

    def test_github_tokens(self):
        for tok, prefix in ((GITHUB_PAT, "ghp_"),
                            (GITHUB_OAUTH, "gho_"),
                            (GITHUB_FG_PAT, "github_pat_")):
            out = redact_text(f"token={tok}")
            self.assertNotIn(tok, out, prefix)
            self.assertIn(f"{prefix}...{tok[-4:]}", out, prefix)

    def test_bearer_header(self):
        out = redact_text(f"Authorization: Bearer {BEARER_TOKEN}")
        self.assertNotIn(BEARER_TOKEN, out)
        self.assertIn("Bearer ...QrT5", out)

    def test_slack_tokens(self):
        for tok, prefix in ((SLACK_BOT, "xoxb-"), (SLACK_USER, "xoxp-")):
            out = redact_text(f"token={tok}")
            self.assertNotIn(tok, out, prefix)
            self.assertIn(f"{prefix}...{tok[-4:]}", out, prefix)

    def test_generic_assignment_high_entropy(self):
        for name in ("api_key", "secret", "token", "password"):
            out = redact_text(f"{name}={ASSIGN_SECRET}")
            self.assertNotIn(ASSIGN_SECRET, out, name)
            self.assertIn(f"{name}=...{ASSIGN_SECRET[-4:]}", out, name)

    def test_generic_assignment_quoted_colon_form(self):
        out = redact_text(f'"api_key": "{ASSIGN_SECRET}"')
        self.assertNotIn(ASSIGN_SECRET, out)
        self.assertIn(f"...{ASSIGN_SECRET[-4:]}", out)


class RedactConservativeTest(unittest.TestCase):
    """Ordinary prose and low-confidence values are NEVER mangled.

    Documented choice: 'my password is hunter2' as user prose is left
    alone. It has no assignment operator and 'hunter2' is short and
    low-entropy — redacting it would mangle normal sentences ('the
    password is required', 'my password is hunter2 lol'). Redaction
    fires on assignment-shaped, high-entropy values only.
    """

    def test_prose_password_not_mangled(self):
        text = "my password is hunter2"
        self.assertEqual(redact_text(text), text)

    def test_short_assignment_values_not_mangled(self):
        for text in ("password=hunter2", "token: expired", "api_key=abc",
                     "secret=none"):
            self.assertEqual(redact_text(text), text, text)

    def test_bare_high_entropy_string_without_name_not_mangled(self):
        # A bare high-entropy blob with no api_key=/secret=/... name is
        # deliberately NOT redacted: without the assignment shape it could
        # be a hash, an ID, or test data. Documented conservative choice.
        blob = "9f2KxQvLmN8pQrT5wXyZ7aBcD4eFgH1iJkL"
        self.assertEqual(redact_text(f"checksum {blob}"), f"checksum {blob}")

    def test_plain_prose_untouched(self):
        text = ("The token expired yesterday so I generated a new one. "
                "The secret to good soup is patience.")
        self.assertEqual(redact_text(text), text)

    def test_non_string_values_pass_through(self):
        self.assertEqual(redact(42), 42)
        self.assertIsNone(redact(None))

    def test_field_name_key_is_not_redacted(self):
        # A dict {"api_key": "sk-..."} must keep the key name; only the
        # secret value is redacted (it matches the sk- pattern itself).
        out = redact({"api_key": OPENAI_KEY})
        self.assertIn("api_key", out)
        self.assertNotIn(OPENAI_KEY, out["api_key"])


class JournalRedactionTest(unittest.TestCase):
    """Journaled events persist with secrets redacted (tail preserved)."""

    def _record_and_read_raw(self, etype, data):
        home = tempfile.mkdtemp(prefix="awino-redact-test-")
        st = ProjectState(home, "p1")
        st.record(etype, data)
        raw = (Path(home) / "projects" / "p1" / "events.jsonl").read_text()
        return st, raw

    def _assert_all_redacted(self, raw, secrets, tails):
        for s in secrets:
            self.assertNotIn(s, raw, f"cleartext leaked: {s[:12]}...")
        for t in tails:
            self.assertIn(t, raw, f"tail missing: {t}")

    def test_each_pattern_class_in_journaled_event(self):
        secrets = [AWS_KEY, OPENAI_KEY, OPENROUTER_KEY, ANTHROPIC_KEY,
                   GITHUB_PAT, GITHUB_OAUTH, GITHUB_FG_PAT, BEARER_TOKEN,
                   SLACK_BOT, SLACK_USER]
        text = (" ".join(secrets).replace(BEARER_TOKEN, f"Bearer {BEARER_TOKEN}")
                + f" api_key={ASSIGN_SECRET}")
        st, raw = self._record_and_read_raw(
            "tool_result", {"call_id": "c1", "ok": True, "output": text})
        secrets.append(ASSIGN_SECRET)  # only the api_key= form is in text
        tails = ["AKIA...MPLE", "sk-...JkL6", "sk-or-v1-...iJkL",
                 "sk-ant-...oPqR", "ghp_...H1iJ", "gho_...H1iJ",
                 "github_pat_...L6mN", "Bearer ...QrT5",
                 "xoxb-...wXyZ", "xoxp-...wXyZ",
                 f"api_key=...{ASSIGN_SECRET[-4:]}"]
        self._assert_all_redacted(raw, secrets, tails)
        # In-memory event matches the persisted (redacted) form.
        self.assertEqual(st.events[-1]["data"]["output"],
                         json.loads(raw.strip().split("\n")[-1])["data"]["output"])

    def test_adversarial_tool_output_and_error_text(self):
        blob = (
            f"npm ERR! 401 Unauthorized - PUT https://registry.npmjs.org/x\n"
            f"npm ERR! auth token: {GITHUB_PAT}\n"
            f"Traceback (most recent call last):\n"
            f'  File "/app/main.py", line 9, in <module>\n'
            f"AuthError: invalid key {OPENAI_KEY}\n"
            f"config dump:\n  aws_access_key_id = {AWS_KEY}\n"
            f"  slack: {SLACK_BOT}\n"
        )
        st, raw = self._record_and_read_raw(
            "tool_result", {"call_id": "c1", "ok": False, "output": blob})
        for s in (GITHUB_PAT, OPENAI_KEY, AWS_KEY, SLACK_BOT):
            self.assertNotIn(s, raw)
        # Error text remains readable — structure preserved, tails kept.
        self.assertIn("AuthError", raw)
        self.assertIn("ghp_...H1iJ", raw)

    def test_adversarial_multiline_and_nested_values(self):
        data = {
            "args": {"env": {"OPENAI_API_KEY": OPENAI_KEY}},
            "nested": [{"deep": f"Bearer {BEARER_TOKEN}"}],
            "multiline": f"line1\npassword={ASSIGN_SECRET}\nline3",
        }
        st, raw = self._record_and_read_raw("tool_called", data)
        for s in (OPENAI_KEY, BEARER_TOKEN, ASSIGN_SECRET):
            self.assertNotIn(s, raw)
        self.assertIn(f"password=...{ASSIGN_SECRET[-4:]}", raw)
        # The caller's dict must not be mutated by redaction.
        self.assertEqual(data["args"]["env"]["OPENAI_API_KEY"], OPENAI_KEY)

    def test_prose_events_not_mangled(self):
        text = "my password is hunter2 and the token expired"
        st, raw = self._record_and_read_raw("user_message", {"text": text})
        self.assertIn(text, raw)

    def test_snapshot_persist_redacts(self):
        home = tempfile.mkdtemp(prefix="awino-redact-test-")
        st = ProjectState(home, "p1")
        # learnings are folded into the snapshot by apply_event, so this
        # exercises the snapshot.json persistence path.
        st.record("learning_recorded",
                  {"kind": "note",
                   "text": f"rotated the leaked key {OPENAI_KEY} today"})
        st.persist_snapshot()
        raw = (Path(home) / "projects" / "p1" / "snapshot.json").read_text()
        self.assertNotIn(OPENAI_KEY, raw)
        self.assertIn("sk-...JkL6", raw)


class SidecarEmitRedactionTest(unittest.TestCase):
    """The sidecar's log output path (_emit -> stdout) never carries
    cleartext secrets."""

    def _capture_emit(self, obj):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            sc._emit(obj)
        finally:
            sys.stdout = old
        return buf.getvalue()

    def test_emit_redacts_secrets(self):
        line = self._capture_emit(
            {"event": "error",
             "message": f"auth failed with key {OPENAI_KEY}"})
        self.assertNotIn(OPENAI_KEY, line)
        self.assertIn("sk-...JkL6", line)
        # Still valid JSON on the wire.
        json.loads(line)

    def test_emit_redacts_nested_event_payloads(self):
        line = self._capture_emit(
            {"event": "turn_result",
             "result": {"stderr": f"bad token {GITHUB_PAT}",
                        "headers": {"Authorization": f"Bearer {BEARER_TOKEN}"}}})
        self.assertNotIn(GITHUB_PAT, line)
        self.assertNotIn(BEARER_TOKEN, line)
        self.assertIn("ghp_...H1iJ", line)
        self.assertIn("Bearer ...QrT5", line)

    def test_emit_leaves_benign_payloads_byte_identical(self):
        obj = {"event": "ready", "protocol": 3, "note": "my password is hunter2"}
        line = self._capture_emit(obj)
        self.assertEqual(json.loads(line), obj)


class SidecarStderrRedactionTest(unittest.TestCase):
    """Tracebacks/diagnostics on stderr (captured into host logs) are
    redacted; benign text passes through byte-identical."""

    def test_stderr_wrapper_redacts(self):
        buf = io.StringIO()
        wrapped = sc._RedactingStderr(buf)
        wrapped.write(f"hello: failed with key {OPENAI_KEY}\n")
        wrapped.write("plain diagnostic line\n")
        out = buf.getvalue()
        self.assertNotIn(OPENAI_KEY, out)
        self.assertIn("sk-...JkL6", out)
        self.assertIn("plain diagnostic line\n", out)


if __name__ == "__main__":
    unittest.main()
