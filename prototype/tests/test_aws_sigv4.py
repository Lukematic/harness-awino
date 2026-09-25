"""AWS SigV4 (stdlib-only) + credential chain for the Bedrock provider.

Covers:
  - Signer cross-validation against botocore's SigV4Auth (scratch venv,
    test-only): GET with query params, POST with JSON body, custom
    headers, session-token case. The canonical request AND the final
    signature must match botocore EXACTLY.
  - Credential chain with a temp HOME: env vars win, profile from the
    shared-credentials file, region from config, source_profile chaining
    (STS AssumeRole), SSO cache valid token used, SSO expired/missing ->
    named errors, no credentials anywhere -> named error.
  - Fail-closed transport: with no usable credentials the backend
    constructor raises before any HTTP is attempted (urlopen mock records
    zero calls); with credentials, a local HTTP server proves the request
    carries a SigV4 Authorization header and no Bearer token.
  - hello-level: provider "bedrock" with no credentials fails closed at
    connect time with the named error (real sidecar subprocess).
"""
import datetime
import hashlib
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import awino_sidecar as s

SIDECAR = os.path.join(os.path.dirname(__file__), "..", "awino_sidecar.py")

# The well-known example credentials from the AWS SigV4 docs.
AK = "AKIAIOSFODNN7EXAMPLE"
SK = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
TOKEN = "AQoDYXdzEPT//////////wEa8AKlexampleTOKEN"

try:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials
    import botocore.auth as _boto_auth
    HAS_BOTOCORE = True
except ImportError:
    HAS_BOTOCORE = False


def _botocore_sign(method, url, headers, body, token=None):
    """Sign with botocore's SigV4Auth at a FIXED timestamp. Returns
    (final headers dict, canonical request string).

    Mirrors real botocore usage: add_auth runs on the unprepared
    AWSRequest (via the request-created event), before prepare()."""
    creds = Credentials(AK, SK, token)
    auth = SigV4Auth(creds, "bedrock", "us-east-1")
    ts = "20260925T120000Z"
    fixed = datetime.datetime.strptime(ts, "%Y%m%dT%H%M%SZ")

    def fresh():
        return AWSRequest(method=method, url=url, data=body,
                          headers=dict(headers))

    with mock.patch.object(_boto_auth, "get_current_datetime",
                           return_value=fixed):
        # Capture the canonical request exactly as botocore builds it.
        req = fresh()
        req.context["timestamp"] = ts
        auth._modify_request_before_signing(req)
        canon = auth.canonical_request(req)
        # Now the full add_auth for the final headers.
        req2 = fresh()
        auth.add_auth(req2)
    return dict(req2.headers), canon


