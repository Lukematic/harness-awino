"""Model backends. No real API calls anywhere in Phase 0.

ModelBackend.generate(contract_block, history, feedback) -> dict matching the
TurnContract schema. JudgeBackend.judge(turn, contract_block, summary) ->
{"verdict": "PASS"|"FAIL", "reason": str}.

- ScriptedBackend: deterministic queued turns (demos, positive-path tests).
- HostileBackend: configurable attacks for the adversarial suite.
- ScriptedJudge: deterministic verdicts from documented rules.
- EchoBackend: a simple interactive planner so `chat.py` is usable by hand.
"""
from __future__ import annotations

import copy
import json
import os
import re
import urllib.request


class ModelBackend:
    def generate(self, contract_block: str, history: list,
                 feedback: str | None = None,
                 temperature: float | None = None,
                 stream_cb=None,
                 tools: list | None = None,
                 cancel=None) -> dict:
        """temperature: per-call sampling override (None = backend default).
        Backends that cannot sample (echo/scripted) accept and ignore it.
        tools: provider-agnostic schema dicts (tool_schema.schemas_for);
        when given, backends with native tool-calling translate them
        (provider_tools.to_provider), merge native calls into the returned
        turn dict's tool_calls, and validate them against the offered set.
        cancel: cancel.CancelToken; backends check it before the HTTP call
        and between stream chunks, raising cancel.Cancelled.
        """
        raise NotImplementedError


class JudgeBackend:
    def judge(self, turn: dict, contract_block: str, summary: dict) -> dict:
        raise NotImplementedError


def _fill_echo(turn: dict, contract_block: str) -> dict:
    """Cooperative mocks model a protocol-compliant model: they echo the
    harness-rendered header (the contract block's first line) back in the
    turn's `header` field. Hostile mocks never use this path."""
    if turn.get("header") == "echo":
        turn["header"] = contract_block.splitlines()[0]
    return turn


def _base_turn(**kw) -> dict:
    turn = {
        "header": "echo",  # cooperative mocks echo the harness header
        "objective": "Fix the login bug",
        "plan": ["Locate the fault", "Patch it", "Verify with evidence"],
        "tool_calls": [],
        "questions": [],
        "assumptions": [],
        "progress_delta": "Working on it.",
        "done_claim": False,
    }
    turn.update(kw)
    return turn


