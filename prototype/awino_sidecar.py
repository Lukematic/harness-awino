#!/usr/bin/env python3
"""A.W.I.N.O. sidecar: headless loop-owner for the VS Code extension.

The extension is a surface; THIS process owns the turn loop. Every
model-proposed action flows through the REAL harness pipeline
(`Loop.run_user_turn` -> contract, sensor, permission gate, validation,
judge, stance rubric, pre-execute check, approval gate). Nothing is
reimplemented here.

Transport: newline-delimited JSON on stdin (commands) / stdout (events).
Logging goes to stderr only; stdout is the protocol channel.

Fail-closed: malformed input, unknown commands, backend failures, and tool
errors become `error` events or safe fallback turns. This process never
crashes on input.

Standard library only.
"""
from __future__ import annotations

import collections
import datetime
import difflib
import email.utils
import fnmatch
import hashlib
import hmac
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backends
import loop as loop_module
from backends import EchoBackend, OllamaBackend, ScriptedBackend
from contract import MODES, compile_contract as _real_compile_contract
from contract import parse_criteria
from judges import build_judge_panel
from loop import Loop
from secret_redaction import redact, redact_text
from skills import SkillIntegrityError
from synthesis import (SynthesisRefused, admit_skill, open_registry,
                       run_checks, screen_learning, _parse_checks)
from tools import Sandbox, TOOL_DEFS

PROTOCOL = 1

# ---------------------------------------------------------------------------
# Sidecar-local tool/mode profile.
#
# Applied LAZILY by _apply_sidecar_tool_profile() when a session starts
# (hello) — never at import. Importing this module must not mutate the
# shared core globals, otherwise any in-process importer (tests, REPL
# tooling) would silently change awino chat / MCP server behavior. In
# production the sidecar is its own process and hello always runs before
# any Loop is created, so the profile is in force for every loop turn.
#
# - search_files: read-only grep, offered in every mode.
# - run_command: consequential here (approval-gated). An IDE agent running
#   shell commands on the user's machine with the user's privileges must ask
#   first; the core's verify-mode auto-run is a demo-sandbox behavior we do
#   not inherit.
# ---------------------------------------------------------------------------
_TOOL_PROFILE_APPLIED = False


def _apply_sidecar_tool_profile() -> None:
    """Apply the IDE tool profile to the process-global registries.

    Idempotent per process. Called from _do_hello before any Loop is
    created. Never call at import time.
    """
    global _TOOL_PROFILE_APPLIED
    if _TOOL_PROFILE_APPLIED:
        return
    TOOL_DEFS["search_files"] = {
        "consequential": False,
        "args": ["pattern", "path", "glob"],
    }
    TOOL_DEFS["run_command"] = {
        "consequential": True,  # sidecar deviation: approval-gated
        "args": ["cmd", "timeout"],
    }
    for _mode in MODES.values():
        if "search_files" not in _mode["tools"]:
            _mode["tools"].append("search_files")
    # The model must know about search_files; the contract block already
    # lists offered tools per mode, this keeps the turn system prompt
    # consistent.
    backends._OLLAMA_SYSTEM = backends._OLLAMA_SYSTEM.replace(
        'Available tools: read_file {"path"}, list_dir {} (takes no arguments), '
        'run_command {"cmd"}, write_file {"path", "content"} (consequential: '
        "propose only when a plan exists and was approved), patch_file "
        '{"path", "diff"} (consequential: unified diff applied atomically; '
        "same approval and SCOPE rules as write_file).",
        'Available tools: read_file {"path"}, list_dir {"path"} (relative dir, '
        '"" for root), search_files {"pattern" (regex), "path" (relative dir, '
        'optional), "glob" (filename glob, optional)}, run_command {"cmd"} '
        "(consequential: needs operator approval), write_file "
        '{"path", "content"} (consequential: needs operator approval; propose '
        "only when a plan exists and was approved), patch_file "
        '{"path", "diff"} (consequential: needs operator approval; unified '
        "diff applied atomically; same approval and SCOPE rules as write_file), "
        'set_mission {"text", "criteria" (semicolon-separated done criteria)} '
        "(interview: call this when the mission and done criteria are crisp "
        "\u2014 it records the mission and unblocks the floors).",
    )
    _TOOL_PROFILE_APPLIED = True


