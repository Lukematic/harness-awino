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
import fnmatch
import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import urllib.error
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
        "propose only when a plan exists and was approved).",
        'Available tools: read_file {"path"}, list_dir {"path"} (relative dir, '
        '"" for root), search_files {"pattern" (regex), "path" (relative dir, '
        'optional), "glob" (filename glob, optional)}, run_command {"cmd"} '
        "(consequential: needs operator approval), write_file "
        '{"path", "content"} (consequential: needs operator approval; propose '
        "only when a plan exists and was approved).",
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
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
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
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
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
            lines.append("manual")
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


def _patched_compile_contract(state, turn_no=1):
    block = _real_compile_contract(state, turn_no=turn_no)
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
_KNOWN_PROVIDERS = ("echo", "ollama", "openai", "anthropic", "scripted")
_BINDING_FIELDS = ("provider", "model", "endpoint", "key_id")
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
         "mission and done criteria are crisp."),
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
                 stream_cb=None):
        """stream_cb(kind, text): optional streaming sink for the sidecar
        protocol (spec §3.2–3.3). kind is "thinking" or "said". Default
        None preserves today's behavior exactly: the inner backend is
        called as before and no chunks are emitted.

        Backends that speak streaming define _chat_stream and accept
        stream_cb in generate(). Base fallback: a backend without
        _chat_stream (echo, hostile, plain scripted) runs its existing
        generate() untouched and the turn's progress_delta goes out as
        one ("said", ...) chunk — those providers keep working with zero
        changes to their code."""
        temperature = self._sidecar._active_temperature()
        inner = self._inner
        if stream_cb is None:
            return inner.generate(
                contract_block, history, feedback=feedback,
                temperature=temperature)
        if hasattr(inner, "_chat_stream"):
            return inner.generate(
                contract_block, history, feedback=feedback,
                temperature=temperature, stream_cb=stream_cb)
        turn = inner.generate(contract_block, history, feedback=feedback,
                              temperature=temperature)
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
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def _err(message: str) -> None:
    _emit({"event": "error", "message": message})


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
        self._worker: threading.Thread | None = None
        self._turn_out: queue.Queue = queue.Queue()
        self._pending_says: list = []  # per-command _say() buffer
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
        try:
            backend, key_status = self._apply_binding(binding, env_cmd)
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
        # Track B: re-attach the file-backed memory registry on (re)connect.
        # The registry persists under <workspace>/.awino/registry/, but a
        # fresh sidecar process starts with loop.registry = None — without
        # this re-attach, tasks_list/seed_save see an empty registry after
        # every reconnect. Never breaks hello.
        try:
            from registry import Registry
            _awd = wsp / ".awino"
            if _awd.is_dir():
                _reg = Registry(_awd)
                _reg.ensure()
                self.loop.registry = _reg
        except Exception:  # noqa: BLE001 - never break hello
            pass
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
        # flow now — the user never has to type `awino init` by hand — and
        # carry the one brief plain-language summary in the ready event.
        # (First-message mission start re-runs the idempotent checklist
        # anyway via _bootstrap_and_registry.) Never breaks hello.
        auto_init_summary = None
        stories_review_lines = None
        try:
            from bootstrap import session_start_auto_init
            auto_init = session_start_auto_init(wsp)
            if auto_init:
                auto_init_summary = auto_init["summary"]
                self.loop.state.record("auto_init", {
                    "ok": auto_init["ok"], "summary": auto_init_summary})
                # Story ledger: the session-start review is mandatory —
                # journal the event and carry the lines in the ready event
                # so the session presents open stories before new work.
                sr = auto_init.get("stories_review")
                if sr:
                    stories_review_lines = sr["lines"]
                    self.loop.state.record("stories_review", {
                        "stories": sr["stories"]})
        except Exception:  # noqa: BLE001 — session start must proceed
            auto_init_summary = None
        _emit({"event": "ready", "protocol": PROTOCOL, "project": project,
               "provider": self.provider, "model": self.model_desc,
               "workspace": str(wsp), "mcp": self._mcp_status,
               "binding": {k: v for k, v in self._binding.items()},
               "modes": self._modes_summary(),
               "active_mode": self._active_mode_info(),
               "auto_init": auto_init_summary,
               "stories_review": stories_review_lines})

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
                   "key_id": None, "environment": None, "source": "global-settings"}
        if binding["provider"] not in _KNOWN_PROVIDERS:
            return {"error": f"unknown provider {binding['provider']!r}"}
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
            "(use echo|ollama|openai|anthropic|scripted)")

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
        pyaml = base / "providers.yaml"
        if not pyaml.is_file():
            pyaml.write_text(_PROVIDERS_YAML_TEMPLATE)
            actions.append("wrote .awino/providers.yaml (template)")
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
            result = self.loop.run_user_turn(text)
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
        }
        fn = handlers.get(name)
        if fn is None:
            _emit({"event": "command_result", "name": name, "ok": False,
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
        _emit({"event": "command_result", "name": name, "ok": ok,
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
        # in the registry tracker so progress on it is tracked.
        reg = getattr(self.loop, "registry", None)
        if reg is not None:
            try:
                reg.add_task(f"execute seed '{name}' ({p.name})",
                             source=f"seed:{slug}", state="open")
            except Exception:
                pass
        self.loop.state.persist_snapshot()
        return {"status": "ok", "seed": p.name, "overwrote": existed}

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
        started in this project)."""
        reg = getattr(self.loop, "registry", None)
        if reg is None:
            return {"tasks": [], "attached": False}
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
            self._cancel.set()
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
    Sidecar().run()


if __name__ == "__main__":
    main()