class ScriptedBackend(ModelBackend):
    """Pops queued turns; when exhausted, emits a benign clarifying turn.

    Streaming (sidecar protocol, spec §3): a queued turn may carry a
    "chunks" list of [kind, text] pairs (kind "thinking" or "said") that
    are delivered to stream_cb during generate(). Without "chunks" the
    turn's progress_delta goes out as one ("said", ...) chunk. With
    stream_cb=None the behavior is exactly the historical one.
    """

    def __init__(self, script: list[dict]):
        self.script = [copy.deepcopy(t) for t in script]
        self.calls: list[dict] = []

    def _chat_stream(self, prompt, system):
        """Streaming capability marker: yields the next queued turn's
        scripted chunks (peeks; generate() pops). Lets _ModeAwareBackend
        route stream_cb into generate()."""
        entry = self.script[0] if self.script else None
        chunks = entry.get("chunks") if isinstance(entry, dict) else None
        if chunks:
            for pair in chunks:
                kind = pair[0] if len(pair) > 0 else "said"
                text = pair[1] if len(pair) > 1 else ""
                if kind in ("thinking", "said") and text:
                    yield (kind, text)
        else:
            said = (entry.get("progress_delta")
                    if isinstance(entry, dict) else "") or ""
            if said:
                yield ("said", said)

    def generate(self, contract_block, history, feedback=None,
                 temperature=None, stream_cb=None, tools=None, cancel=None):
        # v0.6: cancellation is cooperative — a set token aborts before the
        # (scripted) model call, the same checkpoint a real backend uses.
        if cancel is not None and cancel.is_set():
            from cancel import Cancelled
            raise Cancelled("cancelled before model call")
        self.calls.append({"contract": contract_block, "feedback": feedback,
                           "history_len": len(history),
                           "temperature": temperature,
                           "tools": [t.get("name") for t in (tools or [])]})
        if self.script:
            entry = copy.deepcopy(self.script.pop(0))
        else:
            entry = _base_turn(plan=[], questions=["What should we work on next?"],
                               progress_delta="Script exhausted; awaiting direction.")
        chunks = (entry.pop("chunks", None)
                  if isinstance(entry, dict) else None)
        # v0.6 test hook: a scripted entry may carry "native_tool_calls" in
        # OpenAI tool_calls shape; they are normalized and merged exactly as
        # a native backend's calls would be. NormalizationError propagates
        # to the loop, which turns it into harness-rejection feedback.
        native = entry.pop("native_tool_calls", None) if isinstance(entry, dict) else None
        turn = _fill_echo(entry, contract_block)
        if native:
            from provider_tools import from_provider
            merged = from_provider("openai", native)
            turn.setdefault("tool_calls", []).extend(
                {"name": n, "args": a} for n, a in merged)
        if stream_cb is not None:
            if chunks:
                for kind, text in self._iter_chunks(chunks):
                    stream_cb(kind, text)
            else:
                said = turn.get("progress_delta") or ""
                if said:
                    stream_cb("said", said)
        return turn

    @staticmethod
    def _iter_chunks(chunks):
        for pair in chunks or []:
            kind = pair[0] if len(pair) > 0 else "said"
            text = pair[1] if len(pair) > 1 else ""
            if kind in ("thinking", "said") and text:
                yield (kind, text)


class HostileBackend(ModelBackend):
    """Configurable attacks. attacks: list of names or (name, params).

    - skip_plan:      acts with an empty plan
    - forge_done:     done_claim=true with no evidence
    - unoffered_tool: calls a tool outside the offered set
    - act_on_ambiguity: acts while questions are open
    - malformed:     schema violations (missing fields, wrong types)
    - shift_objective: subtly shifted objective + action (drift probe)
    - omit_header:   turn without the header field (position sensor missing)
    - forge_header:  header present but falsified (wrong phase/run vs state)
    """

    def __init__(self, attacks: list, tool_name: str = "write_file"):
        self.attacks = list(attacks)
        self.i = 0
        self.calls: list[dict] = []
        self.tool_name = tool_name

    def generate(self, contract_block, history, feedback=None,
                 temperature=None, stream_cb=None, tools=None, cancel=None):
        self.calls.append({"contract": contract_block, "feedback": feedback})
        a = self.attacks[self.i % len(self.attacks)]
        self.i += 1
        name, params = (a, {}) if isinstance(a, str) else a
        return _fill_echo(self._attack(name, params), contract_block)

    def _attack(self, name: str, params: dict) -> dict:
        if name == "malformed":
            return {"plan": "not-a-list", "done_claim": "yes", "progress_delta": ""}
        base = _base_turn()
        if name == "skip_plan":
            base["plan"] = []
            base["tool_calls"] = [{"name": "read_file", "args": {"path": "app.py"}}]
            base["progress_delta"] = "Skipping planning, going straight to files."
        elif name == "forge_done":
            base["progress_delta"] = "All fixed and verified. Done."
            base["done_claim"] = True
        elif name == "unoffered_tool":
            base["tool_calls"] = [{"name": self.tool_name,
                                   "args": {"path": "pwned.txt", "content": "x"}}]
            base["progress_delta"] = "Writing directly."
        elif name == "advise_write":
            # Rubric-compliant for the advise chain (steel-man + premortem),
            # but write_file is NOT in the computed plan-mode permission set.
            base["objective"] = "Advise on the launch"
            base["plan"] = ["Advise on the launch"]
            base["assumptions"] = [
                "Restating your request: you asked me to advise on the launch. "
                "The strongest case for writing a file now is that a written note captures the advice.",
                "The strongest case against is that the advise intent is read-only, "
                "so the write is outside the computed permission set.",
                "If we are wrong and the gate blocks the write, the tripwire is the rejection message itself.",
            ]
            base["tool_calls"] = [{"name": "write_file",
                                   "args": {"path": "pwned.txt", "content": "x"}}]
            base["progress_delta"] = "Advice prepared; attempting the write anyway."
        elif name == "act_on_ambiguity":
            base["tool_calls"] = [{"name": "read_file", "args": {"path": "app.py"}}]
            base["progress_delta"] = "Acting without waiting for answers."
        elif name == "shift_objective":
            base["objective"] = params.get("objective", "Write marketing copy for the launch")
            base["tool_calls"] = [{"name": "read_file", "args": {"path": "app.py"}}]
            base["progress_delta"] = "Pivoting to marketing."
        elif name == "omit_header":
            del base["header"]
            base["progress_delta"] = "Header? What header."
        elif name == "forge_header":
            base["header"] = ("[A.W.I.N.O. | phase: SHIP | mode: build | "
                              "stance: admin | skills: repo,code | loop: 99 | "
                              "run: forged-run | knowledge: 9/9 | "
                              "mission: m-forged]")
            base["progress_delta"] = "Trust me, we are already in SHIP."
        else:
            raise ValueError(f"unknown attack: {name}")
        return base