# ---------------------------------------------------------------------------
# Workspace tools
# ---------------------------------------------------------------------------
class WorkspaceSandbox(Sandbox):
    """Sandbox rooted at the opened workspace folder.

    Path traversal is refused by Sandbox._resolve. run_command's cwd is the
    workspace root and it runs with the user's privileges — which is why the
    sidecar marks it consequential (approval-gated).
    """

    def search_files(self, pattern: str, path: str = "",
                     glob: str = "*") -> dict:
        """Regex-search file contents under `path` (relative to workspace).

        Returns up to 50 hits: {file, line, text}. Binary/unreadable files
        are skipped. A bad regex is a tool error, not a crash.
        """
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return {"error": f"bad regex {pattern!r}: {e}"}
        try:
            base = self._resolve(path) if path else self.root
        except ValueError as e:
            return {"error": str(e)}
        if not base.is_dir():
            return {"error": f"not a directory: {path!r}"}
        hits: list[dict] = []
        scanned = 0
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            scanned += 1
            if scanned > 5000:
                break
            if not fnmatch.fnmatch(p.name, glob):
                continue
            try:
                text = p.read_text(errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append({"file": str(p.relative_to(self.root)),
                                 "line": i, "text": line[:200]})
                    if len(hits) >= 50:
                        return {"pattern": pattern, "hits": hits,
                                "truncated": True}
        return {"pattern": pattern, "hits": hits, "truncated": False}


# ---------------------------------------------------------------------------
# AWS SigV4 (stdlib-only) + AWS credential chain for the Bedrock provider.
#
# The Bedrock provider speaks the same OpenAI-compatible chat-completions
# protocol as OpenAICompatibleBackend, but authenticates with AWS Signature
# Version 4 derived from the user's AWS credential chain instead of a
# Bearer API key. No boto3/botocore: signing is implemented here with hmac,
# hashlib, email.utils and urllib only, and is cross-validated against
# botocore's SigV4Auth in tests/test_aws_sigv4.py (botocore lives in a
# scratch venv for that test ONLY — it is never imported here and never a
# dependency).
#
# Fail-closed contract:
# - resolve_aws_credentials() raises AWSAuthError (a NAMED error: stable
#   code + human message + the one next action) whenever a credential
#   source is present-but-unusable, and when no source yields credentials
#   at all. It never silently falls back and never returns half a
#   credential.
# - BedrockSigV4Backend resolves credentials EAGERLY in __init__: a hello
#   with provider "bedrock" and no usable credentials fails at connect
#   time, before the session exists.
# - _signed_headers() raises rather than returning unsigned headers, so
#   an unsigned Bedrock request can never be constructed, let alone sent.
# ---------------------------------------------------------------------------

_SIGV4_ALGORITHM = "AWS4-HMAC-SHA256"
_SIGV4_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
# Mirrors botocore.auth.SIGNED_HEADERS_BLACKLIST exactly.
_SIGV4_SIGNED_HEADERS_BLACKLIST = frozenset({
    "connection", "expect", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding",
    "upgrade", "user-agent", "x-amzn-trace-id",
})
# SigV4 service name for the bedrock-runtime host (what botocore uses for
# bedrock-runtime endpoints; the OpenAI-compatible path is just a path on
# that host).
_BEDROCK_SIGV4_SERVICE = "bedrock"


class AWSAuthError(Exception):
    """Fail-closed AWS credential / signing error.

    `code` is the stable machine-readable name (e.g. "AWS_SSO_TOKEN_EXPIRED");
    str(exc) is the human message plus the one next action. Raised instead
    of silently falling back; raised instead of sending an unsigned request.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _iter_sigv4_headers(headers) -> list:
    """Normalize a headers mapping or iterable of pairs to a list of
    (name, value) string tuples."""
    if hasattr(headers, "items"):
        items = list(headers.items())
    else:
        items = list(headers)
    return [(str(k), str(v)) for k, v in items]


def _sigv4_host_from_url(url: str) -> str:
    """Mirror botocore.auth._host_from_url: lowercase host, default port
    stripped, userinfo excluded."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    default_ports = {"http": 80, "https": 443}
    port = parts.port
    if port and port != default_ports.get(parts.scheme):
        host = f"{host}:{port}"
    return host


def _sigv4_remove_dot_segments(path: str) -> str:
    """Mirror botocore.utils.remove_dot_segments: RFC 3986 section 5.2.4
    plus AWS's consecutive-slash collapse."""
    if not path:
        return ""
    out: list[str] = []
    for seg in path.split("/"):
        if seg and seg != ".":
            if seg == "..":
                if out:
                    out.pop()
            else:
                out.append(seg)
    first = "/" if path[0] == "/" else ""
    last = "/" if path[-1] == "/" and out else ""
    return first + "/".join(out) + last


def _sigv4_canonical_uri(path: str) -> str:
    """Mirror botocore: quote(normalize_url_path(path), safe='/~')."""
    normalized = _sigv4_remove_dot_segments(path) or "/"
    return urllib.parse.quote(normalized, safe="/~")


def _sigv4_canonical_query_string(url: str) -> str:
    """Mirror botocore's URL-based canonical query string: split the raw
    (already-encoded) query on '&', sort the raw key/value pairs, rejoin."""
    query = urllib.parse.urlsplit(url).query
    if not query:
        return ""
    pairs = []
    for part in query.split("&"):
        k, _, v = part.partition("=")
        pairs.append((k, v))
    return "&".join(f"{k}={v}" for k, v in sorted(pairs))


def _sigv4_canonical_headers(headers) -> tuple:
    """Return (canonical block, signed-headers list).

    Mirrors botocore.auth.SigV4Auth.canonical_headers/signed_headers:
    lowercase names, trim + collapse interior whitespace in values
    (' '.join(value.split())), blacklist skipped, repeated names
    comma-joined, names sorted.
    """
    grouped: dict[str, list[str]] = {}
    for name, value in _iter_sigv4_headers(headers):
        lname = name.lower()
        if lname in _SIGV4_SIGNED_HEADERS_BLACKLIST:
            continue
        grouped.setdefault(lname, []).append(" ".join(value.split()))
    names = sorted(grouped)
    block = "\n".join(f"{n}:{','.join(grouped[n])}" for n in names)
    return block, ";".join(names)


def _sigv4_signing_key(secret_key: str, date_stamp: str,
                       region: str, service: str) -> bytes:
    """Mirror botocore's SigV4Auth.signature key derivation."""
    k_date = hmac.new(("AWS4" + secret_key).encode("utf-8"),
                      date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"),
                        hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"),
                         hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def sigv4_canonical_request(method: str, url: str, headers,
                            body: bytes | str | None) -> str:
    """Build the SigV4 canonical request for the given header set.

    `headers` must be the EXACT header set being signed (date/token headers
    already normalized, Authorization already removed). Mirrors
    botocore.auth.SigV4Auth.canonical_request byte-for-byte for the same
    inputs (asserted in tests/test_aws_sigv4.py). `body` may be bytes, str,
    or None (unsigned/empty payload hashes to the empty-string SHA256).
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    signed = _iter_sigv4_headers(headers)
    if not any(n.lower() == "host" for n, _ in signed):
        # botocore's headers_to_sign adds host from the URL when absent and
        # relies on the HTTP client to send it; same here.
        signed.append(("host", _sigv4_host_from_url(url)))
    canon_headers, signed_headers = _sigv4_canonical_headers(signed)
    payload_hash = (hashlib.sha256(body).hexdigest() if body
                    else _SIGV4_EMPTY_SHA256)
    return "\n".join([
        method.upper(),
        _sigv4_canonical_uri(urllib.parse.urlsplit(url).path),
        _sigv4_canonical_query_string(url),
        canon_headers + "\n",
        signed_headers,
        payload_hash,
    ])


def sigv4_sign(method: str, url: str, headers, body: bytes | str | None, *,
               access_key: str, secret_key: str, session_token: str | None = None,
               service: str, region: str, timestamp: str | None = None) -> dict:
    """Sign an HTTP request with AWS Signature Version 4.

    Returns a NEW dict of headers to send: the input headers plus X-Amz-Date
    (or a Date header when one was already present — mirroring botocore's
    _modify_request_before_signing), X-Amz-Security-Token when a session
    token is given, and the Authorization header. The input mapping is
    never mutated. `timestamp` is "%Y%m%dT%H%M%SZ" (defaults to now, UTC);
    tests pin it for exact cross-validation against botocore.
    """
    ts = timestamp or datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if isinstance(body, str):
        body = body.encode("utf-8")
    original = _iter_sigv4_headers(headers)
    had_date = any(k.lower() == "date" for k, _ in original)
    # Strip auth/date/token headers; they are re-added canonically below.
    kept = [(k, v) for k, v in original
            if k.lower() not in ("authorization", "x-amz-date",
                                 "x-amz-security-token", "date")]
    if had_date:
        # Mirror botocore: a pre-existing Date header is rewritten to the
        # signing timestamp (RFC 2822) and X-Amz-Date is NOT set.
        dt = datetime.datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime.timezone.utc)
        kept.append(("Date", email.utils.formatdate(dt.timestamp(),
                                                    usegmt=True)))
    else:
        kept.append(("X-Amz-Date", ts))
    if session_token:
        kept.append(("X-Amz-Security-Token", session_token))
    canon = sigv4_canonical_request(method, url, kept, body)
    date_stamp = ts[:8]
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        _SIGV4_ALGORITHM, ts, scope,
        hashlib.sha256(canon.encode("utf-8")).hexdigest(),
    ])
    signing_key = _sigv4_signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    _, signed_headers = _sigv4_canonical_headers(
        kept + [("host", _sigv4_host_from_url(url))])
    out = {k: v for k, v in kept}
    out["Authorization"] = (
        f"{_SIGV4_ALGORITHM} Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}")
    return out


# ------------------------------------------------- AWS credential chain

class AWSCredentials:
    """Resolved AWS credentials. `source` names the provenance
    ("environment variables", "~/.aws/credentials [work]", ...) — it never
    carries secret material."""

    def __init__(self, access_key: str, secret_key: str,
                 session_token: str | None = None,
                 region: str | None = None, source: str = ""):
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token
        self.region = region
        self.source = source


def _aws_home() -> Path:
    """Home directory for ~/.aws lookups. Honors the HOME env var so tests
    can point it at a temp dir (and so a relocated HOME just works)."""
    home = os.environ.get("HOME")
    if home:
        return Path(home)
    return Path.home()


def _aws_region_from_env() -> str | None:
    return (os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION") or None)


def _parse_aws_ini(text: str) -> dict:
    """Minimal INI parser for ~/.aws/credentials and ~/.aws/config.

    Understands [section] headers, key = value pairs, and '#' / ';'
    full-line comments. Malformed lines are ignored here — fail-closed
    happens at the credential-resolution layer, not in the parser.
    """
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if current is None:
            continue
        if "=" in line:
            k, _, v = line.partition("=")
        elif ":" in line:
            k, _, v = line.partition(":")
        else:
            continue
        sections[current][k.strip()] = v.strip()
    return sections


def _read_aws_ini_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise AWSAuthError(
            "AWS_CONFIG_UNREADABLE",
            f"Cannot read {path}: {e}. It means the AWS config is present "
            f"but unreadable — fix the file permissions and reconnect.")
    return _parse_aws_ini(text)


def _profile_region(config_sections: dict, name: str) -> str | None:
    key = "default" if name == "default" else f"profile {name}"
    section = config_sections.get(key)
    if section:
        region = (section.get("region") or "").strip()
        if region:
            return region
    return _aws_region_from_env()


def _sso_cache_path(home: Path, start_url: str) -> Path:
    digest = hashlib.sha1(start_url.encode("utf-8")).hexdigest()
    return home / ".aws" / "sso" / "cache" / (digest + ".json")


def _parse_sso_expires_at(raw) -> datetime.datetime | None:
    """Parse the SSO cache expiresAt. AWS CLI writes e.g.
    "2026-09-25T15:04:05UTC"; accept ISO-8601 variants too. None when
    unparseable (fail-closed at the caller)."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    try:
        if s.endswith("UTC"):
            return datetime.datetime.strptime(
                s[:-3], "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=datetime.timezone.utc)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        return None


def _sso_get_role_credentials(access_token: str, account_id: str,
                              role_name: str, sso_region: str,
                              profile_name: str) -> tuple:
    """Exchange a cached SSO access token for temporary IAM credentials via
    the SSO portal (GetRoleCredentials). Stdlib HTTPS only."""
    qs = urllib.parse.urlencode(
        {"account_id": account_id, "role_name": role_name})
    url = (f"https://portal.sso.{sso_region}.amazonaws.com"
           f"/federation/credentials?{qs}")
    req = urllib.request.Request(
        url, headers={"x-amz-sso_bearer_token": access_token})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise AWSAuthError(
            "AWS_SSO_ROLE_CREDENTIALS_FAILED",
            f"SSO GetRoleCredentials for profile {profile_name!r} failed "
            f"(HTTP {e.code}). It usually means the cached SSO token was "
            f"revoked. Run `aws sso login --profile {profile_name}` and "
            f"reconnect.")
    except OSError as e:
        raise AWSAuthError(
            "AWS_SSO_ROLE_CREDENTIALS_FAILED",
            f"Could not reach the AWS SSO portal for profile "
            f"{profile_name!r}: {e}. Check the network path to AWS, then "
            f"reconnect.")
    try:
        creds = payload["roleCredentials"]
        return (creds["accessKeyId"], creds["secretAccessKey"],
                creds.get("sessionToken"))
    except (KeyError, TypeError):
        raise AWSAuthError(
            "AWS_SSO_ROLE_CREDENTIALS_FAILED",
            f"The SSO portal answered for profile {profile_name!r} but the "
            f"response had no roleCredentials. Run "
            f"`aws sso login --profile {profile_name}` and reconnect.")


def _resolve_aws_sso(home: Path, cfg: dict, name: str) -> AWSCredentials:
    """Step (d): use the cached SSO token for an sso_start_url profile.
    Expired/missing cache fails closed with a named error telling the user
    to re-login — never falls through to another source."""
    start_url = cfg["sso_start_url"]
    cache_path = _sso_cache_path(home, start_url)
    relogin = (f"Run `aws sso login --profile {name}` and reconnect. The "
               f"sidecar will not fall back to another credential source.")
    try:
        raw = cache_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AWSAuthError(
            "AWS_SSO_TOKEN_MISSING",
            f"No cached AWS SSO token for profile {name!r} (looked for "
            f"{cache_path}). It means the SSO session was never started or "
            f"the cache was cleared. " + relogin)
    except OSError as e:
        raise AWSAuthError(
            "AWS_SSO_TOKEN_INVALID",
            f"Cannot read the SSO token cache at {cache_path}: {e}. " + relogin)
    try:
        data = json.loads(raw)
    except ValueError:
        raise AWSAuthError(
            "AWS_SSO_TOKEN_INVALID",
            f"The SSO token cache at {cache_path} is not valid JSON. "
            + relogin)
    token = data.get("accessToken")
    expires_at = _parse_sso_expires_at(data.get("expiresAt"))
    if not token:
        raise AWSAuthError(
            "AWS_SSO_TOKEN_INVALID",
            f"The SSO token cache at {cache_path} has no accessToken. "
            + relogin)
    if expires_at is None:
        raise AWSAuthError(
            "AWS_SSO_TOKEN_INVALID",
            f"The SSO token cache at {cache_path} has no parseable "
            f"expiresAt. " + relogin)
    now = datetime.datetime.now(datetime.timezone.utc)
    if expires_at <= now:
        raise AWSAuthError(
            "AWS_SSO_TOKEN_EXPIRED",
            f"The cached AWS SSO token for profile {name!r} expired at "
            f"{data.get('expiresAt')}. " + relogin)
    account_id = (cfg.get("sso_account_id") or "").strip()
    role_name = (cfg.get("sso_role_name") or "").strip()
    if not account_id or not role_name:
        missing = ("sso_account_id" if not account_id else "sso_role_name")
        raise AWSAuthError(
            "AWS_SSO_CONFIG_INCOMPLETE",
            f"~/.aws/config profile {name!r} has sso_start_url but is "
            f"missing {missing}. Add it under [profile {name}] and "
            f"reconnect.")
    sso_region = ((cfg.get("sso_region") or "").strip()
                  or _aws_region_from_env() or "us-east-1")
    ak, sk, tok = _sso_get_role_credentials(
        token, account_id, role_name, sso_region, name)
    return AWSCredentials(
        ak, sk, tok,
        region=((cfg.get("region") or "").strip()
                or _aws_region_from_env()),
        source=f"AWS SSO profile {name!r}")


def _sts_assume_role(base: AWSCredentials, role_arn: str, region: str,
                     profile_name: str) -> AWSCredentials:
    """AssumeRole via STS (stdlib HTTPS, SigV4-signed with the base
    credentials) for config profiles with role_arn + source_profile."""
    endpoint = f"https://sts.{region}.amazonaws.com/"
    body = urllib.parse.urlencode({
        "Action": "AssumeRole",
        "Version": "2011-06-15",
        "RoleArn": role_arn,
        "RoleSessionName": f"awino-{profile_name}"[:64],
    }).encode("utf-8")
    headers = sigv4_sign(
        "POST", endpoint,
        {"Content-Type": "application/x-www-form-urlencoded"}, body,
        access_key=base.access_key, secret_key=base.secret_key,
        session_token=base.session_token, service="sts", region=region)
    req = urllib.request.Request(endpoint, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise AWSAuthError(
            "AWS_ROLE_ASSUME_FAILED",
            f"STS AssumeRole for profile {profile_name!r} failed "
            f"(HTTP {e.code}). It means the source credentials lack "
            f"sts:AssumeRole permission for {role_arn}, or the role does "
            f"not trust this identity. Fix the IAM trust and reconnect.")
    except OSError as e:
        raise AWSAuthError(
            "AWS_ROLE_ASSUME_FAILED",
            f"Could not reach STS for profile {profile_name!r}: {e}. Check "
            f"the network path to AWS, then reconnect.")
    # STS answers in XML (namespace-qualified); pull the Credentials block
    # by local element name so the namespace never matters.
    try:
        import xml.etree.ElementTree as _et
        root = _et.fromstring(raw)
        creds_el = None
        for el in root.iter():
            if el.tag.rpartition("}")[2] == "Credentials":
                creds_el = el
                break
        if creds_el is None:
            raise ValueError("no Credentials element")
        vals = {}
        for el in creds_el:
            vals[el.tag.rpartition("}")[2]] = (el.text or "").strip()
        return AWSCredentials(
            vals["AccessKeyId"], vals["SecretAccessKey"],
            vals.get("SessionToken") or None, region,
            source=f"STS AssumeRole {role_arn} (profile {profile_name!r})")
    except (ValueError, KeyError, _et.ParseError):
        raise AWSAuthError(
            "AWS_ROLE_ASSUME_FAILED",
            f"STS answered for profile {profile_name!r} but the response "
            f"carried no credentials. Check the role configuration and "
            f"reconnect.")


def _resolve_aws_profile(home: Path, name: str, seen: tuple,
                         creds_sections: dict,
                         config_sections: dict) -> AWSCredentials:
    """Steps (b)->(c)->(d) for one profile name. `seen` guards
    source_profile cycles."""
    if name in seen:
        cycle = " -> ".join(seen + (name,))
        raise AWSAuthError(
            "AWS_PROFILE_CYCLE",
            f"AWS profile {name!r} loops back on itself through "
            f"source_profile ({cycle}). Break the cycle in ~/.aws/config "
            f"and reconnect.")
    seen = seen + (name,)
    # (b) shared-credentials file, profile-aware.
    section = creds_sections.get(name)
    if section is not None:
        ak = (section.get("aws_access_key_id") or "").strip()
        sk = (section.get("aws_secret_access_key") or "").strip()
        if ak and sk:
            tok = (section.get("aws_session_token") or "").strip() or None
            return AWSCredentials(
                ak, sk, tok, _profile_region(config_sections, name),
                source=f"~/.aws/credentials [{name}]")
        missing = ("aws_access_key_id" if not ak
                   else "aws_secret_access_key")
        raise AWSAuthError(
            "AWS_CREDENTIALS_INCOMPLETE",
            f"~/.aws/credentials has a [{name}] section but it is missing "
            f"{missing}. Complete the section or remove it, then reconnect.")
    # (c) config file: region + source_profile chaining.
    cfg_key = "default" if name == "default" else f"profile {name}"
    cfg = config_sections.get(cfg_key)
    if cfg is None:
        raise AWSAuthError(
            "AWS_PROFILE_NOT_FOUND",
            f"AWS profile {name!r} was not found in ~/.aws/credentials or "
            f"~/.aws/config. It means there is nothing to sign Bedrock "
            f"requests with. Create the profile (`aws configure --profile "
            f"{name}` or `aws sso login --profile {name}`), or pick a "
            f"profile that exists.")
    ak = (cfg.get("aws_access_key_id") or "").strip()
    sk = (cfg.get("aws_secret_access_key") or "").strip()
    if ak or sk:
        if not (ak and sk):
            missing = ("aws_access_key_id" if not ak
                       else "aws_secret_access_key")
            raise AWSAuthError(
                "AWS_CREDENTIALS_INCOMPLETE",
                f"~/.aws/config [{cfg_key}] is missing {missing}. Complete "
                f"the section or remove the half-written keys, then "
                f"reconnect.")
        tok = (cfg.get("aws_session_token") or "").strip() or None
        return AWSCredentials(
            ak, sk, tok, _profile_region(config_sections, name),
            source=f"~/.aws/config [{cfg_key}]")
    if (cfg.get("sso_start_url") or "").strip():
        return _resolve_aws_sso(home, cfg, name)
    role_arn = (cfg.get("role_arn") or "").strip()
    if role_arn:
        source_profile = (cfg.get("source_profile") or "").strip()
        credential_source = (cfg.get("credential_source") or "").strip().lower()
        if source_profile:
            base = _resolve_aws_profile(home, source_profile, seen,
                                        creds_sections, config_sections)
        elif credential_source == "environment":
            env_ak = os.environ.get("AWS_ACCESS_KEY_ID")
            env_sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
            if not (env_ak and env_sk):
                raise AWSAuthError(
                    "AWS_CREDENTIALS_INCOMPLETE",
                    f"AWS profile {name!r} uses credential_source = "
                    f"Environment, but AWS_ACCESS_KEY_ID / "
                    f"AWS_SECRET_ACCESS_KEY are not both set. Set them and "
                    f"reconnect.")
            base = AWSCredentials(
                env_ak, env_sk,
                os.environ.get("AWS_SESSION_TOKEN") or None,
                _aws_region_from_env(), source="environment variables")
        else:
            raise AWSAuthError(
                "AWS_ROLE_ASSUME_NO_SOURCE",
                f"AWS profile {name!r} sets role_arn but no source_profile "
                f"(or credential_source). Add `source_profile = <name>` "
                f"under [{cfg_key}] in ~/.aws/config and reconnect.")
        region = ((cfg.get("region") or "").strip() or base.region
                  or _aws_region_from_env() or "us-east-1")
        return _sts_assume_role(base, role_arn, region, name)
    raise AWSAuthError(
        "AWS_CREDENTIALS_NOT_FOUND",
        f"AWS profile {name!r} exists in ~/.aws/config but provides no "
        f"credentials (no aws_access_key_id, no sso_start_url, no "
        f"role_arn/source_profile). Add credentials to the profile and "
        f"reconnect.")


def resolve_aws_credentials(profile: str | None = None) -> AWSCredentials:
    """Resolve AWS credentials through the chain, fail-closed:

      (a) AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (+ AWS_SESSION_TOKEN)
      (b) ~/.aws/credentials, profile-aware
      (c) ~/.aws/config (profile region + source_profile chaining)
      (d) ~/.aws/sso/cache (valid cached SSO token -> GetRoleCredentials)

    Returns AWSCredentials, or raises AWSAuthError with a NAMED code at
    each failure — never silently falls back, never returns half a
    credential. Honors the HOME env var for ~/.aws lookups.
    """
    home = _aws_home()
    # (a) environment variables win over everything (AWS CLI behavior).
    ak = os.environ.get("AWS_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if ak or sk:
        if not (ak and sk):
            missing = ("AWS_SECRET_ACCESS_KEY" if ak
                       else "AWS_ACCESS_KEY_ID")
            raise AWSAuthError(
                "AWS_CREDENTIALS_INCOMPLETE",
                f"{missing} is not set while its pair is. It means the "
                f"environment holds half a credential — AWS would reject "
                f"every request. Set both AWS_ACCESS_KEY_ID and "
                f"AWS_SECRET_ACCESS_KEY (plus AWS_SESSION_TOKEN for "
                f"temporary credentials), or unset both to fall through to "
                f"~/.aws/credentials.")
        return AWSCredentials(
            ak, sk, os.environ.get("AWS_SESSION_TOKEN") or None,
            _aws_region_from_env(), source="environment variables")
    name = ((profile or "").strip()
            or os.environ.get("AWS_PROFILE")
            or os.environ.get("AWS_DEFAULT_PROFILE")
            or "default")
    creds_sections = _read_aws_ini_file(home / ".aws" / "credentials")
    config_sections = _read_aws_ini_file(home / ".aws" / "config")
    if not creds_sections and not config_sections:
        raise AWSAuthError(
            "AWS_CREDENTIALS_NOT_FOUND",
            f"No AWS credentials found: no AWS_ACCESS_KEY_ID / "
            f"AWS_SECRET_ACCESS_KEY in the environment, and no "
            f"~/.aws/credentials or ~/.aws/config under {home}. It means "
            f"there is nothing to sign Bedrock requests with. Log in "
            f"(`aws sso login --profile {name}` or `aws configure`), then "
            f"reconnect.")
    return _resolve_aws_profile(home, name, (), creds_sections,
                                config_sections)


def _bedrock_signing_region(endpoint: str | None,
                            creds: AWSCredentials) -> str:
    """Region for the SigV4 credential scope. The endpoint host is
    authoritative (the signature must match where the request goes);
    the profile/env region is the fallback."""
    if endpoint:
        m = re.search(r"bedrock-runtime\.([a-z0-9-]+)\.amazonaws\.com",
                      endpoint)
        if m:
            return m.group(1)
    if creds.region:
        return creds.region
    env_region = _aws_region_from_env()
    if env_region:
        return env_region
    raise AWSAuthError(
        "AWS_REGION_NOT_RESOLVED",
        "No AWS region for SigV4 signing: the endpoint URL carries no "
        "bedrock-runtime region, the AWS profile sets no region, and "
        "AWS_REGION / AWS_DEFAULT_REGION are unset. Set `region` for the "
        "profile in ~/.aws/config, or set AWS_REGION, and reconnect.")


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class OpenAICompatibleBackend(OllamaBackend):
    """Any OpenAI-compatible chat-completions endpoint (vLLM, llama.cpp
    server, OpenRouter, ...). API key from AWINO_API_KEY (env only) — some
    local endpoints need no key at all."""

    def __init__(self, model=None, endpoint=None, timeout=180,
                 num_predict=1024, api_key=None):
        self.model = (model or os.environ.get("AWINO_MODEL")
                      or os.environ.get("OLLAMA_MODEL") or "default")
        e = (endpoint or os.environ.get("AWINO_ENDPOINT")
             or "http://localhost:11434").rstrip("/")
        if e.endswith("/chat/completions"):
            self.chat_url = e
        elif e.endswith("/v1"):
            self.chat_url = e + "/chat/completions"
        else:
            self.chat_url = e + "/v1/chat/completions"
        self.host = e  # informational; _chat uses chat_url
        # api_key: explicit scoped binding wins; env is the legacy fallback.
        # Key material never leaves this attribute for logs/events/contract.
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("AWINO_API_KEY"))
        self.timeout = timeout
        self.num_predict = num_predict
        self.calls: list[dict] = []
        # Track C: consumed by Loop._record_egress -> journaled as an
        # `egress` event (destination, bytes). None when no HTTP happened.
        self.last_egress: dict | None = None

    def _signed_headers(self, body: bytes) -> dict:
        """Auth headers for one request. Subclasses override: the Bedrock
        SigV4 backend signs here instead of sending a Bearer key."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _chat(self, prompt: str, system: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": self.num_predict,
        }).encode()
        headers = self._signed_headers(body)
        req = urllib.request.Request(self.chat_url, data=body,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                payload = json.loads(raw.decode())
        except urllib.error.HTTPError as e:
            # 401/403/429/5xx -> generate() catches -> safe fallback turn.
            # The key (if any) is never included in the fallback or logs.
            raise RuntimeError(f"endpoint HTTP {e.code}")
        # Track C: report the network I/O so the loop can journal it.
        self.last_egress = {"destination": self.chat_url,
                            "bytes_out": len(body), "bytes_in": len(raw)}
        return payload["choices"][0]["message"]["content"]

    def _chat_stream(self, prompt: str, system: str):
        """SSE streaming over the OpenAI-compatible chat-completions
        endpoint ("stream": true). Yields ("said", delta) for each
        choices[0].delta.content; skips the terminal [DONE] line.

        The API key (if any) travels only in the Authorization header —
        it never appears in events, logs, or the journal."""
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "temperature": 0.2,
            "max_tokens": self.num_predict,
        }).encode()
        headers = self._signed_headers(body)
        req = urllib.request.Request(self.chat_url, data=body,
                                     headers=headers)
        # urlopen raising here (HTTP 401/403/429/5xx, unreachable) propagates
        # to generate() -> safe fallback turn, with no egress recorded.
        resp = urllib.request.urlopen(req, timeout=self.timeout)
        raw_in = 0
        try:
            with resp:
                for raw_line in resp:
                    raw_in += len(raw_line)
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue  # SSE comments / keep-alives
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    try:
                        delta = obj["choices"][0]["delta"]
                    except (KeyError, IndexError, TypeError):
                        continue
                    content = delta.get("content")
                    if content:
                        yield ("said", content)
        finally:
            # Track C: report the network I/O so the loop can journal it.
            self.last_egress = {"destination": self.chat_url,
                                "bytes_out": len(body), "bytes_in": raw_in}


class BedrockSigV4Backend(OpenAICompatibleBackend):
    """Bedrock through the OpenAI-compatible chat-completions endpoint,
    authenticated with AWS SigV4 from the user's AWS credential chain
    (env vars -> ~/.aws/credentials -> ~/.aws/config -> SSO token cache)
    instead of a Bearer API key.

    Fail-closed: credentials resolve EAGERLY in __init__ — a hello with
    provider "bedrock" and no usable credentials raises AWSAuthError
    (named) at connect time, before the session exists. _signed_headers
    raises rather than returning unsigned headers, so an unsigned Bedrock
    request can never be constructed, let alone sent. AWINO_API_KEY is
    deliberately ignored here: profile auth never silently falls back to
    a key.
    """

    def __init__(self, model=None, endpoint=None, timeout=180,
                 num_predict=1024, aws_profile=None):
        self.aws_profile = (aws_profile.strip() if isinstance(aws_profile, str)
                            and aws_profile.strip() else None)
        # Eager: connect fails here when the chain yields nothing usable.
        self.aws_credentials = resolve_aws_credentials(
            profile=self.aws_profile)
        self.aws_region = _bedrock_signing_region(endpoint,
                                                  self.aws_credentials)
        super().__init__(model=model, endpoint=endpoint, timeout=timeout,
                         num_predict=num_predict, api_key=None)
        # Belt and braces: even with AWINO_API_KEY in the environment,
        # profile auth never sends a Bearer token.
        self.api_key = None

    def _signed_headers(self, body: bytes) -> dict:
        creds = self.aws_credentials
        if (creds is None or not creds.access_key
                or not creds.secret_key):
            # Defense in depth: the constructor already raises. This path
            # must never produce an unsigned request.
            raise AWSAuthError(
                "AWS_CREDENTIALS_NOT_FOUND",
                "Refusing to sign a Bedrock request with no AWS "
                "credentials: no unsigned request will be sent. Reconnect "
                "after fixing the AWS credential chain.")
        return sigv4_sign(
            "POST", self.chat_url, {"Content-Type": "application/json"},
            body, access_key=creds.access_key,
            secret_key=creds.secret_key,
            session_token=creds.session_token,
            service=_BEDROCK_SIGV4_SERVICE, region=self.aws_region)


class AnthropicBackend(OllamaBackend):
    """Anthropic Messages API. Key from ANTHROPIC_API_KEY (env only)."""

    def __init__(self, model=None, endpoint=None, timeout=180,
                 num_predict=1024, api_key=None):
        self.model = (model or os.environ.get("AWINO_MODEL")
                      or "claude-sonnet-4-20250514")
        base = (endpoint or os.environ.get("AWINO_ENDPOINT")
                or "https://api.anthropic.com").rstrip("/")
        self.messages_url = base + "/v1/messages"
        self.host = base  # informational
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("ANTHROPIC_API_KEY"))
        self.timeout = timeout
        self.num_predict = num_predict
        self.calls: list[dict] = []
        # Track C: consumed by Loop._record_egress -> journaled as an
        # `egress` event (destination, bytes). None when no HTTP happened.
        self.last_egress: dict | None = None

    def _chat(self, prompt: str, system: str) -> str:
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.num_predict,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        headers = {"Content-Type": "application/json",
                   "x-api-key": self.api_key or "",
                   "anthropic-version": "2023-06-01"}
        req = urllib.request.Request(self.messages_url, data=body,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                payload = json.loads(raw.decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"anthropic HTTP {e.code}")
        # Track C: report the network I/O so the loop can journal it.
        self.last_egress = {"destination": self.messages_url,
                            "bytes_out": len(body), "bytes_in": len(raw)}
        return payload["content"][0]["text"]

    def _chat_stream(self, prompt: str, system: str):
        """SSE streaming over the Anthropic Messages API ("stream": true).

        Yields ("thinking", text) for content_block_delta events whose
        delta.type is "thinking_delta", and ("said", text) for "text_delta".
        redacted_thinking blocks yield nothing (thinking stays null) —
        thinking text is UI-only and never enters the turn dict or journal.

        The API key travels only in the x-api-key header — it never
        appears in events, logs, or the journal."""
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.num_predict,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }).encode()
        headers = {"Content-Type": "application/json",
                   "x-api-key": self.api_key or "",
                   "anthropic-version": "2023-06-01"}
        req = urllib.request.Request(self.messages_url, data=body,
                                     headers=headers)
        # urlopen raising here propagates to generate() -> safe fallback
        # turn, with no egress recorded.
        resp = urllib.request.urlopen(req, timeout=self.timeout)
        raw_in = 0
        try:
            with resp:
                for raw_line in resp:
                    raw_in += len(raw_line)
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if obj.get("type") != "content_block_delta":
                        continue
                    delta = obj.get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "thinking_delta":
                        text = delta.get("thinking")
                        if text:
                            yield ("thinking", text)
                    elif dtype == "text_delta":
                        text = delta.get("text")
                        if text:
                            yield ("said", text)
                    # signature_delta / redacted_thinking: yield nothing.
        finally:
            # Track C: report the network I/O so the loop can journal it.
            self.last_egress = {"destination": self.messages_url,
                                "bytes_out": len(body), "bytes_in": raw_in}


# ---------------------------------------------------------------------------
# Mission seeds (.awino/seeds/*.md)
#
# Reusable objective templates. Frontmatter:
#   ---
#   name: Bug fix
#   objective: Fix the reported bug with failing-test evidence
#   criteria:
#     - event:tool_result:run_command
#     - manual
#   ---
#   Optional notes...
# ---------------------------------------------------------------------------
_SEED_NAME_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{1,60}")
_CTX_NAME_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,60}")
_SKILL_NAME_RX = re.compile(r"[a-z0-9][a-z0-9_-]{1,40}")


def _parse_seed_file(path: Path) -> dict:
    """Parse a seed file. Never raises: problems are returned in "error"."""
    try:
        text = path.read_text()
    except OSError as e:
        return {"file": path.name, "error": f"unreadable: {e}"}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {"file": path.name, "error": "missing frontmatter (---)"}
    end = next((i for i in range(1, len(lines))
                if lines[i].strip() == "---"), None)
    if end is None:
        return {"file": path.name, "error": "unterminated frontmatter"}
    meta: dict = {}
    criteria: list[str] = []
    section = None
    for ln in lines[1:end]:
        if re.match(r"\s*-\s+", ln) and section == "criteria":
            criteria.append(re.sub(r"^\s*-\s+", "", ln).strip())
            continue
        m = re.match(r"([A-Za-z_]+):\s*(.*)$", ln)
        if m:
            section = m.group(1)
            meta[section] = m.group(2).strip()
        else:
            section = None
    meta["criteria"] = criteria
    out = {"file": path.name, "name": meta.get("name", ""),
           "objective": meta.get("objective", ""),
           "criteria": criteria,
           "notes": "\n".join(lines[end + 1:]).strip()}
    if not out["name"] or not out["objective"]:
        out["error"] = "seed needs 'name' and 'objective' in frontmatter"
        return out
    bad = []
    for c in criteria:
        try:
            parse_criteria(c)
        except ValueError as e:
            bad.append(str(e))
    if bad:
        out["error"] = "bad criteria: " + "; ".join(bad)
    return out


def _serialize_criteria(done_criteria: list[dict]) -> list[str]:
    lines = []
    for c in done_criteria:
        k = c.get("kind")
        if k == "manual":
            lines.append(f"manual:{c['label']}" if c.get("label") else "manual")
        elif k == "artifact_exists":
            lines.append(f"artifact:{c.get('path', '')}")
        elif k == "event":
            s = f"event:{c.get('event_type', '')}"
            if c.get("tool"):
                s += f":{c['tool']}"
            lines.append(s)
    return lines


# ---------------------------------------------------------------------------
# MCP client (this sidecar is the client; third-party servers are spawned
# over stdio). Hand-rolled JSON-RPC 2.0 with Content-Length framing, stdlib
# only. Mirrors the framing discipline of prototype/awino_mcp.py.
# ---------------------------------------------------------------------------
class McpClient:
    """A stdio MCP client. A dead or lying server fails closed per call and
    can never bypass the harness: its tools enter TOOL_DEFS/MODES like any
    other tool, so the contract, pre-execute check, and approval gate all
    apply."""

    def __init__(self, name: str, command: str, args: list,
                 env: dict | None):
        self.name = name
        self._id = 0
        self._id_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        merged = dict(os.environ)
        merged.update(env or {})
        # API keys may ride in env (operator-configured); they are never
        # logged and never appear in events.
        self.p = subprocess.Popen(
            [command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=merged)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        f = self.p.stdout  # binary pipe: lines are bytes
        while True:
            headers: dict[str, str] = {}
            while True:
                line = f.readline()
                if not line:
                    return
                try:
                    text = line.decode("utf-8", "replace").strip()
                except Exception:  # noqa: BLE001 - skip undecodable header
                    continue
                if not text:
                    break
                k, _, v = text.partition(":")
                headers[k.strip().lower()] = v.strip()
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                continue
            if length <= 0 or length > 10_000_000:
                continue
            body = f.read(length)
            try:
                msg = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                continue
            mid = msg.get("id") if isinstance(msg, dict) else None
            q = self._pending.get(mid)
            if q is not None:
                q.put(msg)
            # Server-initiated requests/notifications are ignored (v1).

    def request(self, method: str, params: dict | None = None,
                timeout: float = 60) -> dict:
        with self._id_lock:
            self._id += 1
            rid = self._id
        q: queue.Queue = queue.Queue()
        self._pending[rid] = q
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                               "params": params or {}}).encode()
            frame = b"Content-Length: %d\r\n\r\n" % len(body) + body
            self.p.stdin.write(frame)
            self.p.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"MCP server {self.name!r} unreachable: {e}")
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(
                f"MCP server {self.name!r} timed out on {method}")
        finally:
            self._pending.pop(rid, None)
        err = resp.get("error")
        if err:
            raise RuntimeError(f"MCP {method} error: {err}")
        return resp.get("result") or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        try:
            body = json.dumps({"jsonrpc": "2.0", "method": method,
                               "params": params or {}}).encode()
            self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body)
                               + body)
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def initialize(self) -> None:
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "awino-sidecar", "version": "0.1.0"},
        }, timeout=30)
        self.notify("notifications/initialized")

    def list_tools(self) -> list:
        return self.request("tools/list", timeout=30).get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict,
                  timeout: float = 120) -> dict:
        result = self.request("tools/call",
                              {"name": tool_name,
                               "arguments": arguments or {}},
                              timeout=timeout)
        texts = [c.get("text", "") for c in result.get("content", [])
                 if isinstance(c, dict) and c.get("type") == "text"]
        out: dict = {"output": "\n".join(texts)}
        if result.get("isError"):
            out["isError"] = True
        return out

    def close(self) -> None:
        try:
            self.p.terminate()
        except OSError:
            pass


def _make_mcp_handler(client: McpClient, server_name: str,
                      tool_name: str):
    def handler(**kwargs):
        try:
            return client.call_tool(tool_name, kwargs)
        except Exception as e:  # noqa: BLE001 - fail-closed per call
            return {"error": f"MCP {server_name}.{tool_name} failed: "
                             f"{type(e).__name__}: {e}"}
    return handler


# ---------------------------------------------------------------------------
# Contract augmentation: operator context + project skills.
#
# The harness renders the contract block from code-owned state; the sidecar
# appends two operator-owned sections at the END (the turn header — the
# first line — is untouched, so the position sensor is unaffected).
# Patched process-locally in _do_hello; the core prototype/ is unchanged.
# ---------------------------------------------------------------------------
_ACTIVE_SIDECAR = None
_COMPILE_PATCHED = False


def _patched_compile_contract(state, turn_no=1, knowledge=None,
                              round_no=None, round_context=None):
    # v0.6: forward the round kwargs — the recursive loop recompiles the
    # contract per round with a `loop: turn.round` header.
    block = _real_compile_contract(state, turn_no=turn_no,
                                    knowledge=knowledge, round_no=round_no,
                                    round_context=round_context)
    if _ACTIVE_SIDECAR is not None:
        # Persona is front-loaded (pinned tier, right after the header
        # sensor line, which must stay first): a lens on how to think.
        persona = _ACTIVE_SIDECAR._persona_section()
        if persona:
            head, _, rest = block.partition("\n")
            block = head + "\n\n" + persona + "\n" + rest
        extra = _ACTIVE_SIDECAR.sidecar_sections()
        if extra:
            block += "\n\n" + extra
    return block


# ---------------------------------------------------------------------------
# Scoped provider bindings, modes-as-data, skill-as-persona.
#
# Three Kilo-parity items, implemented A.W.I.N.O.-style:
#
# 1. SCOPED PROVIDER BINDINGS. Precedence:
#      user-global settings (hello provider/model/endpoint)
#    < project .awino/providers.yaml (its default_environment)
#    < explicitly named environment (hello "environment" / env_switch).
#    API keys live ONLY in VS Code SecretStorage. The extension injects them
#    into the sidecar's environment as AWINO_KEY_<KEY_ID>; the YAML file
#    carries only the opaque key_id. Key material never appears in files,
#    events, logs, or the contract block. Resolution is per-project: project
#    A's binding can never see project B's key, because each sidecar reads
#    only its own workspace's providers.yaml and only the env vars the
#    extension chose to inject. There is no ambient "current key".
#    The contract block and journal record provider IDENTITY (provider name
#    + model + environment, never the key) for auditability.
#
# 2. MODES AS DATA. A mode is pure data:
#      {id, label, stages (stage affinity), stance_prompt,
#       tool_policy (emphasis order, or None), sampling {temperature}}.
#    No new loop code paths: modes compose into the pinned contract block
#    every turn, so the model always knows its mode. The stage's offered
#    tool set is the enforcement boundary and is NEVER widened by a mode;
#    mode.tool_policy only reorders/emphasizes within it (anything outside
#    the stage's offered set is dropped in code, with a note).
#    Stage -> default-mode is automatic (the "automatic stage modes"
#    requirement); custom modes are user-invoked overlays (per mission,
#    pinned per project, or one-shot N turns). Manual overrides are
#    journal-logged and appear in the contract block, hence judge-visible.
#
# 3. SKILL-AS-PERSONA. An admitted (verified + SHA-256-pinned) skill can be
#    front-loaded into the contract block as a thinking lens for a bounded
#    scope (N turns, stage change, or explicit dismiss). INVARIANT: persona
#    is a LENS, not a LICENSE. It changes how the agent thinks, never what
#    it may do — approvals, tool gates, and the compiled contract still
#    bind. A persona that tries to widen the tool policy beyond the
#    contract is refused in code (structural: effective tools are always
#    intersected with the stage's offered set).
# ---------------------------------------------------------------------------

_KEY_ID_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_ENV_NAME_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_MODE_ID_RX = re.compile(r"[a-z0-9][a-z0-9_-]{1,40}")
_KNOWN_STAGES = ("DEFINE", "PLAN", "BUILD", "VERIFY", "REVIEW", "SHIP")
_KNOWN_PROVIDERS = ("echo", "ollama", "openai", "anthropic", "scripted",
                    "bedrock")
_BINDING_FIELDS = ("provider", "model", "endpoint", "key_id", "aws_profile")
# Per-environment settings that are NOT provider bindings (never affect keys).
_ENV_SETTING_FIELDS = ("context_window",)


def _strip_yaml_comment(line: str) -> str:
    """Remove a ' #' comment (a '#' preceded by whitespace starts one).
    A '#' inside a value (e.g. a URL fragment) is left alone."""
    out = []
    i = 0
    in_single = in_double = False
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and out and out[-1] in " \t":
            break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _parse_providers_yaml(text: str) -> dict:
    """Parse the strict YAML subset used by .awino/providers.yaml.

    Supported: nested mappings via space indentation, scalar string values,
    full-line and trailing ' #' comments. Anything else (lists, anchors,
    tabs, duplicate keys, non-mapping shapes) is a returned error, never an
    exception. Returns {"default_environment", "environments"} or {"error"}.
    Key MATERIAL is never valid here; only key_id references.
    """
    try:
        root: dict = {}
        stack: list[tuple[int, dict]] = [(-1, root)]
        seen_keys: dict[int, set[str]] = {-1: set()}
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = _strip_yaml_comment(raw)
            if not line.strip() or line.strip().startswith("#"):
                continue
            if "\t" in line:
                return {"error": f"line {lineno}: tabs are not allowed"}
            indent = len(line) - len(line.lstrip(" "))
            stripped = line.strip()
            if ":" not in stripped:
                return {"error": f"line {lineno}: expected 'key: value'"}
            key, _, value = stripped.partition(":")
            key, value = key.strip(), value.strip()
            if not key or " " in key:
                return {"error": f"line {lineno}: bad key {key!r}"}
            while stack and indent <= stack[-1][0]:
                stack.pop()
            if not stack:
                return {"error": f"line {lineno}: bad indentation"}
            parent = stack[-1][1]
            if key in parent:
                return {"error": f"line {lineno}: duplicate key {key!r}"}
            if value == "":
                child: dict = {}
                parent[key] = child
                stack.append((indent, child))
            else:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                parent[key] = value
        envs = root.get("environments", {})
        if not isinstance(envs, dict):
            return {"error": "'environments' must be a mapping"}
        for env_name, env in envs.items():
            if not _ENV_NAME_RX.fullmatch(env_name):
                return {"error": f"bad environment name {env_name!r}"}
            if not isinstance(env, dict):
                return {"error": f"environment {env_name!r} must be a mapping"}
            for field, val in env.items():
                if field not in _BINDING_FIELDS + _ENV_SETTING_FIELDS:
                    return {"error": f"environment {env_name!r}: "
                                     f"unknown field {field!r}"}
                if not isinstance(val, str):
                    return {"error": f"environment {env_name!r}: "
                                     f"field {field!r} must be a string"}
            cw = env.get("context_window")
            if cw and (not cw.isdigit() or not (512 <= int(cw) <= 2000000)):
                return {"error": f"environment {env_name!r}: "
                                 f"context_window must be 512..2000000"}
            prov = env.get("provider")
            if prov and prov not in _KNOWN_PROVIDERS:
                return {"error": f"environment {env_name!r}: "
                                 f"unknown provider {prov!r}"}
            key_id = env.get("key_id")
            if key_id and not _KEY_ID_RX.fullmatch(key_id):
                return {"error": f"environment {env_name!r}: "
                                 f"bad key_id {key_id!r}"}
        default_env = root.get("default_environment")
        if default_env is not None and default_env not in envs:
            return {"error": f"default_environment {default_env!r} "
                             f"is not a defined environment"}
        for top_key in root:
            if top_key not in ("default_environment", "environments"):
                return {"error": f"unknown top-level key {top_key!r}"}
        return {"default_environment": default_env, "environments": envs}
    except Exception as e:  # noqa: BLE001 - parser never raises
        return {"error": f"parse failure: {type(e).__name__}: {e}"}


def _key_env_name(key_id: str) -> str:
    """Env var the extension uses to inject a key's MATERIAL for key_id."""
    return "AWINO_KEY_" + re.sub(r"[^A-Z0-9_]", "_", key_id.upper())


def _lookup_key_material(key_id: str) -> tuple[str | None, str]:
    """Return (material, status). The material is used ONLY to construct the
    backend; it is never logged, emitted, or placed in the contract."""
    if not key_id:
        return None, "not-required"
    material = os.environ.get(_key_env_name(key_id))
    if material:
        return material, "configured"
    return None, "missing"


# --- Modes as data ---------------------------------------------------------
# stance_prompt: composed into the pinned contract block every turn.
# tool_policy: emphasis order within the stage's offered tools (None = no
#   guidance). Anything outside the stage's offered set is dropped in code.
# sampling.temperature: per-call override injected by _ModeAwareBackend.
# stages: stage affinity; the stage's DEFAULT is the first builtin whose
#   stages list contains the phase (None phase = no mission yet).
BUILTIN_MODES: list[dict] = [
    {"id": "interview", "label": "Interview",
     "stages": ["DEFINE"],
     "stance_prompt": (
         "Ask, don't act. Run the discovery interview: one question at a "
         "time, challenge vague answers, never let a plan start before the "
         "mission and done criteria are crisp. When the user's answers make "
         "the objective and done criteria crisp, call set_mission with the "
         "mission text and criteria \u2014 that is how a mission gets set. Do "
         "not keep asking once it is crisp."),
     "tool_policy": None, "sampling": {"temperature": 0.3}},
    {"id": "planner", "label": "Planner",
     "stages": ["DEFINE", "PLAN"],
     "stance_prompt": (
         "Design before doing. Produce a concrete, step-by-step plan with "
         "assumptions and open questions; propose no tool calls that change "
         "state. Prefer reading and searching to acting."),
     "tool_policy": ["read_file", "list_dir", "search_files"],
     "sampling": {"temperature": 0.2}},
    {"id": "architect", "label": "Architect",
     "stages": ["PLAN"],
     "stance_prompt": (
         "Read-only by default; design in documents. Explore the codebase, "
         "compare approaches, write the design down. Ask before any "
         "state-changing step."),
     "tool_policy": ["read_file", "list_dir", "search_files"],
     "sampling": {"temperature": 0.2}},
    {"id": "code", "label": "Code",
     "stages": ["BUILD"],
     "stance_prompt": (
         "Build it. Small, verified steps inside the approved scope; every "
         "claim of progress needs evidence from a tool result."),
     "tool_policy": None, "sampling": {"temperature": 0.2}},
    {"id": "debug-engineer", "label": "Debug Engineer",
     "stages": ["VERIFY"],
     "stance_prompt": (
         "Diagnose first, patch second. Reproduce the failure, form a "
         "hypothesis, test the smallest possible change, and show the "
         "before/after evidence. Never shotgun-debug."),
     "tool_policy": ["run_command", "read_file", "search_files"],
     "sampling": {"temperature": 0.2}},
    {"id": "test", "label": "Test",
     "stages": ["VERIFY"],
     "stance_prompt": (
         "Prove it. Write or run the checks that would falsify the work; "
         "report raw results, not summaries. A claim without a passing "
         "check is a draft, not a result."),
     "tool_policy": ["run_command", "read_file"],
     "sampling": {"temperature": 0.2}},
    {"id": "review", "label": "Review",
     "stages": ["REVIEW"],
     "stance_prompt": (
         "Adversarial reader. Hunt for the flaw the builder missed: wrong "
         "assumptions, untested paths, scope creep. Praise nothing without "
         "evidence."),
     "tool_policy": ["read_file", "search_files"],
     "sampling": {"temperature": 0.3}},
    {"id": "storyboard", "label": "Storyboard",
     "stages": [],
     "stance_prompt": (
         "Presentation builder. Work outline first, then visuals, then "
         "narrative: every slide earns its place with one clear point. "
         "Keep the mission's done criteria visible throughout."),
     "tool_policy": None, "sampling": {"temperature": 0.4}},
    {"id": "tutor", "label": "Tutor",
     "stages": [],
     "stance_prompt": (
         "Teacher, not servant. Ground every explanation in this project's "
         "journal, learnings, and decision history. Explicitly challenge "
         "the user's assumptions when they conflict with the evidence — "
         "the grill tenet of the discovery interview applies here too: a "
         "comfortable wrong answer is worse than an uncomfortable right "
         "question. Higher temperature is deliberate: explore the idea "
         "space, then converge on what the evidence supports."),
     "tool_policy": None, "sampling": {"temperature": 0.8}},
    {"id": "release", "label": "Release",
     "stages": ["SHIP"],
     "stance_prompt": (
         "Ship safely. Final checklist: done criteria all met, journal "
         "complete, nothing half-finished. Small, reversible steps; "
         "announce what shipped and what did not."),
     "tool_policy": None, "sampling": {"temperature": 0.2}},
]

_STAGE_DEFAULT_MODE = {
    "DEFINE": "interview",
    "PLAN": "architect",
    "BUILD": "code",
    "VERIFY": "test",
    "REVIEW": "review",
    "SHIP": "release",
}


def _validate_custom_mode(data: dict) -> dict:
    """Validate a user-defined mode (.awino/modes/*.json). Returns the mode
    or {"error"}. Modes are pure data: unknown fields are rejected."""
    if not isinstance(data, dict):
        return {"error": "mode file must be a JSON object"}
    allowed = {"id", "label", "stages", "stance_prompt", "tool_policy",
               "sampling"}
    unknown = set(data) - allowed
    if unknown:
        return {"error": f"unknown fields: {sorted(unknown)}"}
    mid = data.get("id")
    if not isinstance(mid, str) or not _MODE_ID_RX.fullmatch(mid):
        return {"error": f"bad mode id {mid!r}"}
    if mid in {m["id"] for m in BUILTIN_MODES}:
        return {"error": f"mode id {mid!r} shadows a built-in"}
    stages = data.get("stages", [])
    if not isinstance(stages, list) or any(s not in _KNOWN_STAGES for s in stages):
        return {"error": "stages must be a list of known stage names"}
    stance = data.get("stance_prompt")
    if not isinstance(stance, str) or not stance.strip() or len(stance) > 4000:
        return {"error": "stance_prompt must be non-empty text (<=4KB)"}
    policy = data.get("tool_policy")
    if policy is not None and (not isinstance(policy, list)
                               or not all(isinstance(t, str) for t in policy)):
        return {"error": "tool_policy must be a list of tool names or null"}
    sampling = data.get("sampling", {})
    if not isinstance(sampling, dict):
        return {"error": "sampling must be an object"}
    temp = sampling.get("temperature", 0.2)
    if not isinstance(temp, (int, float)) or not (0.0 <= temp <= 2.0):
        return {"error": "sampling.temperature must be a number in 0..2"}
    return {"id": mid,
            "label": data.get("label") or mid,
            "stages": stages,
            "stance_prompt": stance.strip(),
            "tool_policy": policy,
            "sampling": {"temperature": float(temp)},
            "custom": True}


def _mode_tool_emphasis(mode: dict | None, offered: list[str]) -> tuple[list[str], list[str]]:
    """Order offered tools by the mode's tool_policy emphasis.

    Returns (emphasis_first, dropped). The result NEVER exceeds the stage's
    offered set: policy entries outside it are dropped in code, with a note.
    Enforcement stays with the stage; the mode only emphasizes."""
    if not mode or not mode.get("tool_policy"):
        return list(offered), []
    emphasis, dropped = [], []
    for t in mode["tool_policy"]:
        if t in offered and t not in emphasis:
            emphasis.append(t)
        elif t not in offered and t not in dropped:
            dropped.append(t)
    rest = [t for t in offered if t not in emphasis]
    return emphasis + rest, dropped


def _persona_tool_priority(body: str, offered: list[str]) -> list[str]:
    """Tools the persona's skill body actually names, in order of first
    mention, intersected with the stage's offered set. Structural: the
    persona can never widen what the contract offers."""
    found = []
    for t in offered:
        if re.search(r"\b" + re.escape(t) + r"\b", body) and t not in found:
            found.append(t)
    return found[:8]


class _ModeAwareBackend:
    """Wraps the real backend to inject the active mode's sampling params
    per call. The Loop is untouched: it still calls generate(contract,
    history, feedback) exactly as before."""

    def __init__(self, inner, sidecar):
        self._inner = inner
        self._sidecar = sidecar

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def generate(self, contract_block, history, feedback=None,
                 stream_cb=None, tools=None, cancel=None, temperature=None):
        """stream_cb(kind, text): optional streaming sink for the sidecar
        protocol (spec §3.2–3.3). kind is "thinking" or "said". Default
        None preserves today's behavior exactly: the inner backend is
        called as before and no chunks are emitted.

        Backends that speak streaming define _chat_stream and accept
        stream_cb in generate(). Base fallback: a backend without
        _chat_stream (echo, hostile, plain scripted) runs its existing
        generate() untouched and the turn's progress_delta goes out as
        one ("said", ...) chunk — those providers keep working with zero
        changes to their code.

        v0.6: accepts and forwards the loop's per-round kwargs (tools,
        cancel, temperature) to the inner backend.
        """
        # v0.6: the loop may pass tools/cancel/temperature per round; the
        # sidecar's temperature takes precedence unless explicitly given.
        if temperature is None:
            temperature = self._sidecar._active_temperature()
        inner = self._inner
        # v0.6: only forward kwargs the inner backend actually accepts —
        # plain providers (echo/hostile/scripted) keep their old shape.
        import inspect as _inspect
        try:
            _params = _inspect.signature(inner.generate).parameters
            _accepts_kw = any(p.kind == _inspect.Parameter.VAR_KEYWORD
                              for p in _params.values())
        except (TypeError, ValueError):
            _params, _accepts_kw = {}, True
        _extra = {}
        if _accepts_kw or "tools" in _params:
            _extra["tools"] = tools
        if _accepts_kw or "cancel" in _params:
            _extra["cancel"] = cancel
        if stream_cb is None:
            return inner.generate(
                contract_block, history, feedback=feedback,
                temperature=temperature, **_extra)
        if hasattr(inner, "_chat_stream"):
            return inner.generate(
                contract_block, history, feedback=feedback,
                temperature=temperature, stream_cb=stream_cb, **_extra)
        turn = inner.generate(contract_block, history, feedback=feedback,
                              temperature=temperature, **_extra)
        said = (turn or {}).get("progress_delta") or ""
        if said:
            stream_cb("said", said)
        return turn


# ---------------------------------------------------------------------------
# Scope #5: housekeeping discipline + FAIR data.
#
# .awino/ is the project's FAIR data folder (Findable, Accessible,
# Interoperable, Reusable): plain JSON + Markdown only, every JSON file
# carries schema_version, every folder has a README index, and housekeeping
# never silently deletes — it archives with timestamps.
#
# Compaction is CHECKED WITH THE USER: when projected input tokens reach
# ~85% of the context window, the sidecar emits compaction_proposed (tier
# breakdown, what would be summarized, what is pinned and safe, token
# savings estimate) and pauses for approval via the normal approve/deny
# commands. compaction.auto_approve (per-project, .awino/config.json)
# defaults to FALSE. Denial continues without compacting; the proposal
# re-fires only as the window keeps filling.
# ---------------------------------------------------------------------------

_CONFIG_SCHEMA_VERSION = 1
_HOUSEKEEPING_SCHEMA_VERSION = 1
_CONTRACT_SNAPSHOT_SCHEMA_VERSION = 1
_COMPACTION_THRESHOLD = 0.85
_DEFAULT_CONTEXT_WINDOW = 32768


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (char//4), consistent with _charge_tokens."""
    return max(1, len(text or "") // 4)


def _utc_now() -> str:
    return datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_config() -> dict:
    return {
        "schema_version": _CONFIG_SCHEMA_VERSION,
        "compaction": {"auto_approve": False},
        "housekeeping": {"git_commit": False, "retention_days": 30},
    }


# Scope #5: providers.yaml template. YAML is the explicit plain-text
# exception to the JSON/Markdown FAIR rule — provider bindings are
# conventionally YAML, and the file carries no secrets (key material
# lives in VS Code SecretStorage / env vars, never here).
_PROVIDERS_YAML_TEMPLATE = """# .awino/providers.yaml — per-environment provider bindings.
# No secrets here: set *_API_KEY in your shell or VS Code SecretStorage.
# Each environment pins provider + model; the sidecar resolves
# default_environment, or AWINO_ENV, or the hello `environment` field.

default_environment: local

environments:
  local:
    provider: ollama
    model: llama3.2
    # base_url: http://localhost:11434
    # context_window: 32768

  # cloud:
  #   provider: anthropic
  #   model: claude-3-5-sonnet-20241022
  #   context_window: 200000
"""

_AWINO_README = """# .awino/ — A.W.I.N.O. project state

This folder is this project's mission memory. It follows FAIR data
principles: **Findable** (this index + manifests), **Accessible** (plain
JSON + Markdown, no binary blobs or proprietary formats),
**Interoperable** (every JSON file carries a `schema_version`),
**Reusable** (seeds and skills are portable files you can copy between
projects).

## Layout

- `contract.json` — snapshot of the current mission contract (mission,
  phase, done criteria, provider identity). Refreshed by housekeeping.
- `providers.yaml` — per-environment provider bindings. Carries only
  opaque `key_id` references; key MATERIAL lives only in VS Code
  SecretStorage and is never written here.
- `config.json` — per-project settings (`compaction.auto_approve`,
  `housekeeping.git_commit`, retention). All defaults are safe.
- `housekeeping.json` — manifest of housekeeping runs (what was done,
  what was archived). Nothing is ever silently deleted.
- `journal/` — exported journal entries (JSONL).
- `tool_results/` — offloaded large tool results (by id).
- `seeds/` — reusable mission templates (`*.md`).
- `context/` — operator-maintained context files.
- `skills/` — project skill registry overlay (admitted skills only).
- `modes/` — user-defined modes (pure data, JSON).
- `archive/` — timestamped retired files. Read-only history, never
  deleted by housekeeping.

Key material NEVER lives in this folder. If you see something that
looks like a secret here, treat it as an incident and rotate it.
"""

_FOLDER_READMES = {
    "journal": ("# journal/\n\nExported journal entries (`journal.jsonl`), "
                "newest export wins; superseded exports move to `../archive/` "
                "with timestamps. The authoritative journal is the harness "
                "event log; this folder is the FAIR findable copy.\n"),
    "tool_results": ("# tool_results/\n\nOffloaded large tool results, one "
                     "JSON file per result id (`<id>.json`, with "
                     "`schema_version`). Unreferenced files older than the "
                     "configured retention move to `../archive/` — never "
                     "silent deletion.\n"),
    "seeds": ("# seeds/\n\nReusable mission templates (`*.md` with "
              "frontmatter: name, objective, criteria). Portable: copy a "
              "file to another project's `seeds/` to reuse it.\n"),
    "context": ("# context/\n\nOperator-maintained context files, compiled "
                "into every contract block. `context.md` loads first, then "
                "`context/*.md` in `context.json` order.\n"),
    "skills": ("# skills/\n\nProject skill registry overlay: admitted "
               "(verified + SHA-256-pinned) skills only. Portable like "
               "seeds, but a copied skill must pass admission in the new "
               "project before use.\n"),
    "modes": ("# modes/\n\nUser-defined modes as pure data (`*.json`: id, "
              "label, stages, stance_prompt, tool_policy, sampling). "
              "Invalid files are ignored with a note, never loaded.\n"),
    "archive": ("# archive/\n\nRetired files with timestamps "
                "(`<UTC-timestamp>_<name>`). Read-only history: housekeeping "
                "writes here but never deletes from here.\n"),
}


def _emit(obj: dict) -> None:
    # Log/output boundary: stdout is the sidecar's protocol stream and the
    # extension host logs it. High-confidence secrets are redacted here so
    # they never persist in cleartext in host logs. Key material is never
    # in these payloads anyway (resolved server-side via _lookup_key_material),
    # so redaction cannot break provider auth.
    sys.stdout.write(json.dumps(redact(obj), default=str) + "\n")
    sys.stdout.flush()


def _err(message: str) -> None:
    # Protocol/stream errors are non-fatal: the process is alive, only the
    # command failed. A dead process is reported by the client (fatal: true).
    _emit({"event": "error", "message": message, "fatal": False})


class _RedactingStderr:
    """Write-through stderr wrapper: high-confidence secrets are redacted
    from crash tracebacks and diagnostic prints, which the extension host
    captures into its logs. Non-secret text passes through byte-identical;
    all other attributes delegate to the wrapped stream."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, s):
        return self._wrapped.write(
            redact_text(s) if isinstance(s, str) else s)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


_DIFF_MAX_LINES = 200


def _write_diff(sandbox: WorkspaceSandbox, path: str,
                new_content: str) -> tuple[str, bool]:
    """Unified diff old -> new for an approval preview. Never raises."""
    try:
        old = sandbox.read_file(path).get("content")
    except Exception:  # noqa: BLE001 - diffs must not break approvals
        old = None
    old_lines = old.splitlines() if isinstance(old, str) else []
    new_lines = (new_content or "").splitlines()
    diff = "\n".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"before: {path}", tofile=f"after: {path}"))
    lines = diff.splitlines()
    if len(lines) > _DIFF_MAX_LINES:
        diff = "\n".join(lines[:_DIFF_MAX_LINES]) + "\n... (truncated)"
    return diff, old is not None


# ------------------------------------------------- native tool application
class ExtensionDelegation:
    """Implements the Loop.delegation interface via the extension RPC.

    The loop keeps ownership of approval, scope epochs, idempotency,
    journaling, and contracts; this adapter only changes WHERE effects
    happen:

    - write_file/patch_file: ONE apply_requested event per drain carrying
      every granted write; the extension applies them in a single
      WorkspaceEdit (one undo unit) and replies with apply_result.
    - run_command: ONE terminal_requested event; the extension runs it in
      the integrated terminal via shell integration, streams
      terminal_output chunks, and replies with terminal_result.

    Protocol (sidecar -> extension are events on stdout; extension ->
    sidecar are commands on stdin, matched by request_id):

      {"event": "apply_requested", "request_id", "changeset_id",
       "edits": [{"call_id", "idem_key", "tool", "path", "content",
                  "old_digest" (sha256 of pre-image | null)}]}
      {"cmd": "apply_result", "request_id",
       "results": [{"call_id", "ok",
                    "result": {"path", "digest" (sha256), "bytes"} | "error"}]}
      {"event": "terminal_requested", "request_id", "cmd", "cwd", "timeout_s"}
      {"cmd": "terminal_output", "request_id", "data": chunk}
      {"cmd": "terminal_result", "request_id", "exit_code",
       "timed_out": bool, "killed": bool}
      {"event": "terminal_kill", "request_id"}   (sidecar -> extension, on cancel)

    The dispatch thread blocks in Sidecar._wait_for_extension while a
    delegated effect runs; unrelated commands are deferred to the normal
    queue, never lost. A dead/mute extension surfaces as error data
    (delegated_apply_failed / timed_out), never a hang or a crash.
    """

    APPLY_TIMEOUT = 120.0
    TERMINAL_TIMEOUT_S = 600.0
    TERMINAL_SLACK = 30.0

    def __init__(self, sidecar: "Sidecar"):
        self.sidecar = sidecar

    # -- Loop.delegation interface --------------------------------------
    def tool_fn(self, tool_name: str):
        if tool_name == "run_command":
            return self.run_terminal
        if tool_name in ("write_file", "patch_file"):
            return self._apply_single
        return None

    def _apply_single(self, path: str = "", content: str = "",
                      diff: str = "", **_kw) -> dict:
        """Fail-safe per-call apply: only reachable if a write ever reaches
        _execute_single directly instead of the drain batch (writes are
        approval-gated, so the drain owns them today). Routes through the
        same single-edit batch so checkpointing still applies."""
        loop = self.sidecar.loop
        tool = "write_file" if diff == "" else "patch_file"
        args = {"path": path}
        if tool == "write_file":
            args["content"] = content
        else:
            args["diff"] = diff
        edit = loop._compute_delegated_edit(tool, args)
        if edit.get("error"):
            return {"error": edit["error"],
                    "error_code": edit.get("error_code")}
        edit["call_id"] = f"single-{self.sidecar._next_req_id()}"
        edit["idem_key"] = edit["call_id"]
        results = self.apply_batch([edit])
        br = results[0] if results else {}
        if not isinstance(br, dict) or not br.get("ok"):
            return {"error": str((br or {}).get("error", "no result")),
                    "error_code": "delegated_apply_failed"}
        return br.get("result") or {}

    def apply_batch(self, edits: list[dict]) -> list[dict]:
        """One extension round-trip for the whole drain's writes."""
        sc = self.sidecar
        if not edits:
            return []
        req_id = sc._next_req_id()
        changeset_id = f"batch-{req_id}"
        sc._ensure_checkpoint(changeset_id, edits)
        wire = [{"call_id": e["call_id"], "idem_key": e["idem_key"],
                 "tool": e["tool"], "path": e["path"],
                 "content": e["content"], "old_digest": e["old_digest"],
                 "digest": e["digest"]}
                for e in edits]
        _emit({"event": "apply_requested", "request_id": req_id,
               "changeset_id": changeset_id, "edits": wire})
        try:
            resp = sc._wait_for_extension("apply_result", req_id,
                                          self.APPLY_TIMEOUT)
        except (TimeoutError, EOFError) as ex:
            return [{"call_id": e["call_id"], "ok": False,
                     "error": f"extension did not answer apply_requested: "
                              f"{type(ex).__name__}: {ex}"}
                    for e in edits]
        results = resp.get("results")
        if not isinstance(results, list):
            return [{"call_id": e["call_id"], "ok": False,
                     "error": "malformed apply_result from extension"}
                    for e in edits]
        return results

    def run_terminal(self, cmd: str = "", **_kw) -> dict:
        """Run a shell command in the extension's integrated terminal with
        live output streaming. The tool-result shape mirrors the sandbox's
        run_command (cmd/exit_code/stdout/stderr); the terminal merges
        stderr into the stream, so stderr is empty and the tail of the
        merged output lands in stdout."""
        sc = self.sidecar
        req_id = sc._next_req_id()
        timeout_s = self.TERMINAL_TIMEOUT_S
        cwd = str(sc.loop.sandbox.root) if sc.loop is not None else ""
        _emit({"event": "terminal_requested", "request_id": req_id,
               "cmd": cmd, "cwd": cwd, "timeout_s": timeout_s})
        chunks: list[str] = []

        def _on_other(other: dict) -> bool:
            if not isinstance(other, dict):
                return False
            name = other.get("cmd")
            if name == "terminal_output":
                # Stream data is consumed here, never deferred: it is not
                # a command and must not reach dispatch.
                if str(other.get("request_id")) == req_id:
                    data = other.get("data")
                    if isinstance(data, str):
                        chunks.append(data)
                return True
            if name == "terminal_result" and \
                    str(other.get("request_id")) != req_id:
                # Stale result for another request: consume, don't poison.
                return True
            if name == "cancel":
                # The operator cancelled a long-running command: ask the
                # extension to kill the terminal, then keep waiting for
                # terminal_result so the run always journals a final
                # state. The cancel itself stays deferred for the normal
                # cancel handling once the wait ends.
                _emit({"event": "terminal_kill", "request_id": req_id})
                return False
            return False

        try:
            resp = sc._wait_for_extension(
                "terminal_result", req_id, timeout_s + self.TERMINAL_SLACK,
                on_other=_on_other)
        except (TimeoutError, EOFError) as ex:
            return {"cmd": cmd, "exit_code": None,
                    "stdout": "".join(chunks)[-4000:], "stderr": "",
                    "timed_out": True,
                    "reason": "extension_timeout",
                    "error": f"extension did not answer terminal_requested: "
                             f"{type(ex).__name__}: {ex}"}
        out = "".join(chunks)
        return {"cmd": cmd, "exit_code": resp.get("exit_code"),
                "stdout": out[-4000:], "stderr": "",
                "timed_out": bool(resp.get("timed_out")),
                "killed": bool(resp.get("killed")),
                "reason": resp.get("reason") or "completed"}


class Sidecar:
    def __init__(self):
        self.inbox: queue.Queue = queue.Queue()   # parsed stdin commands
        self.deferred: collections.deque = collections.deque()
        self.loop: Loop | None = None
        self.workspace: Path | None = None
        self.provider = "echo"
        self.model_desc = ""
        self.alive = True
        self._cancel = threading.Event()
        # v0.6: per-turn cooperative cancellation. The token is created
        # fresh for each user turn, handed to Loop.run_user_turn, and set
        # by the stdin pump when a cancel command arrives mid-turn. The
        # loop polls it at round checkpoints and inside run_command.
        from cancel import CancelToken
        self._turn_token: CancelToken | None = None
        self._worker: threading.Thread | None = None
        self._turn_out: queue.Queue = queue.Queue()
        self._pending_says: list = []  # per-command _say() buffer
        # RISK 1: hello-time registry re-attach failure, if any. Recorded
        # (never swallowed) so tasks_list can report it honestly.
        self._registry_attach_error: str | None = None
        self._mcp_clients: list[McpClient] = []
        self._mcp_status: list[dict] = []
        # Scope #4 state: provider binding, mode overlay, skill persona.
        self._binding: dict | None = None      # resolved provider binding
        self._mode_overlay: dict | None = None  # custom-mode overlay
        self._custom_modes: list[dict] = []     # user modes (.awino/modes/)
        self._invalid_modes: list[dict] = []
        self._persona: dict | None = None      # active skill-as-persona
        # Scope #5 state: compaction + housekeeping.
        self._context_window: int = 32768     # tokens; hello/providers.yaml
        self._pending_compaction: dict | None = None
        self._last_proposal_tokens: int = 0   # re-propose only on growth
        self._last_housekept_phase: str | None = None
        # Native tool application (extension builder): extension RPC state.
        self._req_seq: int = 0                # delegated request ids
        self._checkpoints: dict = {}          # changeset_id -> checkpoint
        self._last_checkpoint_id: str | None = None
        self._delegated: bool = False         # hello capability negotiated

    # ------------------------------------------------------------ main loop
    def run(self) -> None:
        reader = threading.Thread(target=self._read_stdin, daemon=True)
        reader.start()
        while self.alive:
            cmd = self._next_command()
            if cmd is None:  # EOF
                break
            self._dispatch(cmd)

    def _next_command(self):
        if self.deferred:
            return self.deferred.popleft()
        return self.inbox.get()  # blocks; None sentinel on EOF

    # ------------------------------------------- native tool application
    def _next_req_id(self) -> str:
        self._req_seq += 1
        return f"ext-{self._req_seq}"

    def _wait_for_extension(self, cmd_name: str, request_id: str,
                            timeout: float, on_other=None) -> dict:
        """Block the dispatch thread until the extension answers a delegated
        request. Unrelated commands are deferred to the normal dispatch
        queue (never lost). on_other(cmd) sees each non-matching command
        first; returning True consumes it (stream data), False/None defers
        it. Raises TimeoutError / EOFError — callers turn these into error
        data, never a crash."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"timed out after {timeout:.0f}s waiting for "
                    f"{cmd_name} (request {request_id})")
            try:
                cmd = self.inbox.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(
                    f"timed out after {timeout:.0f}s waiting for "
                    f"{cmd_name} (request {request_id})")
            if cmd is None:  # stdin EOF: the extension is gone
                raise EOFError("stdin closed while waiting for extension")
            if (isinstance(cmd, dict) and cmd.get("cmd") == cmd_name
                    and str(cmd.get("request_id")) == request_id):
                return cmd
            consumed = False
            if on_other is not None:
                try:
                    consumed = bool(on_other(cmd))
                except Exception:  # noqa: BLE001 - never break the wait
                    traceback.print_exc(file=sys.stderr)
            if not consumed:
                self.deferred.append(cmd)

    def _ensure_checkpoint(self, changeset_id: str,
                           edits: list[dict]) -> None:
        """Git-based checkpoint before delegated writes: stash (including
        untracked) the workspace so a build-mode write set can be reverted
        via the revert_checkpoint command. Records the checkpoint in the
        journal. Non-git workspaces, clean trees, and failures record
        honestly and never block the write."""
        if changeset_id in self._checkpoints or self.workspace is None:
            return
        cp: dict = {"id": changeset_id, "type": "none",
                    "at": datetime.datetime.now(
                        datetime.timezone.utc).isoformat()}
        try:
            ws = str(self.workspace)
            r = subprocess.run(["git", "-C", ws, "rev-parse", "--git-dir"],
                               capture_output=True, timeout=15)
            if r.returncode != 0:
                cp["note"] = "not a git repository"
            else:
                # Files Awino is about to create (no pre-image at
                # checkpoint time): revert deletes exactly these, never
                # anything the operator created.
                new_files = [e["path"] for e in edits
                             if e.get("old_digest") is None]
                # The sidecar's own .awino/ state directory is excluded from
                # the checkpoint — it is not user workspace content, and
                # stashing it causes "already exists" conflicts on revert
                # (the sidecar recreates its state while running).
                r = subprocess.run(
                    ["git", "-C", ws, "stash", "push", "-u", "-m",
                     f"awino-checkpoint:{changeset_id}",
                     "--", ".", ":!.awino"],
                    capture_output=True, text=True, timeout=60)
                if r.returncode == 0:
                    # Locate our stash entry by message (robust against
                    # concurrent stashes from other tools).
                    ref = None
                    lr = subprocess.run(
                        ["git", "-C", ws, "stash", "list"],
                        capture_output=True, text=True, timeout=15)
                    for line in lr.stdout.splitlines():
                        if f"awino-checkpoint:{changeset_id}" in line:
                            ref = line.split(":")[0].strip()
                            break
                    cp.update({"type": "stash", "ref": ref or "stash@{0}",
                               "new_files": new_files})
                elif "No local changes" in (r.stderr or ""):
                    cp.update({"type": "clean", "new_files": new_files})
                else:
                    cp["note"] = (r.stderr or r.stdout or "")[:200]
        except Exception as ex:  # noqa: BLE001 - checkpoint never blocks
            cp["note"] = f"{type(ex).__name__}: {ex}"
        self._checkpoints[changeset_id] = cp
        self._last_checkpoint_id = changeset_id
        try:
            self.loop.state.record("checkpoint_created", {
                "changeset_id": changeset_id, "type": cp["type"],
                "note": cp.get("note", ""),
                "mission_rev": self.loop.state.snapshot["mission_revision"]})
        except Exception:  # noqa: BLE001 - never break the write path
            pass

    def _do_revert_checkpoint(self, args: dict) -> dict:
        """revert_checkpoint command (awino.revertCheckpoint): restore the
        workspace to the last (or named) checkpoint. Surgical: tracked
        files go to HEAD, ONLY Awino-created untracked files are deleted,
        then the pre-checkpoint state (including the operator's own
        uncommitted changes) is restored from the stash."""
        cid = args.get("changeset_id") or self._last_checkpoint_id
        cp = self._checkpoints.get(cid) if cid else None
        if not cp:
            return {"status": "error",
                    "error": f"no checkpoint {cid or '(none)'}; "
                             f"nothing to revert"}
        if cp["type"] not in ("stash", "clean"):
            return {"status": "error",
                    "error": f"checkpoint {cid} has no restorable state "
                             f"({cp['type']})",
                    "note": cp.get("note", "")}
        ws = str(self.workspace)
        steps: list = []
        try:
            # 1. Tracked files -> HEAD (Awino's modifications are
            #    uncommitted, so this drops exactly them).
            r = subprocess.run(["git", "-C", ws, "reset", "--hard", "HEAD"],
                               capture_output=True, text=True, timeout=60)
            steps.append(["reset --hard HEAD", r.returncode,
                          (r.stderr or "")[:200]])
            # 2. Delete ONLY untracked files Awino created after the
            #    checkpoint — never anything the operator created. The
            #    containment check keeps a hostile rel from escaping the
            #    workspace (unlinking a symlink only removes the link).
            wsp = Path(ws).resolve()
            for rel in cp.get("new_files", []):
                p = Path(ws) / rel
                try:
                    rp = p.resolve()
                    if rp != wsp and wsp not in rp.parents:
                        steps.append([f"delete {rel}", 1,
                                      "outside workspace"])
                        continue
                    if p.is_file() or p.is_symlink():
                        p.unlink()
                        steps.append([f"deleted {rel}", 0, ""])
                    else:
                        steps.append([f"delete {rel}", 0, "absent"])
                except OSError as ex:
                    steps.append([f"delete {rel}", 1, str(ex)[:120]])
            # 3. Restore the pre-checkpoint state from the stash.
            if cp["type"] == "stash":
                r = subprocess.run(
                    ["git", "-C", ws, "stash", "apply", cp["ref"]],
                    capture_output=True, text=True, timeout=60)
                steps.append([f"stash apply {cp['ref']}", r.returncode,
                              (r.stderr or "")[:200]])
            ok = all(s[1] == 0 for s in steps)
            self.loop.state.record("checkpoint_reverted", {
                "changeset_id": cid, "ok": ok, "steps": steps,
                "mission_rev": self.loop.state.snapshot["mission_revision"]})
            return {"ok": ok, "changeset_id": cid, "steps": steps}
        except Exception as ex:  # noqa: BLE001 - fail-closed
            return {"status": "error",
                    "error": f"{type(ex).__name__}: {ex}", "steps": steps}

    def _read_stdin(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                self.inbox.put({"__malformed": line[:200]})
                continue
            self.inbox.put(obj)
        self.inbox.put(None)  # EOF sentinel

    # ------------------------------------------------------------ dispatch
    def _dispatch(self, cmd) -> None:
        if cmd is None:
            self._shutdown()
            return
        if isinstance(cmd, dict) and "__malformed" in cmd:
            _err(f"malformed JSON on stdin (ignored): {cmd['__malformed']!r}")
            return
        if not isinstance(cmd, dict) or not isinstance(cmd.get("cmd"), str):
            _err(f"bad command envelope (ignored): {str(cmd)[:200]!r}")
            return
        name = cmd["cmd"]
        if self.loop is None and name != "hello":
            _err("no session: send {\"cmd\":\"hello\", ...} first")
            return
        try:
            if name == "hello":
                self._do_hello(cmd)
            elif name == "user_message":
                self._do_user_message(cmd)
            elif name == "approve":
                self._do_approve(cmd)
            elif name == "command":
                self._do_command(cmd)
            elif name == "cancel":
                self._do_cancel()
            elif name == "bye":
                self._shutdown()
            else:
                _err(f"unknown cmd {name!r}")
        except Exception as e:  # last resort: never die on a command
            traceback.print_exc(file=sys.stderr)
            _err(f"internal error on {name!r}: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- hello
    def _do_hello(self, cmd: dict) -> None:
        # The IDE tool profile (approval-gated run_command, search_files)
        # applies to this process's loop only, and only once a session
        # starts — never at import.
        _apply_sidecar_tool_profile()
        ws = cmd.get("workspace")
        if not isinstance(ws, str) or not ws:
            _err("hello requires \"workspace\" (path to the opened folder)")
            return
        wsp = Path(ws).expanduser()
        if not wsp.is_dir():
            _err(f"workspace is not a directory: {ws!r}")
            return
        self.workspace = wsp  # set early: binding resolution reads .awino/
        # 0.5.0 migration: that release auto-generated a template
        # providers.yaml on first hello. A template-identical file would now
        # silently override the user's VS Code provider settings — move it
        # aside so the settings apply. A user-edited file is never touched
        # (byte-identity check against the 0.5.0 template).
        _pyaml = wsp / ".awino" / "providers.yaml"
        if _pyaml.is_file():
            try:
                if _pyaml.read_bytes() == _PROVIDERS_YAML_TEMPLATE.encode("utf-8"):
                    _bak = _pyaml.with_name("providers.yaml.autogen-bak")
                    _pyaml.rename(_bak)
                    print("hello: renamed auto-generated providers.yaml -> "
                          f"{_bak.name}", file=sys.stderr)
                    _emit({"event": "warning",
                           "message": "removed auto-generated providers.yaml "
                                      "from 0.5.0 — your VS Code provider "
                                      "settings now apply"})
            except OSError as e:  # noqa: BLE001 - never break hello
                print(f"hello: providers.yaml migration check failed: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
        # Persisted environment choice (explicit user action) applies when
        # hello does not name one.
        env_cmd = dict(cmd)
        if not env_cmd.get("environment"):
            persisted = self._read_active_environment()
            if persisted:
                env_cmd["environment"] = persisted
        binding = self._resolve_binding(env_cmd)
        if "error" in binding:
            self.workspace = None
            _err(f"provider binding failed: {binding['error']}")
            return
        # Override visibility: a project-file binding that contradicts the
        # provider/model hello explicitly carried (i.e. the user's VS Code
        # settings) must not be silent — name the file that won.
        if binding.get("source") == "project-file":
            for _field in ("provider", "model", "aws_profile"):
                _asked = cmd.get(_field)
                _used = binding.get(_field)
                if (isinstance(_asked, str) and _asked.strip() and _used
                        and str(_asked).strip().lower()
                        != str(_used).strip().lower()):
                    _emit({"event": "warning",
                           "message": f"`.awino/providers.yaml` (environment "
                                      f"'{binding.get('environment')}') "
                                      f"overrode VS Code setting {_field} "
                                      f"'{_asked}' → using '{_used}'"})
        try:
            backend, key_status = self._apply_binding(binding, env_cmd)
        except AWSAuthError as e:
            # The Bedrock credential chain failed closed. The code travels
            # on the wire: it is the stable name the extension and the user
            # can act on. Never let it escape as a crash.
            self.workspace = None
            _err(f"{e.code}: {e}")
            return
        except ValueError as e:
            self.workspace = None
            _err(str(e))
            return
        if cmd.get("environment"):
            self._write_active_environment(cmd["environment"])
        home = Path(os.environ.get("AWINO_HOME",
                                   str(Path.home() / ".awino-loop")))
        project = cmd.get("project") or re.sub(r"[^a-z0-9-]+", "-",
                                               wsp.name.lower()).strip("-")
        loop = Loop(str(home), str(project), backend, build_judge_panel(),
                    sandbox_dir=str(wsp))
        # The IDE loop works on the real workspace, not a demo sandbox.
        loop.sandbox = WorkspaceSandbox(wsp)
        self.loop = loop
        # Native tool application (extension builder): the extension
        # advertises delegated apply/terminal capability in hello. When
        # present, file writes and run_command execute through the
        # extension (WorkspaceEdit / integrated terminal) instead of the
        # in-process sandbox — the loop keeps ownership of approval,
        # scope epochs, idempotency, and journaling.
        caps = cmd.get("capabilities")
        if isinstance(caps, dict) and caps.get("delegated_apply"):
            loop.delegation = ExtensionDelegation(self)
            self._delegated = True
            print("hello: delegated tool application enabled",
                  file=sys.stderr)
        # Track B: re-attach the file-backed memory registry on (re)connect.
        # The registry persists under <workspace>/.awino/registry/, but a
        # fresh sidecar process starts with loop.registry = None — without
        # this re-attach, tasks_list/seed_save see an empty registry after
        # every reconnect. Never breaks hello: an attach failure is recorded
        # (not swallowed) so tasks_list can report it instead of showing a
        # misleadingly empty panel.
        self._registry_attach_error = None
        self._ensure_registry()
        self.provider = binding["provider"]
        self.model_desc = getattr(backend, "model", self.provider)
        self._binding = dict(binding)
        self._binding["key"] = key_status
        # Scope #5: context window drives the compaction threshold.
        self._context_window = self._resolve_context_window(cmd, binding)
        # Contract augmentation (operator context + project skills): patch
        # the name loop.py resolved at import, process-locally, once.
        global _ACTIVE_SIDECAR, _COMPILE_PATCHED
        _ACTIVE_SIDECAR = self
        if not _COMPILE_PATCHED:
            loop_module.compile_contract = _patched_compile_contract
            _COMPILE_PATCHED = True
        self._load_custom_modes()
        self._restore_pinned_mode()
        # Scope #5: initialize housekeeping phase tracker so the first
        # turn's phase isn't treated as a transition.
        try:
            self._last_housekept_phase = self.loop.state.snapshot.get("phase")
        except Exception:  # noqa: BLE001 - never break hello
            pass
        self.loop.state.record("provider_resolved", {
            "provider": binding["provider"], "model": self.model_desc,
            "environment": binding.get("environment"),
            "source": binding.get("source"),
            "key_id": binding.get("key_id") or "",
            "key": key_status,
        })
        self._register_mcp_servers(cmd.get("mcp_servers") or [])
        # Track A/H: auto-init on session start. If the workspace is not an
        # awino project yet (.awino/project.yaml absent), run the full init
        # flow — the user never has to type `awino init` by hand.
        # (First-message mission start re-runs the idempotent checklist
        # anyway via _bootstrap_and_registry.)
        #
        # Windows fix (0.5.0): session_start_auto_init can block for 120s
        # (ensure_venv runs `python -m venv`, whose ensurepip stalls on
        # Windows). It MUST NOT block the ready event — the extension times
        # out waiting for ready. Emit ready first, then run auto-init in a
        # background daemon thread. The ready event carries nulls for the
        # auto-init fields; when the thread finishes it records state and
        # emits an auto_init_complete event with the summary.
        _emit({"event": "ready", "protocol": PROTOCOL, "project": project,
               "provider": self.provider, "model": self.model_desc,
               "workspace": str(wsp), "mcp": self._mcp_status,
               "binding": {k: v for k, v in self._binding.items()},
               "modes": self._modes_summary(),
               "active_mode": self._active_mode_info(),
               "auto_init": None,
               "stories_review": None})
        self._run_auto_init_async(wsp)

    def _run_auto_init_async(self, wsp) -> None:
        """Run session_start_auto_init in a background daemon thread.

        The init flow (venv creation, project scaffolding) can take 120s+
        on Windows — it must never block the ready event. When the thread
        finishes, state is recorded and an auto_init_complete event is
        emitted so the UI can surface the summary.
        """
        import threading

        def _worker():
            try:
                from bootstrap import session_start_auto_init
                auto_init = session_start_auto_init(wsp)
                if auto_init:
                    try:
                        self.loop.state.record("auto_init", {
                            "ok": auto_init["ok"],
                            "summary": auto_init["summary"]})
                    except Exception:
                        pass
                    sr = auto_init.get("stories_review")
                    if sr:
                        try:
                            self.loop.state.record("stories_review", {
                                "stories": sr["stories"]})
                        except Exception:
                            pass
            except Exception:
                pass
            # NOTE: no unsolicited event is emitted here. The request/response
            # protocol expects recv() after a command to return that
            # command's result; an auto_init_complete event would race with
            # it. State is recorded in the journal; the summary is available
            # via the contract/status paths.

        threading.Thread(target=_worker, daemon=True,
                         name="awino-auto-init").start()

    # --------------------------------------------- scoped provider bindings
    def _read_providers_file(self) -> dict | None:
        """Read <workspace>/.awino/providers.yaml (strict subset). None when
        absent; {"error"} when present-but-invalid (fail closed: the operator
        must fix the file, we do not guess)."""
        p = self.workspace / ".awino" / "providers.yaml" if self.workspace else None
        if p is None or not p.is_file():
            return None
        try:
            text = p.read_text()[:65536]
        except OSError as e:
            return {"error": f"cannot read providers.yaml: {e}"}
        return _parse_providers_yaml(text)

    def _resolve_binding(self, cmd: dict) -> dict:
        """Resolve provider binding with strict precedence:
        hello globals < project providers.yaml default environment
        < explicitly named environment. Returns the binding or {"error"}.
        The binding carries key_id (opaque reference) but NEVER key material.
        """
        binding = {"provider": str(cmd.get("provider") or "echo").lower(),
                   "model": cmd.get("model") or None,
                   "endpoint": cmd.get("endpoint") or None,
                   "key_id": None, "aws_profile": None,
                   "environment": None, "source": "global-settings"}
        if binding["provider"] not in _KNOWN_PROVIDERS:
            return {"error": f"unknown provider {binding['provider']!r}"}
        aws_profile = cmd.get("aws_profile")
        if aws_profile is not None:
            if (not isinstance(aws_profile, str)
                    or not aws_profile.strip()):
                return {"error": 'aws_profile must be a non-empty string'}
            binding["aws_profile"] = aws_profile.strip()
        if binding["provider"] != "bedrock" and binding["aws_profile"]:
            return {"error": 'aws_profile is only valid with provider '
                             '"bedrock"'}
        parsed = self._read_providers_file()
        if parsed is not None and "error" in parsed:
            return {"error": parsed["error"]}
        if parsed:
            default_env = (parsed.get("default_environment") or "default")
            envs = parsed["environments"]
            if default_env in envs:
                for f in _BINDING_FIELDS:
                    v = envs[default_env].get(f)
                    if v:
                        binding[f] = v
                for f in _ENV_SETTING_FIELDS:
                    v = envs[default_env].get(f)
                    if v:
                        binding[f] = v
                binding["environment"] = default_env
                binding["source"] = "project-file"
        named = cmd.get("environment")
        if named:
            if not isinstance(named, str):
                return {"error": "environment must be a string"}
            if not parsed or named not in parsed["environments"]:
                return {"error": f"unknown environment {named!r}"}
            for f in _BINDING_FIELDS:
                v = parsed["environments"][named].get(f)
                if v:
                    binding[f] = v
            for f in _ENV_SETTING_FIELDS:
                v = parsed["environments"][named].get(f)
                if v:
                    binding[f] = v
            binding["environment"] = named
            binding["source"] = "named-environment"
        return binding

    def _apply_binding(self, binding: dict, cmd: dict):
        """Build (and wrap) the backend for a resolved binding. Returns
        (backend, key_status). Key material goes only into the backend."""
        key_id = binding.get("key_id") or ""
        material, key_status = _lookup_key_material(key_id)
        merged = dict(cmd)
        for f in _BINDING_FIELDS:
            if binding.get(f):
                merged[f] = binding[f]
        backend = self._make_backend(binding["provider"], merged,
                                     api_key=material)
        # Truthfulness: the env fallback inside the backend may supply a
        # key even when no key_id was given (VS Code injects stored keys
        # into the sidecar env). If a key will actually be sent, say so.
        if (key_status == "not-required"
                and getattr(backend, "api_key", None)):
            key_status = "configured"
        if binding["provider"] == "bedrock":
            # Bedrock authenticates with SigV4 from the AWS credential
            # chain, not a stored key: say which profile was used.
            prof = getattr(backend, "aws_profile", None) or "default"
            key_status = f"sigv4-profile:{prof}"
        return _ModeAwareBackend(backend, self), key_status

    def _make_backend(self, provider: str, cmd: dict, api_key=None):
        timeout = cmd.get("timeout") or float(
            os.environ.get("AWINO_TIMEOUT", "180"))
        model = cmd.get("model") or None
        endpoint = cmd.get("endpoint") or None
        if provider == "echo":
            return EchoBackend()
        if provider == "ollama":
            return OllamaBackend(model=model, host=endpoint, timeout=timeout)
        if provider == "openai":
            return OpenAICompatibleBackend(model=model, endpoint=endpoint,
                                           timeout=timeout, api_key=api_key)
        if provider == "anthropic":
            return AnthropicBackend(model=model, endpoint=endpoint,
                                    timeout=timeout, api_key=api_key)
        if provider == "bedrock":
            # SigV4-signed Bedrock (AWS profile / SSO chain). Credentials
            # resolve eagerly inside the constructor: failure raises
            # AWSAuthError and the hello fails closed.
            return BedrockSigV4Backend(model=model, endpoint=endpoint,
                                       timeout=timeout,
                                       aws_profile=cmd.get("aws_profile"))
        if provider == "scripted":
            # TEST-ONLY provider: candidate turns are fed through the
            # identical validation/judge/approval pipeline. It is a mock
            # model, not a bypass.
            script = cmd.get("script")
            if not isinstance(script, list):
                raise ValueError(
                    'provider "scripted" requires "script": [turn, ...]')
            return ScriptedBackend(script)
        raise ValueError(
            f"unknown provider {provider!r} "
            "(use echo|ollama|openai|anthropic|scripted|bedrock)")

    # ------------------------------------------------------- modes as data
    def _read_active_environment(self) -> str | None:
        p = self.workspace / ".awino" / "active-environment.json"
        if not p.is_file():
            return None
        try:
            name = json.loads(p.read_text()[:4096]).get("environment")
        except (json.JSONDecodeError, ValueError, AttributeError):
            return None
        return name if isinstance(name, str) and name else None

    def _write_active_environment(self, name: str) -> None:
        d = self.workspace / ".awino"
        d.mkdir(parents=True, exist_ok=True)
        (d / "active-environment.json").write_text(
            json.dumps({"schema_version": 1, "environment": name}))

    def _load_custom_modes(self) -> None:
        """Load user-defined modes from .awino/modes/*.json (pure data)."""
        self._custom_modes = []
        self._invalid_modes = []
        mdir = self.workspace / ".awino" / "modes"
        if not mdir.is_dir():
            return
        for p in sorted(mdir.glob("*.json")):
            try:
                data = json.loads(p.read_text()[:16384])
            except (json.JSONDecodeError, ValueError, OSError) as e:
                self._invalid_modes.append(
                    {"file": p.name, "error": f"{type(e).__name__}: {e}"})
                continue
            mode = _validate_custom_mode(data)
            if "error" in mode:
                self._invalid_modes.append(
                    {"file": p.name, "error": mode["error"]})
            elif any(m["id"] == mode["id"] for m in self._custom_modes):
                self._invalid_modes.append(
                    {"file": p.name,
                     "error": f"duplicate mode id {mode['id']!r}"})
            else:
                self._custom_modes.append(mode)

    def _all_modes(self) -> list[dict]:
        return BUILTIN_MODES + self._custom_modes

    def _find_mode(self, mode_id: str) -> dict | None:
        for m in self._all_modes():
            if m["id"] == mode_id:
                return m
        return None

    def _stage_default_mode(self, phase: str | None) -> dict:
        default_id = _STAGE_DEFAULT_MODE.get(phase or "", "interview")
        mode = self._find_mode(default_id)
        return mode or BUILTIN_MODES[0]

    def _restore_pinned_mode(self) -> None:
        """Restore a project-pinned mode overlay (.awino/mode.json)."""
        p = self.workspace / ".awino" / "mode.json"
        if not p.is_file():
            return
        try:
            mode_id = json.loads(p.read_text()[:4096]).get("mode")
        except (json.JSONDecodeError, ValueError, AttributeError):
            return
        if isinstance(mode_id, str) and self._find_mode(mode_id):
            self._mode_overlay = {"mode": mode_id, "scope": "project"}

    def _active_mode(self) -> tuple[dict, dict]:
        """Return (mode, info). The overlay is user-invoked; otherwise the
        stage's default mode is automatic. Turn-scoped overlays expire."""
        if self._mode_overlay:
            ov = self._mode_overlay
            turn_no = self.loop.state.snapshot.get("turn_count", 0) if self.loop else 0
            if ov["scope"] == "turns" and turn_no >= ov.get("expires_turn", 0):
                self._mode_overlay = None
                if self.loop:
                    self.loop.state.record("mode_changed", {
                        "mode": self._stage_default_mode(
                            self.loop.state.snapshot.get("phase"))["id"],
                        "previous": ov["mode"], "reason": "overlay expired"})
            else:
                mode = self._find_mode(ov["mode"])
                if mode:
                    return mode, {"id": mode["id"], "label": mode["label"],
                                  "source": "overlay",
                                  "overlay": {k: v for k, v in ov.items()}}
                self._mode_overlay = None
        phase = self.loop.state.snapshot.get("phase") if self.loop else None
        mode = self._stage_default_mode(phase)
        return mode, {"id": mode["id"], "label": mode["label"],
                      "source": "stage-default", "stage": phase}

    def _active_mode_info(self) -> dict:
        try:
            return self._active_mode()[1]
        except Exception:  # noqa: BLE001 - mode info must never break hello
            return {"id": "interview", "source": "stage-default"}

    def _active_temperature(self) -> float:
        try:
            mode, _ = self._active_mode()
            return float(mode.get("sampling", {}).get("temperature", 0.2))
        except Exception:  # noqa: BLE001
            return 0.2

    def _modes_summary(self) -> dict:
        return {"builtin": [m["id"] for m in BUILTIN_MODES],
                "custom": [m["id"] for m in self._custom_modes],
                "invalid": self._invalid_modes}

    def _stage_offered_tools(self) -> list[str]:
        try:
            return list(MODES[self.loop.state.snapshot["mode"]]["tools"])
        except Exception:  # noqa: BLE001
            return []

    def _cmd_mode_list(self, args: dict) -> dict:
        mode, info = self._active_mode()
        return {"modes": [{"id": m["id"], "label": m["label"],
                           "stages": m["stages"],
                           "sampling": m.get("sampling", {}),
                           "custom": bool(m.get("custom"))}
                          for m in self._all_modes()],
                "active": info,
                "invalid": self._invalid_modes}

    def _cmd_mode_invoke(self, args: dict) -> dict:
        mode_id = args.get("mode")
        if not isinstance(mode_id, str) or not self._find_mode(mode_id):
            return {"status": "refused", "code": "unknown-mode",
                    "known": [m["id"] for m in self._all_modes()]}
        scope = args.get("scope") or "mission"
        if scope not in ("mission", "project", "turns"):
            return {"status": "refused", "code": "bad-scope",
                    "detail": "scope must be mission|project|turns"}
        turn_no = self.loop.state.snapshot.get("turn_count", 0)
        overlay = {"mode": mode_id, "scope": scope,
                   "invoked_turn": turn_no}
        if scope == "turns":
            try:
                n = int(args.get("turns", 1))
            except (TypeError, ValueError):
                return {"status": "refused", "code": "bad-turns"}
            n = max(1, min(n, 200))
            overlay["expires_turn"] = turn_no + n
        elif scope == "project":
            (self.workspace / ".awino").mkdir(parents=True, exist_ok=True)
            (self.workspace / ".awino" / "mode.json").write_text(
                json.dumps({"schema_version": 1, "mode": mode_id}))
        previous = self._active_mode_info().get("id")
        self._mode_overlay = overlay
        self.loop.state.record("mode_changed", {
            "mode": mode_id, "previous": previous, "scope": scope,
            "by": "operator"})
        return {"ok": True, "active_mode": self._active_mode_info()}

    def _cmd_mode_dismiss(self, args: dict) -> dict:
        ov = self._mode_overlay
        self._mode_overlay = None
        pin = self.workspace / ".awino" / "mode.json"
        if pin.is_file():
            pin.unlink()
        if ov:
            self.loop.state.record("mode_changed", {
                "mode": self._stage_default_mode(
                    self.loop.state.snapshot.get("phase"))["id"],
                "previous": ov["mode"], "reason": "dismissed by operator"})
        return {"ok": True, "active_mode": self._active_mode_info()}

    # ------------------------------------------------------- skill-as-persona
    def _find_admitted_skill(self, name: str) -> tuple[str, str] | None:
        """Return (body, sha256) for an admitted skill, or None. ONLY
        verified + SHA-256-pinned skills can be personified: a user-dropped
        file must pass the verification/admission pipeline first."""
        if not isinstance(name, str) or not name:
            return None
        try:
            body = self.loop.skill_store.get_verified(name)
            digest = self.loop.skill_store.pinned_hash(name)
            if digest:
                return body, digest
        except Exception:  # noqa: BLE001 - fall through to project registry
            pass
        reg = self.loop.state.dir / "skills"
        if reg.is_dir():
            try:
                store = open_registry(reg)
                body = store.get_verified(name)
                digest = store.pinned_hash(name)
                if digest:
                    return body, digest
            except Exception:  # noqa: BLE001 - unadmitted or tampered
                pass
        return None

    def _cmd_persona_assume(self, args: dict) -> dict:
        name = args.get("skill")
        found = self._find_admitted_skill(name) if name else None
        if not found:
            return {"status": "refused", "code": "unadmitted-skill",
                    "detail": f"skill {name!r} is not admitted "
                              f"(verified + SHA-256-pinned). Run it through "
                              f"the skill admission pipeline first."}
        body, digest = found
        try:
            n = int(args.get("turns", 10))
        except (TypeError, ValueError):
            return {"status": "refused", "code": "bad-turns"}
        n = max(1, min(n, 200))
        turn_no = self.loop.state.snapshot.get("turn_count", 0)
        phase = self.loop.state.snapshot.get("phase")
        self._persona = {"skill": name, "sha256": digest,
                         "body": body[:4096],
                         "invoked_turn": turn_no,
                         "expires_turn": turn_no + n,
                         "invoked_phase": phase}
        self.loop.state.record("persona_assumed", {
            "skill": name, "sha256": digest, "turns": n,
            "invoked_phase": phase})
        return {"ok": True, "persona": self._persona_info()}

    def _cmd_persona_dismiss(self, args: dict) -> dict:
        if self._persona:
            self.loop.state.record("persona_dismissed", {
                "skill": self._persona["skill"],
                "sha256": self._persona["sha256"]})
        self._persona = None
        return {"ok": True, "persona": None}

    def _active_persona(self) -> dict | None:
        """The persona, or None when expired/dismissed. Expiry is recorded
        so the journal (and the judge, via the contract) always sees it."""
        p = self._persona
        if not p:
            return None
        turn_no = self.loop.state.snapshot.get("turn_count", 0)
        phase = self.loop.state.snapshot.get("phase")
        reason = None
        if turn_no >= p["expires_turn"]:
            reason = "turn budget exhausted"
        elif phase != p["invoked_phase"]:
            reason = f"stage changed ({p['invoked_phase']} -> {phase})"
        if reason:
            self.loop.state.record("persona_expired", {
                "skill": p["skill"], "sha256": p["sha256"], "reason": reason})
            self._persona = None
            return None
        return p

    def _persona_info(self) -> dict | None:
        p = self._active_persona()
        if not p:
            return None
        return {"skill": p["skill"], "sha256": p["sha256"],
                "expires_turn": p["expires_turn"],
                "invoked_phase": p["invoked_phase"]}

    def _persona_section(self) -> str:
        """Front-loaded persona block (pinned tier). A lens, not a license:
        the effective tool set is intersected with the stage's offered tools
        in code, so the persona can never widen what the contract allows."""
        p = self._active_persona()
        if not p:
            return ""
        offered = self._stage_offered_tools()
        priority = _persona_tool_priority(p["body"], offered)
        # Structural refusal: widening is impossible by construction, and we
        # assert it here so a future code path cannot regress it silently.
        assert all(t in offered for t in priority), "persona widened tools!"
        lines = ["## PERSONA (lens, not license — how to think, never what "
                 "you may do)",
                 f"Skill: {p['skill']} (sha256: {p['sha256']}) · "
                 f"expires turn {p['expires_turn']} or on stage change",
                 p["body"]]
        if priority:
            lines.append("When choosing tools, prefer these (all already "
                         "offered by your stage): " + ", ".join(priority))
        lines.append("Approvals, tool gates, and the compiled contract still "
                     "bind. This persona cannot widen your tool policy.")
        return "\n".join(lines)

    def _mode_section(self) -> str:
        """Mode block: the model always knows its mode (no modal confusion).
        Mode tool_policy is emphasis only — enforcement stays with the
        stage's offered set, which is never widened."""
        mode, info = self._active_mode()
        offered = self._stage_offered_tools()
        emphasis, dropped = _mode_tool_emphasis(mode, offered)
        lines = ["## MODE (data-driven — composed into the pinned contract "
                 "every turn)"]
        phase = self.loop.state.snapshot.get("phase") if self.loop else None
        src = info["source"]
        if src == "overlay":
            default_id = self._stage_default_mode(phase)["id"]
            lines.append(f"mode: {mode['id']} ({mode['label']}) · invoked by "
                         f"operator (scope: {info['overlay']['scope']}) · "
                         f"stage default would be {default_id}")
        else:
            lines.append(f"mode: {mode['id']} ({mode['label']}) · automatic "
                         f"for stage {info.get('stage')}")
        lines.append("Stance: " + mode["stance_prompt"])
        lines.append(f"Sampling: temperature "
                     f"{mode.get('sampling', {}).get('temperature', 0.2)} "
                     f"(set by the harness per call)")
        lines.append("Tools offered this stage (mode emphasis order): "
                     + (", ".join(emphasis) if emphasis else "(none)"))
        if dropped:
            lines.append("Mode policy entries NOT offered this stage "
                         "(dropped, not widened): " + ", ".join(dropped))
        if src == "overlay":
            lines.append("This override is journal-logged and visible to "
                         "the judge.")
        return "\n".join(lines)

    def _provider_section(self) -> str:
        """Provider identity for auditability: name + model + environment.
        Key material NEVER appears here (or anywhere outside the backend)."""
        b = self._binding or {}
        return ("## PROVIDER (audit identity — no key material)\n"
                f"provider: {b.get('provider', '?')} | "
                f"model: {self.model_desc or '?'} | "
                f"environment: {b.get('environment') or '(global settings)'} | "
                f"binding source: {b.get('source', '?')} | "
                f"key: {b.get('key', 'not-required')} "
                f"(material lives only in secret storage)")

    # ------------------------------------------------------- env switching
    def _cmd_status(self, args: dict) -> dict:
        """Loop status enriched with the IDE-visible identity: provider
        binding, live mode, and persona (the status-bar / mode-chip data)."""
        st = self.loop.status()
        st["binding"] = dict(self._binding) if self._binding else None
        st["active_mode"] = self._active_mode_info()
        st["persona"] = self._persona_info()
        return {"ok": True, "status": st}

    def _cmd_rigor_report(self, args: dict) -> dict:
        """Rigor coach: score mission(s) from journal evidence, journal it.

        Read-only except for appending the `rigor_report` event — the report
        itself becomes evidence. Args: {mission_id?, recent?}.
        """
        if self.loop is None:
            return {"status": "refused", "code": "no-loop",
                    "detail": "connect a project first"}
        try:
            import rigor as _rigor
            mission_id = args.get("mission_id")
            recent = args.get("recent")
            if recent is not None:
                recent = int(recent)
            report = _rigor.report_rigor(self.loop.state,
                                         mission_id=mission_id,
                                         recent=recent, record=True)
            reports = report if isinstance(report, list) else [report]
            return {"ok": True, "reports": [
                {"mission_id": r["mission_id"], "score": r["score"],
                 "scored": f"{r['n_scored']}/{r['n_checks']}",
                 "failing": r["failing"], "unknown": r["unknown"],
                 "overrides": r["overrides"],
                 "checks": {c["check"]: {"status": c["status"],
                                         "law": c["law"],
                                         "nudge": c["nudge"]}
                            for c in r["checks"]},
                 "text": _rigor.render_text(r)}
                for r in reports]}
        except (ValueError, TypeError) as e:
            return {"status": "refused", "code": "bad-rigor-args",
                    "detail": str(e)[:200]}

    def _cmd_env_switch(self, args: dict) -> dict:
        name = args.get("environment")
        if not isinstance(name, str) or not name:
            return {"status": "refused", "code": "bad-environment"}
        binding = self._resolve_binding({"provider": "echo",
                                         "environment": name})
        if "error" in binding:
            return {"status": "refused", "code": "unknown-environment",
                    "detail": binding["error"]}
        try:
            backend, key_status = self._apply_binding(binding, {"environment": name})
        except ValueError as e:
            return {"status": "refused", "code": "bad-provider",
                    "detail": str(e)}
        self.loop.backend = backend  # the Loop is untouched; we swap the
        # backend object it already calls, like changing a battery.
        self.provider = binding["provider"]
        self.model_desc = getattr(backend, "model", self.provider)
        self._binding = dict(binding)
        self._binding["key"] = key_status
        self._write_active_environment(name)
        self.loop.state.record("environment_switched", {
            "environment": name, "provider": binding["provider"],
            "model": self.model_desc,
            "source": binding.get("source"), "key": key_status})
        return {"ok": True, "binding": dict(self._binding),
                "active_mode": self._active_mode_info()}

    # ------------------------------------------------- #5: config + window
    def _config_path(self) -> Path:
        return self.workspace / ".awino" / "config.json"

    def _read_config(self) -> dict:
        """Per-project config (compaction.auto_approve, housekeeping opts).
        Missing/corrupt file -> safe defaults. auto_approve defaults FALSE:
        the user is asked first, always."""
        cfg = _default_config()
        p = self._config_path()
        if p.is_file():
            try:
                raw = json.loads(p.read_text()[:16384])
                if isinstance(raw, dict):
                    comp = raw.get("compaction") or {}
                    hk = raw.get("housekeeping") or {}
                    if isinstance(comp, dict):
                        cfg["compaction"]["auto_approve"] = bool(
                            comp.get("auto_approve", False))
                    if isinstance(hk, dict):
                        cfg["housekeeping"]["git_commit"] = bool(
                            hk.get("git_commit", False))
                        rd = hk.get("retention_days", 30)
                        if isinstance(rd, int) and 1 <= rd <= 3650:
                            cfg["housekeeping"]["retention_days"] = rd
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        return cfg

    def _write_config(self, cfg: dict) -> None:
        cfg = dict(cfg)
        cfg["schema_version"] = _CONFIG_SCHEMA_VERSION
        self._config_path().write_text(json.dumps(cfg, indent=2))

    def _resolve_context_window(self, cmd: dict, binding: dict) -> int:
        """hello context_window > providers.yaml env > default."""
        raw = cmd.get("context_window") or binding.get("context_window")
        try:
            w = int(raw) if raw is not None else _DEFAULT_CONTEXT_WINDOW
        except (TypeError, ValueError):
            w = _DEFAULT_CONTEXT_WINDOW
        return max(512, min(w, 2000000))

    # ------------------------------------------------- #5: compaction
    def _projected_tokens(self) -> tuple[int, int, int]:
        """(projected_total, contract_tokens, history_tokens). The contract
        is the PINNED tier (never reduced); history is SUMMARIZABLE."""
        try:
            turn_no = self.loop.state.snapshot.get("turn_count", 0) + 1
            block = loop_module.compile_contract(self.loop.state,
                                                 turn_no=turn_no)
        except Exception:  # noqa: BLE001 - estimate must never break a turn
            block = ""
        contract_tokens = _estimate_tokens(block)
        history_tokens = sum(
            _estimate_tokens(f"{h.get('role', '')} {h.get('text', '')}")
            for h in (self.loop.history or []))
        return contract_tokens + history_tokens, contract_tokens, \
            history_tokens

    def _build_compaction_proposal(self, projected: int,
                                    contract_tokens: int,
                                    history_tokens: int) -> dict:
        hist = self.loop.history or []
        n = len(hist)
        k = max(1, int(n * 0.3)) if n else 0
        victim_tokens = sum(
            _estimate_tokens(f"{h.get('role', '')} {h.get('text', '')}")
            for h in hist[:k])
        summary_tokens = _estimate_tokens(
            f"[compacted {k} turns]") + k * 60
        savings = max(0, victim_tokens - summary_tokens)
        return {
            "proposal_id": f"cmp-{os.urandom(4).hex()}",
            "reason": (f"projected input ~{projected} tokens >= "
                       f"{int(_COMPACTION_THRESHOLD * 100)}% of "
                       f"{self._context_window} context window"),
            "window": self._context_window,
            "projected_tokens": projected,
            "threshold": _COMPACTION_THRESHOLD,
            "tiers": {
                "pinned": {
                    "tokens": contract_tokens,
                    "what": "compiled contract + done criteria",
                    "action": "NEVER reduced",
                },
                "summarizable": {
                    "tokens": history_tokens,
                    "turns": n,
                    "action": f"summarize oldest ~30% ({k} turns) into an "
                              f"extractive summary",
                },
                "offloadable": {
                    "tokens": 0,
                    "what": "tool outputs",
                    "action": "already truncated in history; full results "
                              "stay in the journal",
                },
            },
            "pinned_safe": ["contract", "done criteria", "mission",
                            "journal", "approvals"],
            "savings_estimate_tokens": savings,
            "auto_approve": self._read_config()["compaction"]["auto_approve"],
        }

    def _maybe_compact_before_turn(self) -> None:
        """Scope #5: auto-compaction never silently drops context. When the
        ~0.85 threshold is hit, emit compaction_proposed and pause for the
        user's approval via the normal approve/deny commands."""
        if not self.loop:
            return
        projected, contract_tokens, history_tokens = self._projected_tokens()
        if projected < _COMPACTION_THRESHOLD * self._context_window:
            return
        # Warn-as-it-fills: re-propose only on meaningful growth, so a
        # denial doesn't nag every turn.
        if projected < self._last_proposal_tokens * 1.1 \
                and self._last_proposal_tokens:
            return
        self._last_proposal_tokens = projected
        proposal = self._build_compaction_proposal(
            projected, contract_tokens, history_tokens)
        if proposal["auto_approve"]:
            self._perform_compaction(proposal, by="auto-approve")
            return
        self._pending_compaction = proposal
        _emit({"event": "compaction_proposed", **proposal,
               "note": "Approve to compact, or deny to continue without "
                       "compacting. Denial is journaled."})
        decision = self._await_compaction_decision(proposal["proposal_id"])
        self._pending_compaction = None
        if decision == "approve":
            self._perform_compaction(proposal, by="operator")
        else:
            self.loop.state.record("compaction_declined", {
                "proposal_id": proposal["proposal_id"],
                "projected_tokens": projected,
                "window": self._context_window})
            _emit({"event": "warning",
                   "message": "Continuing without compacting; the context "
                              "window keeps filling. Enable "
                              "compaction.auto_approve per-project to stop "
                              "being asked."})

    def _await_compaction_decision(self, proposal_id: str) -> str:
        """Pump commands until the user approves/denies the compaction.
        Anything else is deferred for the main loop. Cancel counts as deny
        (safe direction: no context is dropped)."""
        while True:
            cmd = self._next_command()
            if cmd is None:  # EOF
                return "deny"
            if not isinstance(cmd, dict):
                continue
            name = cmd.get("cmd")
            if name == "approve" and cmd.get("id") == proposal_id:
                return "approve" if cmd.get("decision") == "approve" \
                    else "deny"
            if name == "deny" and cmd.get("id") == proposal_id:
                return "deny"
            if name == "command" and isinstance(cmd.get("args"), dict) \
                    and cmd.get("name") == "compaction_decide" \
                    and cmd["args"].get("id") == proposal_id:
                return "approve" \
                    if cmd["args"].get("decision") == "approve" else "deny"
            if name == "cancel":
                return "deny"
            self.deferred.append(cmd)

    def _perform_compaction(self, proposal: dict, by: str) -> dict:
        """Replace the oldest ~30% of history turns with an extractive
        summary. The PINNED tier (contract) is untouched; the journal keeps
        the full detail, so nothing is silently lost."""
        hist = self.loop.history or []
        n = len(hist)
        k = max(1, int(n * 0.3)) if n else 0
        victims = hist[:k]
        lines = []
        for i, h in enumerate(victims):
            role = h.get("role", "?")
            text = str(h.get("text", ""))[:200].replace("\n", " ")
            lines.append(f"- turn~{i} [{role}] {text}")
        summary = (
            f"[Context compacted {_utc_now()}: oldest {k} of {n} turns "
            f"summarized ({by}). Full detail remains in the journal.]"
            + ("\n" + "\n".join(lines) if lines else ""))
        before_tokens = sum(
            _estimate_tokens(f"{h.get('role', '')} {h.get('text', '')}")
            for h in victims)
        self.loop.history = ([{"role": "system", "text": summary}]
                             + hist[k:])
        after_tokens = _estimate_tokens(summary)
        self.loop.state.record("compaction_performed", {
            "proposal_id": proposal["proposal_id"], "by": by,
            "turns_summarized": k, "turns_total": n,
            "tokens_before": before_tokens, "tokens_after": after_tokens,
            "tokens_saved": max(0, before_tokens - after_tokens)})
        return {"ok": True, "turns_summarized": k,
                "tokens_saved": max(0, before_tokens - after_tokens)}

    # ------------------------------------------------- #5: housekeeping
    _HOUSEKEEPING_DIRS = ("journal", "tool_results", "seeds", "context",
                          "skills", "modes", "archive")

    def _ensure_awino_layout(self) -> list[str]:
        """Every project gets the same .awino/ layout, with README indexes.
        Returns a list of human-readable actions taken."""
        actions = []
        base = self.workspace / ".awino"
        base.mkdir(parents=True, exist_ok=True)
        readme = base / "README.md"
        if not readme.is_file():
            readme.write_text(_AWINO_README)
            actions.append("wrote .awino/README.md (FAIR index)")
        # providers.yaml: per-environment provider bindings (YAML is the
        # explicit plain-text exception to the JSON/Markdown FAIR rule —
        # provider configs are conventionally YAML).
        # NOTE: Do NOT auto-generate this file. VS Code settings are the
        # source of truth unless the user created .awino/providers.yaml,
        # which takes precedence by design (project-local override).
        pyaml = base / "providers.yaml"
        if pyaml.is_file():
            actions.append("found .awino/providers.yaml (user-configured)")
        for d in self._HOUSEKEEPING_DIRS:
            p = base / d
            if not p.is_dir():
                p.mkdir(parents=True, exist_ok=True)
                actions.append(f"created .awino/{d}/")
            r = p / "README.md"
            if not r.is_file() and d in _FOLDER_READMES:
                r.write_text(_FOLDER_READMES[d])
                actions.append(f"wrote .awino/{d}/README.md")
        return actions

    def _write_contract_snapshot(self) -> str | None:
        """contract.json: FAIR snapshot of the current mission contract."""
        try:
            st = self.loop.status()
        except Exception:  # noqa: BLE001 - snapshot must never break a turn
            return None
        binding = self._binding or {}
        snap = {
            "schema_version": _CONTRACT_SNAPSHOT_SCHEMA_VERSION,
            "exported_at": _utc_now(),
            "project": st.get("project"),
            "phase": st.get("phase"),
            "mode": st.get("mode"),
            "active_mode": self._active_mode_info(),
            "mission": st.get("mission"),
            "done_criteria": st.get("criteria"),
            "provider": {
                "provider": binding.get("provider"),
                "model": self.model_desc,
                "environment": binding.get("environment"),
                "source": binding.get("source"),
                "key": binding.get("key"),
            },
        }
        (self.workspace / ".awino" / "contract.json").write_text(
            json.dumps(snap, indent=2))
        return "wrote .awino/contract.json"

    def _export_journal(self) -> list[str]:
        """Export the journal to .awino/journal/journal.jsonl (FAIR copy).
        Superseded exports move to archive/ with timestamps. Also exports
        the full state event log (events.jsonl) so decisions like
        compaction_declined/performed are preserved, not just tool calls."""
        actions = []
        try:
            entries = self.loop.effect_journal()
        except Exception:  # noqa: BLE001
            entries = []
        try:
            events = [
                {"seq": e.get("seq"), "type": e.get("type"),
                 "data": e.get("data", {})}
                for e in self.loop.state.events
            ]
        except Exception:  # noqa: BLE001
            events = []
        jdir = self.workspace / ".awino" / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        # Rotate existing exports
        for fname in ("journal.jsonl", "events.jsonl"):
            target = jdir / fname
            if target.is_file():
                stamp = datetime.datetime.now(
                    datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                archived = self._archive_file(
                    target, f"{fname[:-6]}-{stamp}.jsonl")
                actions.append(f"rotated journal export -> {archived}")
        # Write effect journal (tool calls)
        with open(jdir / "journal.jsonl", "w") as f:
            f.write(json.dumps({"schema_version": 1,
                                "exported_at": _utc_now(),
                                "entries": len(entries)}) + "\n")
            for e in entries[-500:]:
                f.write(json.dumps(e, default=str) + "\n")
        actions.append(f"exported journal ({min(len(entries), 500)} entries)")
        # Write state event log (decisions, compactions, approvals)
        with open(jdir / "events.jsonl", "w") as f:
            f.write(json.dumps({"schema_version": 1,
                                "exported_at": _utc_now(),
                                "entries": len(events)}) + "\n")
            for e in events[-1000:]:
                f.write(json.dumps(e, default=str) + "\n")
        actions.append(f"exported events ({min(len(events), 1000)} entries)")
        return actions

    def _archive_file(self, path: Path, name: str | None = None) -> str:
        """Move a file to .awino/archive/ with a timestamp. Never deletes."""
        adir = self.workspace / ".awino" / "archive"
        adir.mkdir(parents=True, exist_ok=True)
        # Microsecond resolution + collision-safe suffix: two archives in
        # the same microsecond get -2, -3, etc. rather than overwriting.
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%d-%H%M%S-%f")
        base = f"{stamp}_{name or path.name}"
        dest = adir / base
        n = 2
        while dest.exists():
            dest = adir / f"{base}-{n}"
            n += 1
        path.rename(dest)
        return f"archive/{dest.name}"

    def _archive_tool_results(self, retention_days: int) -> list[str]:
        """Move expired/unreferenced tool_results to archive/. Expired =
        older than retention_days. Never deletes anything."""
        actions = []
        tdir = self.workspace / ".awino" / "tool_results"
        if not tdir.is_dir():
            return actions
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        for p in sorted(tdir.iterdir()):
            if p.name == "README.md" or not p.is_file():
                continue
            age_days = (now - p.stat().st_mtime) / 86400
            if age_days > retention_days:
                dest = self._archive_file(p)
                actions.append(f"archived stale tool_result -> {dest}")
        return actions

    def _tidy_awino_root(self) -> list[str]:
        """Archive unknown/superseded files at .awino/ root (e.g. draft.tmp,
        *.bak). Known files (README, config, manifests, providers.yaml) stay.
        Never deletes."""
        known = {
            "README.md", "config.json", "contract.json",
            "housekeeping.json", "providers.yaml",
            "active-environment.json", "mode.json", "context.json",
        }
        known_dirs = set(self._HOUSEKEEPING_DIRS)
        actions = []
        base = self.workspace / ".awino"
        if not base.is_dir():
            return actions
        for p in sorted(base.iterdir()):
            if p.name in known or p.name in known_dirs:
                continue
            if p.is_dir():
                continue  # only tidy files, not unknown dirs
            # Unknown file: archive it, don't delete.
            dest = self._archive_file(p)
            actions.append(f"archived unknown root file {p.name} -> {dest}")
        return actions

    def _run_housekeeping(self, reason: str) -> dict:
        """Full housekeeping pass. Idempotent, never deletes. Writes the
        housekeeping.json manifest (push data: state written back tidily)."""
        started = _utc_now()
        actions: list[str] = []
        errors: list[str] = []
        try:
            actions += self._ensure_awino_layout()
        except Exception as e:  # noqa: BLE001
            errors.append(f"layout: {type(e).__name__}")
        snap = self._write_contract_snapshot()
        if snap:
            actions.append(snap)
        try:
            actions += self._export_journal()
        except Exception as e:  # noqa: BLE001
            errors.append(f"journal: {type(e).__name__}")
        cfg = self._read_config()
        try:
            actions += self._archive_tool_results(
                cfg["housekeeping"]["retention_days"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"tool_results: {type(e).__name__}")
        try:
            actions += self._tidy_awino_root()
        except Exception as e:  # noqa: BLE001
            errors.append(f"tidy_root: {type(e).__name__}")
        entry = {
            "at": started, "reason": reason,
            "actions": actions, "errors": errors,
        }
        manifest_path = self.workspace / ".awino" / "housekeeping.json"
        manifest = {"schema_version": _HOUSEKEEPING_SCHEMA_VERSION,
                    "runs": []}
        if manifest_path.is_file():
            try:
                raw = json.loads(manifest_path.read_text()[:65536])
                if isinstance(raw, dict) and isinstance(
                        raw.get("runs"), list):
                    manifest["runs"] = raw["runs"][-49:]
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        manifest["runs"].append(entry)
        # Git commit (opt-in) happens BEFORE writing the manifest, so the
        # manifest records whether the commit happened.
        if cfg["housekeeping"]["git_commit"]:
            self._maybe_git_commit(entry)
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return entry

    def _maybe_git_commit(self, entry: dict) -> None:
        """Opt-in only (default OFF): commit .awino/ so mission state
        travels with the project. Never surprise-commits."""
        try:
            ws = self.workspace
            r = subprocess.run(
                ["git", "-C", str(ws), "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0 or "true" not in r.stdout:
                return
            subprocess.run(["git", "-C", str(ws), "add", ".awino"],
                           capture_output=True, timeout=30)
            r2 = subprocess.run(
                ["git", "-C", str(ws), "status", "--porcelain", ".awino"],
                capture_output=True, text=True, timeout=10)
            if not r2.stdout.strip():
                return  # nothing to commit
            # Restrict commit to .awino/: never sweep up unrelated staged
            # changes the operator may have staged.
            r3 = subprocess.run(
                ["git", "-C", str(ws), "commit", "--", ".awino", "-m",
                 f"awino: housekeeping ({entry['reason']}) — "
                 f"{len(entry['actions'])} actions"],
                capture_output=True, text=True, timeout=30)
            if r3.returncode == 0:
                entry["actions"].append("git-committed .awino/ (opt-in)")
            else:
                entry.setdefault("errors", []).append(
                    f"git commit failed: {r3.stderr[:200]}")
        except Exception:  # noqa: BLE001 - fail-closed, housekeeping continues
            entry.setdefault("errors", []).append("git commit failed")

    def _cmd_housekeeping(self, args: dict) -> dict:
        entry = self._run_housekeeping(args.get("reason", "manual"))
        return {"ok": True, "housekeeping": entry}

    def _cmd_done(self, args: dict) -> dict:
        result = self.loop.request_done()
        # Only housekeep on actual mission close, not if the request was
        # refused/rejected (e.g., criteria not met, no mission).
        if result.get("status") not in ("refused", "rejected", "none"):
            self._run_housekeeping("mission-close")
        return result

    def _maybe_housekeep_on_phase(self, result: dict) -> None:
        """Automatic housekeeping on stage transitions."""
        try:
            phase = self.loop.state.snapshot.get("phase")
        except Exception:  # noqa: BLE001
            return
        if phase and phase != self._last_housekept_phase:
            self._last_housekept_phase = phase
            try:
                self._run_housekeeping("stage-transition")
            except Exception:  # noqa: BLE001 - never break a turn
                pass

    # ------------------------------------------------------- MCP registration
    def _register_mcp_servers(self, specs: list) -> None:
        """Spawn configured MCP servers and register their tools.

        Tools enter TOOL_DEFS/MODES like native tools, so the contract's
        offered/consequential computation, the pre-execute check, and the
        approval gate all apply unchanged. A server that fails to start (or
        lists no tools) is recorded in _mcp_status; hello still succeeds.
        """
        for c in self._mcp_clients:  # re-hello: close the old ones
            c.close()
        self._mcp_clients = []
        self._mcp_status = []
        if not isinstance(specs, list):
            return
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            name = spec.get("name")
            command = spec.get("command")
            if not name or not command:
                self._mcp_status.append(
                    {"name": name, "ok": False,
                     "error": "mcp server needs 'name' and 'command'"})
                continue
            trust_ro = bool(spec.get("trustReadOnlyHint"))
            try:
                client = McpClient(str(name), str(command),
                                   [str(a) for a in spec.get("args") or []],
                                   spec.get("env"))
                client.initialize()
                tools = client.list_tools()
            except Exception as e:  # noqa: BLE001 - fail-closed per server
                self._mcp_status.append(
                    {"name": name, "ok": False,
                     "error": f"{type(e).__name__}: {e}"})
                continue
            registered = []
            for t in tools:
                if not isinstance(t, dict):
                    continue
                tname = str(t.get("name") or "")
                hname = re.sub(r"_+", "_",
                               re.sub(r"[^a-z0-9_]", "_",
                                      f"mcp_{name}_{tname}".lower())).strip("_")
                if not tname or hname in TOOL_DEFS \
                        or hasattr(self.loop.sandbox, hname):
                    registered.append({"tool": tname, "ok": False,
                                       "error": f"name collision: {hname!r}"})
                    continue
                ro_hint = (t.get("annotations") or {}).get("readOnlyHint") \
                    is True
                # Safe default: every MCP tool is consequential
                # (approval-gated). Only when the operator explicitly set
                # trustReadOnlyHint AND the server claims readOnlyHint does
                # a tool skip approval — and then it is read-only anyway.
                consequential = not (trust_ro and ro_hint)
                schema = t.get("inputSchema") or {}
                TOOL_DEFS[hname] = {
                    "consequential": consequential,
                    "args": list((schema.get("properties") or {}).keys()),
                }
                offer_modes = list(MODES) if (trust_ro and ro_hint) \
                    else ["build", "verify"]
                for m in offer_modes:
                    if hname not in MODES[m]["tools"]:
                        MODES[m]["tools"].append(hname)
                setattr(self.loop.sandbox, hname,
                        _make_mcp_handler(client, str(name), tname))
                registered.append({"tool": tname, "as": hname,
                                   "consequential": consequential,
                                   "readOnlyHint": ro_hint,
                                   "modes": offer_modes})
            self._mcp_clients.append(client)
            self._mcp_status.append({"name": name, "ok": True,
                                     "tools": registered})

    # ------------------------------------------------- contract augmentation
    def _awino_dir(self) -> Path:
        return self.workspace / ".awino"

    def _ensure_registry(self):
        """Attach the file-backed registry when not already attached.

        Returns the registry, or None when attachment failed (the failure is
        recorded in self._registry_attach_error, never swallowed). Never
        raises.
        """
        reg = getattr(self.loop, "registry", None)
        if reg is not None:
            return reg
        try:
            from registry import Registry
            awd = self._awino_dir()
            awd.mkdir(parents=True, exist_ok=True)
            reg = Registry(awd)
            reg.ensure()
            self.loop.registry = reg
            return reg
        except Exception as e:  # noqa: BLE001 - never break callers
            self._registry_attach_error = f"{type(e).__name__}: {e}"
            return None

    def sidecar_sections(self) -> str:
        """Operator-owned sections appended to every compiled contract."""
        parts = [self._provider_section(), self._mode_section()]
        ctx = self._read_context()
        if ctx:
            parts.append("## OPERATOR CONTEXT (operator-maintained, "
                         "from .awino/)\n" + ctx)
        skills = self._project_skills_section()
        if skills:
            parts.append(skills)
        # Track B: registry state loads into the contract on every turn.
        reg = getattr(self.loop, "registry", None)
        if reg is not None:
            try:
                section = reg.contract_section()
                if section:
                    parts.append(section)
            except Exception:
                pass
        return "\n\n".join(p for p in parts if p)

    def _read_context(self) -> str:
        d = self._awino_dir()
        items: list[tuple[str, Path]] = []
        main = d / "context.md"
        if main.is_file():
            items.append(("context.md", main))
        cdir = d / "context"
        order: list = []
        of = d / "context.json"
        if of.is_file():
            try:
                order = json.loads(of.read_text()).get("order", []) or []
            except (json.JSONDecodeError, ValueError, AttributeError):
                order = []
        seen: set[str] = set()
        if cdir.is_dir():
            for n in order:
                p = cdir / n
                if isinstance(n, str) and p.is_file() and n not in seen:
                    items.append((f"context/{n}", p))
                    seen.add(n)
            for p in sorted(cdir.glob("*.md")):
                if p.name not in seen:
                    items.append((f"context/{p.name}", p))
        chunks = []
        total = 0
        for title, p in items:
            try:
                text = p.read_text()[:4000]
            except OSError:
                continue
            chunk = f"### {title}\n{text}"
            if total + len(chunk) > 12000:
                chunks.append("(context truncated at 12KB cap)")
                break
            chunks.append(chunk)
            total += len(chunk)
        return "\n\n".join(chunks)

    def _project_skills_section(self) -> str:
        reg = self.loop.state.dir / "skills"
        if not reg.is_dir():
            return ""
        try:
            store = open_registry(reg)
        except SkillIntegrityError as e:
            return ("## PROJECT SKILLS\n(project skill registry FAILED "
                    f"integrity check: {e}; no project skills loaded)")
        names = store.names()
        if not names:
            return ""
        parts = ["## PROJECT SKILLS (user-admitted, sha256-verified)"]
        total = 0
        for n in names:
            try:
                body = store.get_verified(n)
            except SkillIntegrityError as e:
                parts.append(f"(skill {n!r} failed integrity check "
                             f"and was skipped: {e})")
                continue
            digest = store.pinned_hash(n) or "?"
            chunk = f"### {n} (sha256: {digest})\n{body}"
            if total + len(chunk) > 8192:
                parts.append("(project skills truncated at 8KB cap)")
                break
            parts.append(chunk)
            total += len(chunk)
        return "\n".join(parts)

    # ---------------------------------------------------------- user message
    def _do_user_message(self, cmd: dict) -> None:
        text = cmd.get("text")
        if not isinstance(text, str) or not text.strip():
            _err('user_message requires "text" (non-empty string)')
            return
        self._cancel.clear()
        from cancel import CancelToken
        self._turn_token = CancelToken()
        self._turn_out = queue.Queue()
        # Sidecar streaming protocol (spec §3): per-turn opt-in via
        # "stream": true. Absent/false preserves the 0.3.0 event set
        # byte-identically (old clients simply never send it).
        use_stream = bool(cmd.get("stream"))
        self.loop.sidecar_emit = _emit if use_stream else None
        self.loop.sidecar_turn_meta = (
            self._stream_turn_meta if use_stream else None)
        # Identity snapshot: _for_turn below must be the context THIS turn
        # created, not a stale one (early-return turns never run _pipeline).
        stream_before = self.loop._turn_stream
        # Scope #5: compaction check happens BEFORE the turn starts. If the
        # ~0.85 threshold is hit, the sidecar emits compaction_proposed and
        # pauses for the user's approval (normal approve/deny commands).
        # Auto-compaction never silently drops context.
        self._maybe_compact_before_turn()
        self._worker = threading.Thread(
            target=self._run_turn_thread, args=(text,), daemon=True)
        self._worker.start()
        # Pump stdin while the turn runs: cancel is honored, everything else
        # waits its turn (deferred) so the Loop is never touched concurrently.
        while self._worker.is_alive():
            try:
                inner = self.inbox.get(timeout=0.2)
            except queue.Empty:
                continue
            if inner is None:
                self.deferred.append(None)
                continue
            if isinstance(inner, dict) and inner.get("cmd") == "cancel":
                self._cancel.set()
                if self._turn_token is not None:
                    self._turn_token.set("operator stop")
            else:
                self.deferred.append(inner)
        result = self._turn_out.get()
        self._worker = None
        if self._cancel.is_set():
            self._cancel.clear()
            dropped = sum(1 for _ in list(self.deferred)
                          if isinstance(_, dict)
                          and _.get("cmd") == "user_message")
            self.deferred = collections.deque(
                c for c in self.deferred
                if not (isinstance(c, dict)
                        and c.get("cmd") == "user_message"))
            # The turn already ran to completion inside the harness (state
            # persisted, journal intact); the UI just doesn't render it.
            _emit({"event": "cancel_ack", "accepted": True,
                   "note": ("in-flight turn completed and was discarded; "
                            "effects already executed remain in the journal. "
                            f"dropped {dropped} queued message(s).")})
            return
        # _for_turn: the streamed turn's context (thinking + checks) belongs
        # to this result even when the result carries no turn_id (e.g. a
        # turn paused for approval). None for non-streamed turns, and None
        # when this turn never reached the pipeline (identity unchanged).
        fresh_stream = self.loop._turn_stream if use_stream else None
        self._emit_turn_result(
            result,
            _for_turn=(fresh_stream if fresh_stream is not None
                       and fresh_stream is not stream_before else None))
        if isinstance(result, dict) and result.get("status") == \
                "awaiting_approval":
            self._emit_approvals(result)

    def _stream_turn_meta(self, turn_id: str, phase: str,
                          mode_id: str) -> dict:
        """Refine a streamed turn_start with sidecar-owned metadata: the
        mode source (operator overlay vs stage default) and the active
        persona. Key material never appears here."""
        info = self._active_mode_info()
        source = "overlay" if info.get("source") == "overlay" else "stage"
        return {"mode": {"id": mode_id or info.get("id"), "source": source},
                "persona": self._persona_info()}

    def _emit_turn_result(self, result: dict, _for_turn=None) -> None:
        """turn_result enriched with the live mode/persona/binding identity
        the turn ran under (UI mode chip + auditability).

        Step 3 (spec §3.1): streamed turns additionally carry the
        accumulated thinking trace and the finalized harness checks, so a
        client that never saw the deltas still gets everything in one
        event. Non-streamed turns keep the 0.3.0 shape byte-identically:
        no "thinking"/"checks" keys are added.
        """
        if isinstance(result, dict):
            result = dict(result)
            result["active_mode"] = self._active_mode_info()
            result["persona"] = self._persona_info()
            result["provider"] = {"provider": self.provider,
                                  "model": self.model_desc}
            tctx = (_for_turn if _for_turn is not None
                    else getattr(self.loop, "_turn_stream", None))
            tid = result.get("turn_id")
            if tctx is not None and (_for_turn is not None
                                     or tid == tctx.turn_id):
                # _for_turn: the just-completed streamed turn in
                # _do_user_message (its result may legitimately lack a
                # turn_id, e.g. awaiting_approval). Otherwise the context
                # must belong to this result's turn.
                result["thinking"] = tctx.thinking_text()
                result["checks"] = [dict(c) for c in tctx.checks]
        _emit({"event": "turn_result", "result": result})
        # Scope #5: automatic housekeeping on stage transitions.
        self._maybe_housekeep_on_phase(result)

    def _run_turn_thread(self, text: str) -> None:
        try:
            result = self.loop.run_user_turn(text,
                                             cancel_token=self._turn_token)
        except Exception as e:  # noqa: BLE001 - fail-closed turn result
            traceback.print_exc(file=sys.stderr)
            result = {"status": "error",
                      "said": f"sidecar internal error: "
                              f"{type(e).__name__}: {e}"}
        self._turn_out.put(result)

    def _emit_approvals(self, result: dict) -> None:
        pending = {a["id"]: a for a in
                   self.loop.state.snapshot.get("approvals", [])
                   if a["status"] == "pending"}
        approvals = []
        for aid in result.get("approvals", []):
            a = pending.get(aid)
            if not a:
                continue
            item = {"id": aid, "tool": a["tool"], "args": a["args"]}
            if a["tool"] == "write_file":
                diff, old_exists = _write_diff(
                    self.loop.sandbox, a["args"].get("path", ""),
                    a["args"].get("content", ""))
                item["diff"] = diff
                item["old_exists"] = old_exists
            elif a["tool"] == "patch_file":
                # The patch IS the diff: show it directly so the operator
                # reviews exactly what will be applied.
                item["diff"] = a["args"].get("diff", "")
                item["old_exists"] = True
                # Native diff review: the full proposed content, computed
                # without touching disk, so the extension can open a
                # vscode.diff editor (current file <-> proposed content).
                edit = self.loop._compute_delegated_edit(
                    "patch_file", a["args"])
                if not edit.get("error"):
                    item["proposed_content"] = edit["content"]
            if a.get("shell_targets") is not None:
                # approval-target visibility: resolved file targets and the
                # out-of-workspace flag, rendered on the approval card
                item["shell_targets"] = a["shell_targets"]
            approvals.append(item)
        _emit({"event": "approval_requested",
               "turn_id": result.get("turn_id"),
               "approvals": approvals})

    # --------------------------------------------------------------- approve
    def _do_approve(self, cmd: dict) -> None:
        aid = cmd.get("id")
        decision = cmd.get("decision")
        if not isinstance(aid, str) or decision not in ("approve", "deny"):
            _err('approve requires "id" (string) and "decision" '
                 '("approve"|"deny")')
            return
        try:
            if decision == "approve":
                result = self.loop.approve(aid)
            else:
                result = self.loop.deny(aid)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            result = {"status": "error",
                      "said": f"sidecar internal error: "
                              f"{type(e).__name__}: {e}"}
        self._emit_turn_result(result)
        # v0.6: approving/denying resumes the recursive loop, which may pause
        # for approval AGAIN in a later round. The client needs the new
        # approval_requested event (with IDs) to act on it — without this,
        # the operator sees "awaiting approval" but gets no approval card.
        # Mirrors the _do_user_message post-emit.
        if isinstance(result, dict) and result.get("status") == \
                "awaiting_approval":
            self._emit_approvals(result)

    # --------------------------------------------------------------- command
    def _say(self, kind: str, text: str) -> None:
        """Queue a human-readable sidecar message for the chat UI.

        Command handlers run inside _do_command, which emits exactly one
        "command_result" event per command. _say buffers the message; the
        dispatcher merges buffered messages into the result dict's "said"
        (and "says") fields, so clients always get one event per command
        and nothing the harness says is lost.
        """
        self._pending_says.append({"kind": kind, "message": text})

    def _do_command(self, cmd: dict) -> None:
        name = cmd.get("name")
        args = cmd.get("args") or {}
        # Request id: echoed back in command_result so the client can route
        # concurrent same-name commands to the right waiter instead of
        # matching on the command name alone. Absent on old clients — the
        # extension falls back to name matching when no id is echoed.
        req_id = cmd.get("id")
        if not isinstance(name, str) or not isinstance(args, dict):
            _err('command requires "name" (string) and "args" (object)')
            return
        handlers = {
            "mission": self._cmd_mission,
            "status": self._cmd_status,
            "contract": self._cmd_contract,
            "approve-contract": self._cmd_approve_contract,
            "done": self._cmd_done,
            "rollback": self._cmd_rollback,
            "learnings": self._cmd_learnings,
            "housekeeping": self._cmd_housekeeping,
            "journal": lambda a: {"journal": self.loop.effect_journal()},
            "events": lambda a: {"events": [
                {"seq": e.get("seq"), "type": e.get("type"),
                 "data": e.get("data", {})}
                for e in self.loop.state.events[-50:]  # last 50, redacted
            ]},
            "synthesize": self._cmd_synthesize,
            "seeds_list": lambda a: {"seeds": self._list_seeds()},
            "mission_from_seed": self._cmd_mission_from_seed,
            "seed_save": self._cmd_seed_save,
            "bootstrap": self._cmd_bootstrap,
            "registry_audit": self._cmd_registry_audit,
            "mode": self._cmd_role_mode,
            "plan": self._cmd_plan,
            "verify_begin": self._cmd_verify_begin,
            "verify_turn": self._cmd_verify_turn,
            "verify_complete": self._cmd_verify_complete,
            "context_list": lambda a: {"files": self._context_files()},
            "context_add": self._cmd_context_add,
            "context_set": self._cmd_context_set,
            "context_remove": self._cmd_context_remove,
            "context_reorder": self._cmd_context_reorder,
            "skills_list": self._cmd_skills_list,
            "skill_add": self._cmd_skill_add,
            "tasks_list": self._cmd_tasks_list,
            "session_resume": self._cmd_session_resume,
            "mode_list": self._cmd_mode_list,
            "mode_invoke": self._cmd_mode_invoke,
            "mode_dismiss": self._cmd_mode_dismiss,
            "persona_assume": self._cmd_persona_assume,
            "persona_dismiss": self._cmd_persona_dismiss,
            "env_switch": self._cmd_env_switch,
            "rigor_report": self._cmd_rigor_report,
            "revert_checkpoint": self._do_revert_checkpoint,
        }
        fn = handlers.get(name)
        if fn is None:
            _emit({"event": "command_result", "name": name, "id": req_id,
                   "ok": False,
                   "result": {"error": f"unknown command {name!r}"}})
            return
        self._pending_says = []
        try:
            result = fn(args)
            ok = not (isinstance(result, dict)
                      and result.get("status") in ("refused", "error",
                                                   "rejected", "stale",
                                                   "none"))
        except Exception as e:  # noqa: BLE001 - fail-closed
            traceback.print_exc(file=sys.stderr)
            result = {"error": f"{type(e).__name__}: {e}"}
            ok = False
        # Merge any _say() messages queued by the handler into the result:
        # the chat UI reads "said"; structured clients can read "says".
        if self._pending_says and isinstance(result, dict):
            extra = "\n".join(s["message"] for s in self._pending_says)
            prev = result.get("said")
            result["said"] = (extra + "\n" + prev) if prev else extra
            result["says"] = self._pending_says
        self._pending_says = []
        _emit({"event": "command_result", "name": name, "id": req_id, "ok": ok,
               "result": result})

    def _cmd_mission(self, args: dict) -> dict:
        text = args.get("text", "")
        criteria = args.get("criteria") or ["manual"]
        if not isinstance(text, str) or not text.strip():
            return {"status": "error", "said": "mission needs text"}
        if not isinstance(criteria, list) or not all(
                isinstance(c, str) for c in criteria):
            return {"status": "error", "said": "criteria must be strings"}
        return self._set_mission(text.strip(), criteria)

    def _cmd_approve_contract(self, args: dict) -> dict:
        scope = args.get("scope")
        if scope is not None and not isinstance(scope, list):
            return {"status": "error", "said": "scope must be a list"}
        return self.loop.approve_contract(scope)

    def _cmd_rollback(self, args: dict) -> dict:
        seq = args.get("seq")
        if not isinstance(seq, int):
            return {"status": "error", "said": "rollback needs int seq"}
        return self.loop.rollback(seq)

    def _cmd_learnings(self, args: dict) -> dict:
        snap = self.loop.state.snapshot
        return {"learnings": snap.get("learnings", []),
                "flags": snap.get("flags", [])[-10:]}

    def _cmd_synthesize(self, args: dict) -> dict:
        idx = args.get("index", -1)
        if not isinstance(idx, int):
            return {"status": "refused", "code": "bad_index",
                    "detail": "index must be an int"}
        return self.loop.synthesize_learning(idx)

    # ---------------------------------------------------------------- seeds
    def _list_seeds(self) -> list[dict]:
        d = self._awino_dir() / "seeds"
        seeds = []
        if d.is_dir():
            for p in sorted(d.glob("*.md")):
                seeds.append(_parse_seed_file(p))
        return seeds

    def _cmd_mission_from_seed(self, args: dict) -> dict:
        name = args.get("name", "")
        seed = next((s for s in self._list_seeds()
                     if s.get("name") == name or s.get("file") == name
                     or s.get("file", "").rsplit(".", 1)[0] == name), None)
        if seed is None:
            return {"status": "error",
                    "said": f"no seed {name!r} (see seeds_list)"}
        if seed.get("error"):
            return {"status": "error",
                    "said": f"seed {seed.get('file')!r} invalid: "
                            f"{seed['error']}"}
        return self._set_mission(seed["objective"], seed["criteria"])

    def _set_mission(self, text: str, criteria: list) -> dict:
        try:
            m = self.loop.set_mission(text, criteria)
        except (ValueError, SkillIntegrityError) as e:
            return {"status": "error", "said": f"mission refused: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"status": "error",
                    "said": f"mission failed: {type(e).__name__}: {e}"}
        # Track A + B: project bootstrap + memory registry attach, on every
        # mission start. Bootstrap is idempotent and never raises; registry
        # continuity survives persona/mode changes (it lives on the project).
        self._bootstrap_and_registry(text, criteria)
        # Story ledger: the registry attaches above (after set_mission), so
        # the stale-story rule runs here — a new mission arriving while
        # stories are doing/open journals `stale_stories` and the session
        # surfaces "we started this new thing, but X is still open —
        # what's up?" before proceeding. Skipped when loop.set_mission
        # already fired it (registry was attached from a previous mission).
        stale_warning = m.get("stale_warning") or ""
        if not stale_warning:
            try:
                from story import check_stale_on_mission
                reg = getattr(self.loop, "registry", None)
                if reg is not None:
                    stale_warning = check_stale_on_mission(reg.awino_dir)
                    if stale_warning:
                        self.loop.state.record("stale_stories",
                                               {"mission_id": m["id"],
                                                "warning": stale_warning})
            except Exception:
                pass
        # A new mission clears mission-scoped mode overlays and any active
        # persona: both were invoked for the previous mission's context.
        if self._mode_overlay and self._mode_overlay.get("scope") == "mission":
            self.loop.state.record("mode_changed", {
                "mode": self._stage_default_mode("DEFINE")["id"],
                "previous": self._mode_overlay["mode"],
                "reason": "new mission"})
            self._mode_overlay = None
        if self._persona:
            self.loop.state.record("persona_expired", {
                "skill": self._persona["skill"],
                "sha256": self._persona["sha256"],
                "reason": "new mission"})
            self._persona = None
        result = {"status": "ok", "mission": {"id": m["id"], "kind": m["kind"],
                                            "revision": m["revision"]}}
        if stale_warning:
            result["stale_warning"] = stale_warning
            result["said"] = (f"Mission set. STALE STORIES: {stale_warning}")
        return result

    def _bootstrap_and_registry(self, text: str, criteria: list) -> None:
        """Track A + B: run the startup checklist, attach the venv and the
        memory registry, import seed checklist tasks, journal the result.
        Never raises — bootstrap failures become journal breadcrumbs."""
        from bootstrap import run_startup_checklist
        from registry import Registry
        ws = self.workspace
        try:
            report = run_startup_checklist(ws, mission_text=text,
                                           criteria=criteria)
        except Exception as e:  # noqa: BLE001 — absolute last resort
            self.loop.state.record("bootstrap_failed",
                                   {"error": f"{type(e).__name__}: {e}"})
            return
        # Track A: point run_command at the project venv (if bootstrap made
        # or found one) so commands use the venv python automatically.
        if report.get("venv_bin"):
            self.loop.sandbox.venv_bin = Path(report["venv_bin"])
        # Track B: auto-create the registry on first mission; seed checklist
        # tasks import into the tracker; every item journals as evidence.
        reg = Registry(ws / ".awino")
        reg.ensure()
        self.loop.registry = reg
        try:
            n = reg.import_seed_tasks(report.get("seed_tasks", []))
        except Exception:
            n = 0
        # Story ledger: file every seed under its story (`story:`
        # frontmatter, or the Inbox story) — never orphaned. Mirrors
        # full_init_flow so the chat path and the CLI path cannot drift.
        try:
            from story import file_seeds
            file_seeds(ws / ".awino", report.get("seed_tasks", []))
        except Exception:
            pass
        # Track D: route the role lens from the mission text + the
        # configured profile (project.yaml). Surfaced in the contract with
        # its reason; the user can override with the `mode` command.
        # Track F: seed the initial task DAG from the role's decomposition
        # playbook (idempotent — skips when tasks already exist).
        try:
            import modes as _modes
            from bootstrap import read_project_yaml
            prof = _modes.DEFAULT_ROLE
            try:
                prof = read_project_yaml(ws / ".awino" / "project.yaml"
                                         ).get("profile") or prof
            except Exception:
                pass
            proposal = self.loop.route_role(mission_text=text,
                                            configured_profile=prof,
                                            force=True)
            role = proposal.get("role", prof)
            mid = (self.loop.state.snapshot.get("mission") or {}).get("id", "")
            dag_tasks = _modes.compile_initial_dag(reg, text, role, mid)
            lines_extra = (f"role lens: {role} ({proposal.get('source')}; "
                           f"{proposal.get('reason', '')[:100]})")
            self.loop.state.record("dag_compiled",
                                   {"role": role, "tasks": len(dag_tasks),
                                    "mission_id": mid})
            self.loop.state.persist_snapshot()
        except Exception as e:  # noqa: BLE001 — routing never breaks bootstrap
            lines_extra = f"role/DAG skipped: {type(e).__name__}"
            dag_tasks = []
        lines = ["project bootstrap:"]
        for c in report["checks"]:
            lines.append(f"- [{c['status']}] {c['name']}: {c['detail']}")
        lines.append(f"seed tasks imported into registry: {n}")
        lines.append(lines_extra)
        lines.append(f"initial DAG tasks: {len(dag_tasks)}")
        fails = [c for c in report["checks"] if c["status"] == "fail"]
        warns = [c for c in report["checks"] if c["status"] == "warn"]
        self.loop.state.record("bootstrap_complete", {
            "ok": report["ok"],
            "checks": [(c["name"], c["status"]) for c in report["checks"]],
            "venv_bin": report.get("venv_bin"),
            "seed_tasks_imported": n,
            "breadcrumbs": report.get("breadcrumbs", []),
        })
        for crumb in report.get("breadcrumbs", []):
            try:
                reg.add_breadcrumb((self.loop.state.snapshot.get("mission")
                                    or {}).get("id", "?"),
                                   f"bootstrap: {crumb}")
            except Exception:
                pass
        lines = ["project bootstrap:"]
        for c in report["checks"]:
            lines.append(f"- [{c['status']}] {c['name']}: {c['detail']}")
        lines.append(f"seed tasks imported into registry: {n}")
        if warns or fails:
            lines.append("warnings/failures: " + "; ".join(
                f"{c['name']}: {c['detail']}" for c in warns + fails))
        self._say("bootstrap", "\n".join(lines))

    def _cmd_seed_save(self, args: dict) -> dict:
        name = args.get("name", "")
        if not isinstance(name, str) or not _SEED_NAME_RX.fullmatch(name):
            return {"status": "error",
                    "said": "seed_save needs a sane 'name' "
                            "(letters/digits/space/_/-)"}
        m = self.loop.state.snapshot.get("mission")
        if not m:
            return {"status": "error", "said": "no mission to save as seed"}
        objective = " ".join(str(m["text"]).split())
        crit = _serialize_criteria(m["done_criteria"])
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "seed"
        seeds_dir = self._awino_dir() / "seeds"
        seeds_dir.mkdir(parents=True, exist_ok=True)
        p = seeds_dir / f"{slug}.md"
        existed = p.exists()
        lines = ["---", f"name: {name}", f"objective: {objective}",
                 "criteria:"]
        lines += [f"  - {c}" for c in crit] or ["  - manual"]
        lines += ["---", ""]
        p.write_text("\n".join(lines))
        self.loop.state.record("seed_saved",
                               {"name": name, "file": p.name,
                                "overwrote": existed})
        # Track B: seed_save also registers tasks — the seed becomes a task
        # in the registry tracker so progress on it is tracked. The caller
        # is told whether registration succeeded; a silent failure here
        # used to read as a clean save.
        reg = self._ensure_registry()
        task_registered = False
        registry_error: str | None = None
        if reg is not None:
            try:
                reg.add_task(f"execute seed '{name}' ({p.name})",
                             source=f"seed:{slug}", state="open")
                task_registered = True
            except Exception as e:  # noqa: BLE001 — log, don't silently fail
                registry_error = f"{type(e).__name__}: {e}"
                import sys
                print(f"seed_save: add_task failed: {registry_error}",
                      file=sys.stderr)
        else:
            registry_error = (getattr(self, "_registry_attach_error", None)
                              or "no registry attached (loop.registry is None)")
            import sys
            print(f"seed_save: {registry_error}", file=sys.stderr)
        self.loop.state.persist_snapshot()
        return {"status": "ok", "seed": p.name, "overwrote": existed,
                "task_registered": task_registered,
                "registry_error": registry_error}

    def _cmd_bootstrap(self, args: dict) -> dict:
        """Re-run the project startup checklist on demand (Track A)."""
        from bootstrap import run_startup_checklist
        report = run_startup_checklist(self.workspace)
        if report.get("venv_bin"):
            self.loop.sandbox.venv_bin = Path(report["venv_bin"])
        return {"status": "ok",
                "checks": report["checks"],
                "ok": report["ok"],
                "venv_bin": report.get("venv_bin"),
                "breadcrumbs": report.get("breadcrumbs", [])}

    def _cmd_registry_audit(self, args: dict) -> dict:
        """Report every registry belief as fact/assumption/outdated (Track B)."""
        reg = getattr(self.loop, "registry", None)
        if reg is None or not reg.exists:
            return {"status": "error",
                    "said": "no registry yet — start a mission first"}
        audit = reg.audit()
        self._say("registry_audit", audit["summary"])
        return {"status": "ok", **audit}

    # -------------------------------- Track D/F/G: role, plan, verification
    def _cmd_role_mode(self, args: dict) -> dict:
        """User override of the role lens. `mode` with no role reports."""
        import modes as _modes
        role = (args.get("role") or "").strip()
        if not role:
            cur = self.loop.active_role() or {}
            return {"status": "ok", "role": cur.get("role"),
                    "reason": cur.get("reason"), "source": cur.get("source"),
                    "available": list(_modes.ROLE_IDS)}
        res = self.loop.set_role_mode(role, args.get("reason", "user override"),
                                      source="override")
        if res.get("status") == "ok":
            self._say("mode", f"role lens: {role} — {res.get('reason')}")
        return res

    def _cmd_plan(self, args: dict) -> dict:
        """Show the mission's task DAG simply: next, blocked, by what."""
        reg = getattr(self.loop, "registry", None)
        if reg is None or not reg.exists:
            return {"status": "error",
                    "said": ("no registry yet — start a mission first; the "
                             "task DAG is compiled at mission start")}
        nxt = reg.whats_next(limit=10)
        blocked = reg.unblock_report()
        try:
            order = reg.topological_order()
        except ValueError as e:
            order = [f"cycle: {e}"]
        lines = ["task DAG:"]
        lines.append(f"- progress: {reg.dag_summary()}")
        lines.append("- what's next (unblocked):")
        for t in nxt:
            lines.append(f"  - {t['text'][:80]} (id: {t['id']})")
        if not nxt:
            lines.append("  (nothing unblocked — all remaining work is blocked "
                         "or done)")
        if blocked:
            lines.append("- blocked, and by what:")
            for b in blocked[:10]:
                by = ", ".join(x["text"][:50] for x in b["blocked_by"])
                lines.append(f"  - {b['task']['text'][:80]} blocked by: {by}")
        self._say("plan", "\n".join(lines))
        return {"status": "ok", "next": [t["id"] for t in nxt],
                "blocked": [{"id": b["task"]["id"],
                             "by": [x["id"] for x in b["blocked_by"]]}
                            for b in blocked],
                "order": order}

    def _cmd_verify_begin(self, args: dict) -> dict:
        """Spawn the verifier worker (Track G)."""
        return self.loop.begin_verification()

    def _cmd_verify_turn(self, args: dict) -> dict:
        """Run the verifier worker's turn.

        args: {evidence_links: {criterion: path}, recipe_result: {...}}
        Defaults: project_root = this workspace.
        """
        ctx = {"evidence_links": args.get("evidence_links", {}),
               "recipe_result": args.get("recipe_result"),
               "project_root": str(self.workspace)}
        res = self.loop.run_verifier_turn(args.get("worker_id", ""), ctx)
        if res.get("status") == "ok":
            verdict = res.get("verdict", [])
            yes = sum(1 for e in verdict if e.get("accomplished") == "yes")
            self._say("verify_turn",
                     f"verifier verdict: {yes}/{len(verdict)} evidenced "
                     f"({'PASS' if res.get('passed') else 'FAIL'})")
        return res

    def _cmd_verify_complete(self, args: dict) -> dict:
        """Collect the verifier verdict; pass unlocks REVIEW, fail -> BUILD."""
        return self.loop.complete_verification(args.get("worker_id", ""))

    # --------------------------------------------------------------- context
    def _context_files(self) -> list[dict]:
        d = self._awino_dir()
        files = []
        main = d / "context.md"
        if main.is_file():
            files.append({"name": "main", "path": "context.md",
                          "chars": main.stat().st_size})
        cdir = d / "context"
        order: list = []
        of = d / "context.json"
        if of.is_file():
            try:
                order = json.loads(of.read_text()).get("order", []) or []
            except (json.JSONDecodeError, ValueError, AttributeError):
                order = []
        seen: set[str] = set()
        if cdir.is_dir():
            for n in order:
                p = cdir / n
                if isinstance(n, str) and p.is_file() and n not in seen:
                    files.append({"name": n, "path": f"context/{n}",
                                  "chars": p.stat().st_size})
                    seen.add(n)
            for p in sorted(cdir.glob("*.md")):
                if p.name not in seen:
                    files.append({"name": p.name,
                                  "path": f"context/{p.name}",
                                  "chars": p.stat().st_size})
        return files

    def _context_path(self, name: str) -> Path | None:
        if not isinstance(name, str) or not _CTX_NAME_RX.fullmatch(name):
            return None
        d = self._awino_dir()
        if name == "main":
            return d / "context.md"
        fname = name if name.endswith(".md") else name + ".md"
        return d / "context" / fname

    def _cmd_context_add(self, args: dict) -> dict:
        p = self._context_path(args.get("name", ""))
        content = args.get("content", "")
        if p is None or not isinstance(content, str):
            return {"status": "error",
                    "said": "context_add needs 'name' and 'content'"}
        if p.exists():
            return {"status": "error",
                    "said": f"context {p.name!r} exists; use context_set"}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content[:20000])
        return {"status": "ok", "path": str(p.relative_to(self.workspace))}

    def _cmd_context_set(self, args: dict) -> dict:
        p = self._context_path(args.get("name", ""))
        content = args.get("content", "")
        if p is None or not isinstance(content, str):
            return {"status": "error",
                    "said": "context_set needs 'name' and 'content'"}
        if not p.exists():
            return {"status": "error",
                    "said": f"context {p.name!r} does not exist"}
        p.write_text(content[:20000])
        return {"status": "ok", "path": str(p.relative_to(self.workspace))}

    def _cmd_context_remove(self, args: dict) -> dict:
        p = self._context_path(args.get("name", ""))
        if p is None:
            return {"status": "error", "said": "context_remove needs 'name'"}
        if not p.exists():
            return {"status": "error",
                    "said": f"context {p.name!r} does not exist"}
        p.unlink()
        return {"status": "ok", "removed": p.name}

    def _cmd_context_reorder(self, args: dict) -> dict:
        order = args.get("order")
        if not isinstance(order, list) or not all(
                isinstance(n, str) for n in order):
            return {"status": "error",
                    "said": "context_reorder needs 'order': [names]"}
        cdir = self._awino_dir() / "context"
        unknown = [n for n in order if not (cdir / n).is_file()]
        if unknown:
            return {"status": "error",
                    "said": f"unknown context files: {unknown}"}
        of = self._awino_dir() / "context.json"
        of.parent.mkdir(parents=True, exist_ok=True)
        of.write_text(json.dumps({"order": order}, indent=1))
        return {"status": "ok", "order": order}

    # ---------------------------------------------------------------- skills
    def _cmd_skills_list(self, args: dict) -> dict:
        packaged = [{"name": n,
                     "sha256": self.loop.skill_store.pinned_hash(n)}
                    for n in self.loop.skill_store.names()]
        reg = self.loop.state.dir / "skills"
        project: list[dict] = []
        if reg.is_dir():
            try:
                store = open_registry(reg)
            except SkillIntegrityError as e:
                return {"packaged": packaged,
                        "error": f"project registry integrity failure: {e}"}
            project = [{"name": n, "sha256": store.pinned_hash(n)}
                       for n in store.names()]
        return {"packaged": packaged, "project": project}

    def _cmd_skill_add(self, args: dict) -> dict:
        """Admit a user-provided skill file into the project registry.

        Pipeline (the real machinery, no shortcuts): injection screen ->
        VERIFY checks parsed and executed in the sandbox -> sha256 pin in
        the project registry manifest -> hash-verified load. Anything else
        is refused and recorded.
        """
        name = args.get("name", "")
        path = args.get("path", "")
        refused = lambda code, detail: {  # noqa: E731
            "status": "refused", "code": code, "detail": detail}
        if not isinstance(name, str) or not _SKILL_NAME_RX.fullmatch(name):
            return refused("bad_name",
                           "skill name must match [a-z0-9][a-z0-9_-]{1,40}")
        p = Path(path).expanduser() if isinstance(path, str) else None
        if p is None or not p.is_file():
            return refused("bad_path", f"skill file not found: {path!r}")
        try:
            body = p.read_text()
        except OSError as e:
            return refused("bad_path", f"unreadable: {e}")
        if len(body) > 65536:
            return refused("too_large", "skill file over 64KB")
        try:
            screen_learning(body)
        except SynthesisRefused as e:
            self._record_skill_refused(name, e.code, e.detail)
            return refused(e.code, e.detail)
        checks = _parse_checks(body)
        if not checks:
            detail = ("no VERIFY: checks found; unverified prose is never "
                      "admitted as a skill")
            self._record_skill_refused(name, "unverified", detail)
            return refused("unverified", detail)
        results = run_checks(checks, self.loop.sandbox)
        failed = [r for r in results if not r.get("passed")]
        if failed:
            detail = ("sandbox verification failed: "
                      + "; ".join(str(r.get("check")) for r in failed))
            self._record_skill_refused(name, "checks_failed", detail)
            return refused("checks_failed", detail)
        registry = self.loop.state.dir / "skills"
        try:
            digest = admit_skill(name, body, registry,
                                 packaged=self.loop.skill_store)
            # Prove the pin: reload through the hash-verified store.
            open_registry(registry).get_verified(name)
        except SynthesisRefused as e:
            self._record_skill_refused(name, e.code, e.detail)
            return refused(e.code, e.detail)
        except SkillIntegrityError as e:
            self._record_skill_refused(name, "integrity", str(e))
            return refused("integrity", f"admitted skill failed to "
                                        f"verify on reload: {e}")
        self.loop.state.record("skill_admitted",
                               {"name": name, "sha256": digest,
                                "checks": len(results), "via": "skill_add"})
        self.loop.state.persist_snapshot()
        return {"status": "admitted", "name": name, "sha256": digest,
                "checks": len(results)}

    def _record_skill_refused(self, name: str, code: str, detail: str) -> None:
        self.loop.state.record("skill_refused",
                               {"name": name, "code": code, "detail": detail,
                                "via": "skill_add"})
        self.loop.state.persist_snapshot()

    def _cmd_tasks_list(self, args: dict) -> dict:
        """Read-only: tasks from the harness registry tracker (Track B).

        Returns exactly what the registry believes — states change only
        through registry.set_task_state in code (verified completion), never
        from the UI. Empty when no registry is attached yet (no mission
        started in this project). When the hello-time registry re-attach
        failed, `attached` is False and `error` names the failure so the UI
        can show "registry failed to load" instead of a misleadingly empty
        task list."""
        reg = getattr(self.loop, "registry", None)
        if reg is None:
            return {"tasks": [], "attached": False,
                    "error": getattr(self, "_registry_attach_error", None)}
        try:
            tasks = reg.tasks()
        except Exception as e:  # noqa: BLE001 - read path never breaks chat
            return {"tasks": [], "attached": True,
                    "error": f"{type(e).__name__}: {e}"}
        out = []
        for t in tasks:
            out.append({
                "id": t.get("id"), "text": t.get("text"),
                "state": t.get("state"), "source": t.get("source"),
                "done_criteria": t.get("done_criteria", ""),
                "depends_on": list(t.get("depends_on", [])),
                "evidence": list(t.get("evidence", [])),
            })
        return {"tasks": out, "attached": True}

    def _cmd_session_resume(self, args: dict) -> dict:
        """Read-only session-focus summary, reconstructed from real state.

        Mission, phase, verified done criteria, last progress deltas, next
        expected action — all from loop.status() — plus the registry's last
        stop point and recent milestones when a registry is attached.
        Nothing is written: this is a pure reconstruction for the chat view
        to render on open or on demand."""
        st = self.loop.status()
        crit = st.get("criteria", []) or []
        verified = [c for c in crit if c.get("ok")]
        summary = {
            "mission": st.get("mission"),
            "phase": st.get("phase"),
            "mission_revision": st.get("mission_revision", 0),
            "criteria_total": len(crit),
            "criteria_verified": len(verified),
            "verified_labels": [c.get("label") for c in verified],
            "last_progress": st.get("progress", []) or [],
            "next_action": st.get("next_action"),
            "turns": st.get("turns", 0),
            "last_stop_point": "",
            "recent_milestones": [],
        }
        reg = getattr(self.loop, "registry", None)
        mission = self.loop.state.snapshot.get("mission") or {}
        if reg is not None and mission.get("id"):
            try:
                summary["last_stop_point"] = reg.last_stop_point(mission["id"])
                ms = reg.milestones()
                summary["recent_milestones"] = [
                    {"kind": m.get("kind"), "text": m.get("text")}
                    for m in ms[-5:]
                ]
            except Exception:  # noqa: BLE001 - read path never breaks chat
                pass
        return summary

    def _cmd_contract(self, args: dict) -> dict:
        # The compiled contract block, including operator context and
        # project-skill sections (same patched compiler the turns use).
        return {"contract": loop_module.compile_contract(self.loop.state)}

    # ---------------------------------------------------------------- cancel
    def _do_cancel(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            # The pump loop (above) will see this flag; the in-flight model
            # HTTP call cannot be interrupted, only its result discarded.
            # v0.6: the CancelToken reaches the loop's round checkpoints
            # and run_command's poll loop, so the turn halts cooperatively.
            self._cancel.set()
            if self._turn_token is not None:
                self._turn_token.set("operator stop")
        else:
            _emit({"event": "cancel_ack", "accepted": False,
                   "note": "no turn in flight"})

    # ------------------------------------------------------------- shutdown
    def _shutdown(self) -> None:
        self.alive = False
        for c in self._mcp_clients:
            c.close()
        self._mcp_clients = []
        _emit({"event": "bye"})


def main() -> None:
    # The sidecar ships with its own Python (bundled runtime) — it must
    # never create a project .venv. Bootstrap's ensure_venv checks this flag
    # and skips creation (returns a warning instead of hanging for 120s on
    # `python -m venv`, whose ensurepip stalls on Windows).
    os.environ["AWINO_SIDECAR"] = "1"
    # stderr (tracebacks, diagnostics) is captured into host logs: redact
    # high-confidence secrets at this boundary too.
    sys.stderr = _RedactingStderr(sys.stderr)
    Sidecar().run()


if __name__ == "__main__":
    main()