@unittest.skipUnless(HAS_BOTOCORE, "botocore required (scratch venv)")
class SigV4CrossValidationTest(unittest.TestCase):
    """Our stdlib signer must agree with botocore byte-for-byte."""

    def _check(self, method, url, headers, body, token=None):
        mine = s.sigv4_sign(
            method, url, headers, body, access_key=AK, secret_key=SK,
            session_token=token, service="bedrock", region="us-east-1",
            timestamp="20260925T120000Z")
        theirs, their_canon = _botocore_sign(method, url, headers, body,
                                             token=token)
        # Same canonical request...
        my_signed_set = [(k, v) for k, v in mine.items()
                         if k.lower() != "authorization"]
        my_canon = s.sigv4_canonical_request(method, url, my_signed_set,
                                             body)
        self.assertEqual(my_canon, their_canon,
                         "canonical request differs from botocore")
        # ...same signature (Authorization, date, token headers).
        self.assertEqual(mine["Authorization"], theirs["Authorization"],
                         "Authorization header differs from botocore")
        self.assertEqual(mine["X-Amz-Date"], theirs["X-Amz-Date"])
        if token:
            self.assertEqual(mine["X-Amz-Security-Token"],
                             theirs["X-Amz-Security-Token"])
        else:
            self.assertNotIn("X-Amz-Security-Token", theirs)
            self.assertNotIn("X-Amz-Security-Token", mine)
        return mine

    def test_get_with_query_params(self):
        url = ("https://bedrock-runtime.us-east-1.amazonaws.com"
               "/openai/v1/models?b=2&a=1")
        self._check("GET", url, {}, None)

    def test_post_with_json_body(self):
        url = ("https://bedrock-runtime.us-east-1.amazonaws.com"
               "/openai/v1/chat/completions")
        body = (b'{"model":"anthropic.claude-3-5-sonnet-20240620-v1:0",'
                b'"messages":[{"role":"user","content":"hi"}]}')
        self._check("POST", url, {"Content-Type": "application/json"},
                    body)

    def test_custom_headers(self):
        url = ("https://bedrock-runtime.us-east-1.amazonaws.com"
               "/openai/v1/chat/completions")
        body = b'{"model":"x"}'
        headers = {
            "Content-Type": "application/json",
            "X-Custom-Header": "  hello   world  ",
            "x-MIXED-Case": "VaL",
        }
        self._check("POST", url, headers, body)

    def test_session_token(self):
        url = ("https://bedrock-runtime.us-east-1.amazonaws.com"
               "/openai/v1/chat/completions")
        body = b'{"model":"x"}'
        self._check("POST", url, {"Content-Type": "application/json"},
                    body, token=TOKEN)

    def test_empty_body_hashes_to_empty_sha256(self):
        canon = s.sigv4_canonical_request(
            "GET", "https://bedrock-runtime.us-east-1.amazonaws.com/", {},
            None)
        self.assertTrue(canon.endswith(hashlib.sha256(b"").hexdigest()))

    def test_sign_does_not_mutate_input_headers(self):
        headers = {"Content-Type": "application/json"}
        s.sigv4_sign("POST", "https://example.com/", headers, b"{}",
                     access_key=AK, secret_key=SK, service="bedrock",
                     region="us-east-1", timestamp="20260925T120000Z")
        self.assertEqual(headers, {"Content-Type": "application/json"})


_AWS_ENV_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                 "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
                 "AWS_REGION", "AWS_DEFAULT_REGION")