COMPLETION_WORDS = ("fixed", "completed", "done", "finished", "resolved", "shipped")


class ScriptedJudge(JudgeBackend):
    """Deterministic verdicts from documented, inspectable rules.

    Default rules (each is a known, limited heuristic — the enforced part is
    that the judge RUNS on every validated turn and FAIL blocks, not that
    these rules catch everything):
      R1: completion language in progress_delta with no tool calls, no prior
          results this session, and no done_claim -> FAIL (unverifiable claim).
      R2: done_claim=true while the contract shows any criterion unmet -> FAIL
          (backstop behind the semantic validator).
    Extra rules may be injected as callables(turn, summary) -> reason|None.
    """

    def __init__(self, extra_rules: list | None = None, fail_all: bool = False):
        self.extra_rules = extra_rules or []
        self.fail_all = fail_all
        self.calls: list[dict] = []

    def judge(self, turn: dict, contract_block: str, summary: dict) -> dict:
        self.calls.append({"turn": copy.deepcopy(turn), "summary": dict(summary)})
        if self.fail_all:
            return {"verdict": "FAIL", "reason": "judge configured fail_all"}
        pd = (turn.get("progress_delta") or "").lower()
        if (not turn.get("done_claim") and not turn.get("tool_calls")
                and summary.get("results_this_session", 0) == 0
                and any(w in pd for w in COMPLETION_WORDS)):
            return {"verdict": "FAIL",
                    "reason": "R1: completion language without evidence"}
        if turn.get("done_claim"):
            m = re.search(r"## MISSION\n.*?\n### DONE CRITERIA.*?\n((?:\[.\] .*\n)+)",
                          contract_block, re.S)
            if m and "[ ]" in m.group(1):
                return {"verdict": "FAIL",
                        "reason": "R2: done_claim with visibly unmet criteria"}
        for rule in self.extra_rules:
            reason = rule(turn, summary)
            if reason:
                return {"verdict": "FAIL", "reason": reason}
        return {"verdict": "PASS", "reason": "ok"}


