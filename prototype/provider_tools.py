"""Provider-native tool-call normalization (design doc section 2.3).

One internal representation: contract.ToolCall(name, args). This module
translates between that and each provider's native tool-calling dialect:

- to_provider(provider, schemas): schemas -> provider-native definitions
- from_provider(provider, raw_calls): native calls -> normalized ToolCalls
- results_to_provider(provider, calls): normalized results -> native messages

Providers: "openai" | "ollama" (OpenAI-compatible), "anthropic", "bedrock".

from_provider raises NormalizationError on bad JSON arguments or unknown
structure — the loop turns that into harness-rejection feedback, never a
crash. Unknown tool NAMES are not dropped here; they are validated against
TOOL_DEFS + the offered set by merge_native_calls, which reports them as
validation errors.
"""

import copy
import json

from tools import TOOL_DEFS

_PROVIDERS = ("openai", "ollama", "anthropic", "bedrock")


class NormalizationError(ValueError):
    """A provider's tool-call payload could not be normalized."""


def _require_provider(provider: str) -> str:
    p = (provider or "").lower()
    if p not in _PROVIDERS:
        raise NormalizationError(f"unknown provider for tool normalization: {provider!r}")
    return p


def to_provider(provider: str, schemas: list[dict]) -> list[dict]:
    """Translate provider-agnostic schema dicts to provider-native tool defs.

    Each schema: {"name", "description", "parameters"} (see tool_schema.py).
    """
    p = _require_provider(provider)
    out = []
    for s in schemas:
        name, desc, params = s["name"], s.get("description", ""), s.get("parameters", {})
        if p in ("openai", "ollama"):
            out.append({"type": "function",
                        "function": {"name": name, "description": desc,
                                     "parameters": copy.deepcopy(params)}})
        elif p == "anthropic":
            out.append({"name": name, "description": desc,
                        "input_schema": copy.deepcopy(params)})
        else:  # bedrock
            out.append({"toolSpec": {"name": name, "description": desc,
                                     "inputSchema": {"json": copy.deepcopy(params)}}})
    return out


def _coerce_args(args) -> dict:
    """Native tool arguments -> {str: JSON scalar}. Reject lists/dicts.

    Mirrors contract.coerce_turn_contract's arg rule so native and JSON
    envelope calls share one idempotency hash and one journal shape.
    """
    if not isinstance(args, dict):
        raise NormalizationError(
            f"tool arguments must be an object, got {type(args).__name__}")
    for k, v in args.items():
        if not isinstance(k, str):
            raise NormalizationError("tool argument keys must be strings")
        if isinstance(v, bool) or v is None or isinstance(v, (str, int, float)):
            continue
        raise NormalizationError(
            f"tool argument {k!r} must be a JSON scalar, got {type(v).__name__}")
    return dict(args)


def from_provider(provider: str, raw_calls: list) -> list[tuple[str, dict]]:
    """Normalize provider-native tool calls to [(name, args)].

    - openai/ollama: message["tool_calls"][*]["function"] -> json.loads(arguments)
    - anthropic: content blocks type=="tool_use" -> {"name","input"}
    - bedrock: content blocks "toolUse" -> {"name","input"}
    Returns (name, args) pairs; the caller builds contract.ToolCall.
    native_ids() recovers the provider's call ids for result correlation.
    """
    p = _require_provider(provider)
    out: list[tuple[str, dict]] = []
    if p in ("openai", "ollama"):
        calls = raw_calls or []
        for c in calls:
            try:
                fn = c["function"]
                name = fn["name"]
                raw_args = fn.get("arguments", "{}")
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except (KeyError, TypeError, json.JSONDecodeError) as ex:
                raise NormalizationError(f"bad openai tool_call: {ex}") from ex
            out.append((name, _coerce_args(args)))
    elif p == "anthropic":
        for b in raw_calls or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            try:
                name, args = b["name"], b.get("input", {})
            except KeyError as ex:
                raise NormalizationError(f"bad anthropic tool_use block: {ex}") from ex
            out.append((name, _coerce_args(args)))
    else:  # bedrock
        for b in raw_calls or []:
            tu = b.get("toolUse") if isinstance(b, dict) else None
            if not isinstance(tu, dict):
                continue
            try:
                name, args = tu["name"], tu.get("input", {})
            except KeyError as ex:
                raise NormalizationError(f"bad bedrock toolUse block: {ex}") from ex
            out.append((name, _coerce_args(args)))
    return out


def native_ids(provider: str, raw_calls: list) -> list[str | None]:
    """Recover provider-native call ids parallel to from_provider's output."""
    p = _require_provider(provider)
    ids: list[str | None] = []
    if p in ("openai", "ollama"):
        for c in raw_calls or []:
            ids.append(c.get("id") if isinstance(c, dict) else None)
    elif p == "anthropic":
        for b in raw_calls or []:
            ids.append(b.get("id") if isinstance(b, dict)
                       and b.get("type") == "tool_use" else None)
    else:  # bedrock
        for b in raw_calls or []:
            tu = b.get("toolUse") if isinstance(b, dict) else None
            ids.append(tu.get("toolUseId") if isinstance(tu, dict) else None)
    return ids


def results_to_provider(provider: str,
                        calls: list[tuple[str | None, str, dict]]) -> list[dict]:
    """(native_call_id, tool_name, normalized_result_summary) -> native messages.

    The summary is the harness-rendered tool-result text (already truncated
    by the loop). Tool output is untrusted data: it travels in tool messages
    only, never inside the contract block.
    """
    p = _require_provider(provider)
    out = []
    for nid, _name, summary in calls:
        text = summary if isinstance(summary, str) else json.dumps(summary, default=str)
        if p in ("openai", "ollama"):
            out.append({"role": "tool", "tool_call_id": nid, "content": text})
        elif p == "anthropic":
            out.append({"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": nid,
                                     "content": text}]})
        else:  # bedrock
            out.append({"role": "user",
                        "content": [{"toolResult": {"toolUseId": nid,
                                                   "content": {"text": text}}}]})
    return out


def merge_native_calls(turn: dict, native: list[tuple[str, dict]],
                       offered: list[str]) -> list[str]:
    """Merge normalized native calls into the turn dict's tool_calls.

    Unknown names (not in TOOL_DEFS) or names outside the offered set become
    validation error strings (never silent drops); the caller feeds them to
    the harness-rejection path. Returns the error list (empty = clean).
    """
    errs: list[str] = []
    calls = turn.setdefault("tool_calls", [])
    for name, args in native:
        if name not in TOOL_DEFS:
            errs.append(f"unknown tool: {name}")
        elif name not in offered:
            errs.append(f"tool '{name}' not offered in this mode (offered: {offered})")
        else:
            calls.append({"name": name, "args": args})
    return errs