class TempHomeTest(unittest.TestCase):
    """Credential-chain tests run against a temp HOME (honored via the
    HOME env var); the real ~/.aws is never touched."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="awino-aws-test-"))
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self._saved = {k: os.environ.pop(k, None) for k in _AWS_ENV_KEYS}
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def write(self, relpath, text):
        p = self.home / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def assertNamedError(self, code, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except s.AWSAuthError as e:
            self.assertEqual(e.code, code, f"wrong error code: {e}")
            return e
        self.fail(f"expected AWSAuthError {code}, got success")


class CredentialChainTest(TempHomeTest):
    def test_env_vars_win_over_files(self):
        os.environ["AWS_ACCESS_KEY_ID"] = "ENVAK"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "ENVSK"
        os.environ["AWS_SESSION_TOKEN"] = "ENVTOK"
        self.write(".aws/credentials",
                   "[default]\naws_access_key_id = FILEAK\n"
                   "aws_secret_access_key = FILESK\n")
        creds = s.resolve_aws_credentials()
        self.assertEqual(
            (creds.access_key, creds.secret_key, creds.session_token),
            ("ENVAK", "ENVSK", "ENVTOK"))
        self.assertIn("environment", creds.source)

    def test_env_partial_is_named_error(self):
        os.environ["AWS_ACCESS_KEY_ID"] = "ONLYAK"
        e = self.assertNamedError("AWS_CREDENTIALS_INCOMPLETE",
                                 s.resolve_aws_credentials)
        self.assertIn("AWS_SECRET_ACCESS_KEY", str(e))

    def test_profile_from_credentials_file(self):
        os.environ["AWS_PROFILE"] = "work"
        self.write(".aws/credentials",
                   "[default]\naws_access_key_id = DEFAK\n"
                   "aws_secret_access_key = DEFSK\n"
                   "[work]\naws_access_key_id = WORKAK\n"
                   "aws_secret_access_key = WORKSK\n"
                   "aws_session_token = WORKTOK\n")
        creds = s.resolve_aws_credentials()
        self.assertEqual(
            (creds.access_key, creds.secret_key, creds.session_token),
            ("WORKAK", "WORKSK", "WORKTOK"))

    def test_explicit_profile_arg_beats_aws_profile_env(self):
        os.environ["AWS_PROFILE"] = "work"
        self.write(".aws/credentials",
                   "[work]\naws_access_key_id = WORKAK\n"
                   "aws_secret_access_key = WORKSK\n"
                   "[other]\naws_access_key_id = OTHERAK\n"
                   "aws_secret_access_key = OTHERSK\n")
        creds = s.resolve_aws_credentials(profile="other")
        self.assertEqual(creds.access_key, "OTHERAK")

    def test_region_from_config(self):
        self.write(".aws/credentials",
                   "[work]\naws_access_key_id = WORKAK\n"
                   "aws_secret_access_key = WORKSK\n")
        self.write(".aws/config",
                   "[profile work]\nregion = eu-west-1\n")
        creds = s.resolve_aws_credentials(profile="work")
        self.assertEqual(creds.region, "eu-west-1")

    def test_unknown_profile_is_named_error(self):
        self.write(".aws/credentials",
                   "[default]\naws_access_key_id = A\naws_secret_access_key = B\n")
        e = self.assertNamedError("AWS_PROFILE_NOT_FOUND",
                                 s.resolve_aws_credentials, profile="nope")
        self.assertIn("nope", str(e))

    def test_no_credentials_anywhere_is_named_error(self):
        e = self.assertNamedError("AWS_CREDENTIALS_NOT_FOUND",
                                 s.resolve_aws_credentials)
        self.assertIn("nothing to sign", str(e))

    def test_incomplete_credentials_file_section(self):
        self.write(".aws/credentials",
                   "[work]\naws_access_key_id = WORKAK\n")
        self.assertNamedError("AWS_CREDENTIALS_INCOMPLETE",
                             s.resolve_aws_credentials, profile="work")

    def test_source_profile_chaining_with_sts(self):
        self.write(".aws/credentials",
                   "[base]\naws_access_key_id = BASEAK\n"
                   "aws_secret_access_key = BASESK\n")
        self.write(".aws/config",
                   "[profile chained]\n"
                   "region = us-west-2\n"
                   "role_arn = arn:aws:iam::123456789012:role/ReadOnly\n"
                   "source_profile = base\n")
        sts_xml = (
            '<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
            "<AssumeRoleResult><Credentials>"
            "<AccessKeyId>STS_AK</AccessKeyId>"
            "<SecretAccessKey>STS_SK</SecretAccessKey>"
            "<SessionToken>STS_TOK</SessionToken>"
            "</Credentials></AssumeRoleResult></AssumeRoleResponse>").encode()

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return sts_xml

        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["headers"] = dict(req.header_items())
            return FakeResp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            creds = s.resolve_aws_credentials(profile="chained")
        self.assertEqual(
            (creds.access_key, creds.secret_key, creds.session_token),
            ("STS_AK", "STS_SK", "STS_TOK"))
        self.assertEqual(creds.region, "us-west-2")
        # The STS call itself was SigV4-signed with the base credentials.
        self.assertTrue(seen["url"].startswith("https://sts.us-west-2"))
        self.assertTrue(
            seen["headers"].get("Authorization", "").startswith(
                "AWS4-HMAC-SHA256 Credential=BASEAK/"))

    def test_source_profile_cycle_is_named_error(self):
        self.write(".aws/config",
                   "[profile a]\nrole_arn = arn:aws:iam::1:role/R\n"
                   "source_profile = b\n"
                   "[profile b]\nrole_arn = arn:aws:iam::1:role/R\n"
                   "source_profile = a\n")
        self.assertNamedError("AWS_PROFILE_CYCLE",
                             s.resolve_aws_credentials, profile="a")


class SsoChainTest(TempHomeTest):
    START_URL = "https://my-sso.awsapps.com/start"

    def _write_sso_profile(self):
        self.write(".aws/config",
                   "[profile sso]\n"
                   "region = us-east-1\n"
                   f"sso_start_url = {self.START_URL}\n"
                   "sso_account_id = 123456789012\n"
                   "sso_role_name = ReadOnly\n"
                   "sso_region = us-east-1\n")

    def _cache_path(self):
        digest = hashlib.sha1(self.START_URL.encode()).hexdigest()
        return self.write(".aws/sso/cache/" + digest + ".json", "")

    def test_sso_valid_token_used(self):
        self._write_sso_profile()
        future = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._cache_path().write_text(json.dumps({
            "startUrl": self.START_URL,
            "region": "us-east-1",
            "accessToken": "SSO_TOKEN",
            "expiresAt": future,
        }))
        portal_body = json.dumps({"roleCredentials": {
            "accessKeyId": "SSO_AK", "secretAccessKey": "SSO_SK",
            "sessionToken": "SSO_TOK",
            "expiration": 9999999999999}}).encode()

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return portal_body

        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["headers"] = dict(req.header_items())
            return FakeResp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            creds = s.resolve_aws_credentials(profile="sso")
        self.assertEqual(
            (creds.access_key, creds.secret_key, creds.session_token),
            ("SSO_AK", "SSO_SK", "SSO_TOK"))
        self.assertIn("SSO", creds.source)
        # Bearer token went to the SSO portal, never to Bedrock.
        self.assertIn("portal.sso.us-east-1.amazonaws.com", seen["url"])
        self.assertEqual(seen["headers"].get("X-amz-sso_bearer_token"),
                         "SSO_TOKEN")

    def test_sso_expired_token_is_named_error(self):
        self._write_sso_profile()
        past = (datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SUTC")
        self._cache_path().write_text(json.dumps({
            "startUrl": self.START_URL,
            "accessToken": "OLD",
            "expiresAt": past,
        }))
        with mock.patch("urllib.request.urlopen") as m:
            e = self.assertNamedError("AWS_SSO_TOKEN_EXPIRED",
                                     s.resolve_aws_credentials,
                                     profile="sso")
            m.assert_not_called()
        self.assertIn("aws sso login --profile sso", str(e))

    def test_sso_missing_cache_is_named_error(self):
        self._write_sso_profile()
        with mock.patch("urllib.request.urlopen") as m:
            e = self.assertNamedError("AWS_SSO_TOKEN_MISSING",
                                     s.resolve_aws_credentials,
                                     profile="sso")
            m.assert_not_called()
        self.assertIn("aws sso login --profile sso", str(e))

    def test_sso_does_not_fall_back_silently(self):
        # A static [default] exists, but the requested profile is the SSO
        # one: an expired SSO token must NOT fall through to it.
        self.write(".aws/credentials",
                   "[default]\naws_access_key_id = DEFAK\n"
                   "aws_secret_access_key = DEFSK\n")
        self._write_sso_profile()
        past = (datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._cache_path().write_text(json.dumps({
            "accessToken": "OLD", "expiresAt": past}))
        self.assertNamedError("AWS_SSO_TOKEN_EXPIRED",
                             s.resolve_aws_credentials, profile="sso")


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    seen = None  # class-level: last request observed

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _RecordingHandler.seen = {
            "path": self.path,
            "headers": {k.lower(): v
                        for k, v in self.headers.items()},
            "body": body,
        }
        payload = json.dumps({"choices": [{"message": {
            "content": "sigv4 says hi"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


class BackendTransportTest(TempHomeTest):
    def _serve(self):
        server = socketserver.TCPServer(("127.0.0.1", 0), _RecordingHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return port

    def test_no_credentials_no_unsigned_request_ever(self):
        with mock.patch("urllib.request.urlopen") as m:
            try:
                s.BedrockSigV4Backend(
                    model="m",
                    endpoint=("https://bedrock-runtime.us-east-1."
                              "amazonaws.com/openai/v1"))
            except s.AWSAuthError as e:
                self.assertEqual(e.code, "AWS_CREDENTIALS_NOT_FOUND")
            else:
                self.fail("expected AWSAuthError")
            # The constructor failed BEFORE any HTTP could be attempted.
            m.assert_not_called()

    def test_signed_request_reaches_bedrock(self):
        os.environ["AWS_ACCESS_KEY_ID"] = AK
        os.environ["AWS_SECRET_ACCESS_KEY"] = SK
        os.environ["AWS_SESSION_TOKEN"] = TOKEN
        port = self._serve()
        backend = s.BedrockSigV4Backend(
            model="m",
            endpoint=("https://bedrock-runtime.us-east-1."
                      "amazonaws.com/openai/v1"))
        # Point the already-constructed backend at the local server: the
        # signing inputs (host/region/service) stay Bedrock's; only the
        # TCP destination changes.
        backend.chat_url = f"http://127.0.0.1:{port}/openai/v1/chat/completions"
        _RecordingHandler.seen = None
        out = backend._chat("hello", "system")
        self.assertEqual(out, "sigv4 says hi")
        seen = _RecordingHandler.seen
        self.assertIsNotNone(seen)
        auth = seen["headers"].get("authorization", "")
        self.assertTrue(
            auth.startswith("AWS4-HMAC-SHA256 "),
            f"expected SigV4 Authorization, got: {auth[:60]}")
        self.assertIn("x-amz-date", seen["headers"])
        self.assertEqual(seen["headers"].get("x-amz-security-token"), TOKEN)
        self.assertNotIn("bearer", auth.lower())

    def test_api_key_never_sent_even_when_env_has_one(self):
        os.environ["AWS_ACCESS_KEY_ID"] = AK
        os.environ["AWS_SECRET_ACCESS_KEY"] = SK
        os.environ["AWINO_API_KEY"] = "SHOULD-NEVER-BE-SENT"
        port = self._serve()
        backend = s.BedrockSigV4Backend(
            model="m",
            endpoint=("https://bedrock-runtime.us-east-1."
                      "amazonaws.com/openai/v1"))
        backend.chat_url = f"http://127.0.0.1:{port}/openai/v1/chat/completions"
        _RecordingHandler.seen = None
        backend._chat("hello", "system")
        auth = _RecordingHandler.seen["headers"].get("authorization", "")
        self.assertNotIn("SHOULD-NEVER-BE-SENT", auth)
        self.assertTrue(auth.startswith("AWS4-HMAC-SHA256 "))

    def test_openai_backend_still_sends_bearer(self):
        # The _signed_headers refactor must not change the API-key path.
        backend = s.OpenAICompatibleBackend(
            model="m", endpoint="http://127.0.0.1:1", api_key="K")
        headers = backend._signed_headers(b"{}")
        self.assertEqual(headers["Authorization"], "Bearer K")


class HelloFailClosedTest(TempHomeTest):
    """provider 'bedrock' with no credentials fails at hello/connect time
    against the REAL sidecar subprocess — never a crash, never a session."""

    def _hello(self, **kw):
        ws = tempfile.mkdtemp(prefix="awino-sidecar-test-")
        self.addCleanup(shutil.rmtree, ws, True)
        env = dict(os.environ)
        for k in _AWS_ENV_KEYS:
            env.pop(k, None)
        env["HOME"] = str(self.home)
        env["AWINO_HOME"] = tempfile.mkdtemp(prefix="awino-home-test-")
        self.addCleanup(shutil.rmtree, env["AWINO_HOME"], True)
        p = subprocess.Popen(
            [sys.executable, SIDECAR], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        payload = {"cmd": "hello", "workspace": ws, "provider": "bedrock",
                   "endpoint": ("https://bedrock-runtime.us-east-1."
                                "amazonaws.com/openai/v1")}
        payload.update(kw)
        try:
            p.stdin.write((json.dumps(payload) + "\n").encode())
            p.stdin.flush()
            line = p.stdout.readline().decode()
            ev = json.loads(line)
        finally:
            # No session exists after a failed hello, so "bye" is a
            # no-op ("no session" early-return); EOF on stdin is what
            # stops the process.
            p.stdin.close()
            p.wait(timeout=15)
        return ev

    def test_hello_bedrock_no_credentials_errors(self):
        ev = self._hello()
        self.assertEqual(ev.get("event"), "error", ev)
        self.assertIn("AWS_CREDENTIALS_NOT_FOUND", ev.get("message", ""))

    def test_hello_bedrock_profile_succeeds_with_no_api_key(self):
        # A credentials file with a profile is enough: the sidecar
        # connects with SigV4, no API key anywhere in the environment.
        self.write(".aws/credentials",
                   "[default]\naws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
                   "aws_secret_access_key = "
                   "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n")
        self.write(".aws/config", "[profile default]\nregion = us-east-1\n")
        ev = self._hello(aws_profile="default")
        self.assertEqual(ev.get("event"), "ready", ev)
        binding = ev.get("binding", {})
        self.assertEqual(binding.get("provider"), "bedrock")
        self.assertEqual(binding.get("aws_profile"), "default")
        self.assertEqual(binding.get("key"), "sigv4-profile:default")


if __name__ == "__main__":
    unittest.main()