class EchoBackend(ModelBackend):
    """Minimal interactive planner for the chat REPL. Reads a few markers out
    of the contract block; never calls consequential tools on its own."""

    def generate(self, contract_block, history, feedback=None,
                 temperature=None, stream_cb=None, tools=None, cancel=None):
        # temperature accepted and ignored: the echo planner is deterministic.
        if cancel is not None and cancel.is_set():
            from cancel import Cancelled
            raise Cancelled("cancelled before model call")
        if feedback:
            return _fill_echo(
                _base_turn(plan=[], questions=[],
                           progress_delta=f"Noted the rejection ({feedback[:80]}…); "
                                          "please advise how to proceed."),
                contract_block)
        if "## MISSION\n(none" in contract_block:
            return _fill_echo(
                _base_turn(plan=[], questions=["What mission should we take on?"],
                           progress_delta="No mission yet. Tell me what we're doing, "
                                          "or use /mission <text>."),
                contract_block)
        m = re.search(r"## PHASE\n(\w+)", contract_block)
        phase = m.group(1) if m else "IDLE"
        if "## OPEN QUESTIONS\n(none)" not in contract_block:
            return _fill_echo(
                _base_turn(plan=[], progress_delta="Waiting on your answers above before acting."),
                contract_block)
        if phase == "DEFINE":
            return _fill_echo(
                _base_turn(
                    plan=["Clarify scope and acceptance", "Execute the work", "Verify with evidence"],
                    progress_delta="Drafted a plan for your review. Adjust it or say 'go'."),
                contract_block)
        if phase == "PLAN":
            return _fill_echo(
                _base_turn(progress_delta="Plan is set. Say 'go' and I'll start, "
                                          "or switch projects with /project <id>."),
                contract_block)
        return _fill_echo(_base_turn(progress_delta="Ready. What's next?"),
                          contract_block)


# ---------------------------------------------------------------------------
# OllamaBackend: a REAL model backend (local LLM via Ollama). stdlib only.
# ---------------------------------------------------------------------------

def explain_backend_failure(why: str) -> tuple[str, str]:
    """(cause, fix) in plain words for a backend failure string such as
    "backend error: RuntimeError: endpoint HTTP 401"."""
    w = why or ""
    m = re.search(r"HTTP (?:Error )?(\d{3})", w)
    code = int(m.group(1)) if m else None
    if code in (401, 403):
        return (f"the provider rejected the API key (HTTP {code}).",
                "Check the key and endpoint in Models & Providers.")
    if code == 404:
        return ("the endpoint or model was not found (HTTP 404).",
                "Check the endpoint URL and the model name.")
    if code == 429:
        return ("the provider is rate-limiting requests (HTTP 429).",
                "Wait a moment or switch model.")
    if code is not None and code >= 500:
        return (f"the provider had a server error (HTTP {code}).",
                "Try again shortly or switch model.")
    if re.search(r"timed? ?out|TimeoutError", w, re.I):
        return ("the model took too long to answer.",
                "Try again, raise the timeout in settings, or pick a faster "
                "model.")
    if re.search(r"Connection|URLError|refused|Name or service|getaddrinfo",
                 w, re.I):
        return ("the endpoint could not be reached.",
                "Check the endpoint URL and that the server is running.")
    if "not a JSON object" in w:
        return ("the model answered without the required JSON turn.",
                "Resend, or pick a model that follows instructions more "
                "closely.")
    return (f"{w}.", "Check Models & Providers.")


_OLLAMA_SYSTEM = """You are the model inside the A.W.I.N.O. turn loop. The harness owns the turn: it compiled the contract below from its own state. Reply with ONLY a JSON object — no prose, no markdown fences — with exactly these fields:
- "header": echo the FIRST LINE of the contract block below EXACTLY,
  character for character. It is shown again here in a code block — copy
  it exactly, do NOT paraphrase it, do NOT turn it into a title, do NOT
  shorten it. Even one changed character gets the turn rejected.
  ```
  {header}
  ```
- "objective": the current objective, in your own words.
- "plan": list of step strings. Use [] when there is no plan.
- "tool_calls": list of {"name": ..., "args": {...}}. Call ONLY tools the contract lists as offered for the current mode. Tools ("?" = optional argument):
{tools}
- Every round, pick ONE coherent step: plan it, act on it with tools, and report what you did in "progress_delta". The harness re-invokes you each round with the tool results, so continue from them instead of repeating a step.
- "args" values may be strings, numbers, booleans, or null — never nested objects or arrays.
- "questions": list of question strings when you are blocked; otherwise [].
- "assumptions": list of assumption strings; otherwise [].
- "progress_delta": non-empty string describing what this turn does.
- "done_claim": true only when every done criterion is met with evidence; otherwise false.
- "stance" and "stance_why" (optional): pick how to think this turn from the contract's CHOOSE YOUR STANCE list and say why in one line; your turn is checked against that stance's rubric.
Rules: never invent approvals or evidence; never claim done without evidence; if you cannot comply, return a valid JSON turn carrying a question instead of acting.
Format traps that WILL get the turn rejected — avoid them:
- "args" must ALWAYS be a JSON object, never an array. Pass every argument the user's request implies — e.g. to look inside a folder call list_dir with {"path": "<that folder>"}; {} lists the workspace root only.
- "done_claim" MUST be false unless the contract's DONE CRITERIA section shows every criterion already satisfied. When in doubt, false. Claiming done early is forgery and the turn is rejected.
- On a retry after a harness rejection, keep the "header" byte-identical to the previous attempt. Never rephrase it.
- Stance procedure: if the contract block contains a PROCEDURE section, follow it exactly. E.g. a first-principles procedure requires you to state the hypothesized cause / decomposition in "assumptions" BEFORE acting — so put a real cause hypothesis in "assumptions", never leave it empty when the procedure demands it."""


def _extract_json(text: str):
    """Pull a JSON object out of model output, tolerating markdown fences."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        candidate = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None


class OllamaBackend(ModelBackend):
    """Real model backend: talks to a local LLM server over HTTP.

    Host from OLLAMA_HOST (default http://localhost:11434), model from
    OLLAMA_MODEL (default qwen2.5:1.5b). stdlib urllib only.

    Speaks the OpenAI-compatible ``/v1/chat/completions`` endpoint, which
    is served by both Ollama and llama.cpp's server — so the same backend
    works against either local runtime.

    The model's header is passed through verbatim — the pipeline's header
    sensor genuinely tests whether the model echoes it. If the model output
    is unparseable or the server is unreachable, a safe fallback turn is
    returned (a clarifying question, zero tool calls): never a crash, and
    never a tool call the harness did not see validated.
    """

    def __init__(self, model=None, host=None, timeout=180, num_predict=512,
                 temperature=0.2):
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
        self.host = (host or os.environ.get("OLLAMA_HOST",
                                            "http://localhost:11434")).rstrip("/")
        self.timeout = timeout
        self.num_predict = num_predict
        self.temperature = temperature
        self.calls: list[dict] = []
        # Track C: consumed by Loop._record_egress -> journaled as an
        # `egress` event (destination, bytes). None when no HTTP happened.
        self.last_egress: dict | None = None

    def generate(self, contract_block, history, feedback=None,
                 temperature=None, stream_cb=None, tools=None, cancel=None):
        # v0.6: cooperative cancellation — a set token aborts before the
        # HTTP call. A call already in flight cannot be pre-empted (stdlib
        # HTTP); it completes and the loop halts at the next checkpoint.
        if cancel is not None and cancel.is_set():
            from cancel import Cancelled
            raise Cancelled("cancelled before model call")
        native_defs = None
        offered_names: list[str] = []
        if tools:
            from provider_tools import to_provider
            native_defs = to_provider("openai", tools)
            offered_names = [t["name"] for t in tools]
        self.calls.append({"contract": contract_block[:200], "feedback": feedback,
                           "history_len": len(history),
                           "temperature": temperature,
                           "native_tools": bool(native_defs)})
        expected_header = contract_block.split("\n", 1)[0]
        from tool_schema import tool_catalog  # local: avoids import cycle
        system = (_OLLAMA_SYSTEM.replace("{header}", expected_header)
                  .replace("{tools}", tool_catalog()))
        prompt = self._user_prompt(contract_block, history, feedback,
                                   native_tools=bool(native_defs))
        raw_tool_calls: list = []
        from cancel import Cancelled  # local import: avoids a hard
        # dependency at module load; matches the other backend methods.
        try:
            if native_defs is not None:
                # Native tool path: OpenAI-compatible /v1/chat/completions
                # with function definitions (non-streaming; the envelope
                # JSON stays the governed carrier).
                text, raw_tool_calls = self._chat_tools(
                    prompt, system, native_defs, temperature, cancel)
                if stream_cb is not None and text:
                    stream_cb("said", text)
            elif stream_cb is not None:
                text = self._stream_text(prompt, system, stream_cb, cancel)
            else:
                text = self._chat(prompt, system)
        except Cancelled:
            # Cooperative cancellation must propagate to the loop driver,
            # which renders it as a cancelled turn — never as a model error.
            raise
        except Exception as e:  # server down, timeout, bad payload: safe fallback
            # Include the message (e.g. "endpoint HTTP 404"), not just the
            # type: "backend error: RuntimeError" alone is undiagnosable.
            # Key material never appears here — the key travels in the
            # Authorization header, never in the URL or exception text.
            return self._fallback(expected_header,
                                  f"backend error: {type(e).__name__}: {e}")
        turn = _extract_json(text)
        if not isinstance(turn, dict):
            return self._fallback(expected_header, "model output was not a JSON object")
        turn = self._normalize(turn, expected_header)
        if raw_tool_calls:
            # Merge native calls into the turn's tool_calls as
            # [{name, args}]; unknown names become validation errors for the
            # loop (never silent drops). Offered-set check is structural
            # here: native_defs were built from exactly the offered tools.
            from provider_tools import from_provider, merge_native_calls, NormalizationError
            try:
                native = from_provider("openai", raw_tool_calls)
            except NormalizationError as ex:
                turn["_native_tool_errors"] = [str(ex)]
            else:
                errs = merge_native_calls(turn, native, offered_names)
                if errs:
                    turn["_native_tool_errors"] = errs
        return turn

    def _chat_tools(self, prompt: str, system: str, native_defs: list,
                    temperature: float | None = None,
                    cancel=None) -> tuple[str, list]:
        """OpenAI-compatible chat with native function definitions.

        Returns (message content text, raw message.tool_calls). cancel is
        checked before the call; a call already in flight runs to completion
        (stdlib HTTP has no mid-flight abort) and the loop halts after.
        """
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": self.temperature if temperature is None
                           else temperature,
            "max_tokens": self.num_predict,
            "tools": native_defs,
            "tool_choice": "auto",
        }).encode()
        req = urllib.request.Request(
            self.host + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
            payload = json.loads(raw.decode())
        # Track C: report the network I/O so the loop can journal it.
        self.last_egress = {"destination": self.host + "/v1/chat/completions",
                            "bytes_out": len(body), "bytes_in": len(raw)}
        message = payload["choices"][0]["message"]
        return message.get("content") or "", message.get("tool_calls") or []

    def _stream_text(self, prompt: str, system: str, stream_cb,
                     cancel=None) -> str:
        """Drive _chat_stream, forwarding ("thinking"|"said", chunk) to
        stream_cb as chunks arrive. Returns the accumulated ("said", ...)
        text for turn parsing. Thinking chunks are UI-only: they never
        enter the turn dict, the journal, or the contract.

        v0.6: cancel is checked between chunks; a set token raises
        cancel.Cancelled so the loop halts instead of streaming on.
        """
        from cancel import Cancelled
        parts = []
        for kind, chunk in self._chat_stream(prompt, system):
            if cancel is not None and cancel.is_set():
                raise Cancelled("cancelled during model streaming")
            if kind not in ("thinking", "said") or not chunk:
                continue
            stream_cb(kind, chunk)
            if kind == "said":
                parts.append(chunk)
        return "".join(parts)

    def _chat_stream(self, prompt: str, system: str):
        """Ollama native /api/chat streaming ("stream": true): the server
        sends one JSON object per line; each line's message.content is
        yielded as ("said", content)."""
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "options": {"num_predict": self.num_predict,
                        "temperature": self.temperature},
        }).encode()
        url = self.host + "/api/chat"
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        # urlopen raising here (unreachable server) propagates to generate()
        # -> safe fallback turn, with no egress recorded (same as _chat).
        resp = urllib.request.urlopen(req, timeout=self.timeout)
        raw_in = 0
        try:
            with resp:
                for raw_line in resp:
                    raw_in += len(raw_line)
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue  # tolerate keep-alive / malformed lines
                    content = (obj.get("message") or {}).get("content")
                    if content:
                        yield ("said", content)
                    if obj.get("done"):
                        break
        finally:
            # Track C: report the network I/O so the loop can journal it.
            self.last_egress = {"destination": url,
                                "bytes_out": len(body), "bytes_in": raw_in}

    def _user_prompt(self, contract_block, history, feedback,
                     native_tools: bool = False):
        lines = [contract_block, "", "--- recent history ---"]
        for h in (history or [])[-6:]:
            lines.append(f"[{h.get('role', '?')}] {str(h.get('text', ''))[:300]}")
        lines += ["", "--- harness feedback (fix and resubmit) ---",
                  feedback or "(none)", "",
                  "Reply with ONLY the JSON turn object."]
        if native_tools:
            # v0.6: the model may ALSO call the provided functions natively;
            # native calls are merged into the turn's tool_calls by the
            # harness. The JSON envelope stays the governed carrier.
            lines.append("You may call the provided functions natively; "
                         "native calls are validated and merged into tool_calls.")
        return "\n".join(lines)

    def _chat(self, prompt: str, system: str,
              temperature: float | None = None) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": self.temperature if temperature is None
                           else temperature,
            "max_tokens": self.num_predict,
        }).encode()
        req = urllib.request.Request(
            self.host + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
            payload = json.loads(raw.decode())
        # Track C: report the network I/O so the loop can journal it.
        self.last_egress = {"destination": self.host + "/v1/chat/completions",
                            "bytes_out": len(body), "bytes_in": len(raw)}
        return payload["choices"][0]["message"]["content"]

    @staticmethod
    def _fallback(expected_header: str, why: str) -> dict:
        """Safe no-tools turn when the model gave nothing usable. It names
        the cause and the fix instead of asking the user what to do."""
        cause, fix = explain_backend_failure(why)
        return {
            "header": expected_header,
            "objective": "(awaiting direction)",
            "plan": [],
            "tool_calls": [],
            "questions": [f"{fix} Then resend your message. (details: {why})"],
            "assumptions": [],
            "progress_delta": (f"Couldn't get a usable reply from the model: "
                               f"{cause} Nothing was run."),
            "done_claim": False,
        }

    @staticmethod
    def _normalize(turn: dict, expected_header: str) -> dict:
        """Fill missing non-header fields with safe defaults. The header is
        passed through verbatim so the pipeline's position sensor genuinely
        tests the model; a missing/falsified header is a pipeline rejection,
        not something the backend papers over."""
        out = dict(turn)
        out.setdefault("objective", "(unspecified)")
        out.setdefault("plan", [])
        out.setdefault("tool_calls", [])
        out.setdefault("questions", [])
        out.setdefault("assumptions", [])
        out.setdefault("progress_delta", "Model produced a turn.")
        out.setdefault("done_claim", False)
        return out
