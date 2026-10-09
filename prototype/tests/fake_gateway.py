"""Fake OpenAI-compatible gateway: 401 without the right Bearer key; replies
with a TurnContract that echoes the harness header. Logs every request.

Script mode (OpenCode e2e): when the latest user message carries a line
`AWINO_TEST {json}` (or `AWINO_TEST_B64 <base64 of the json>`, which
survives shell and CLI quoting), the gateway plays a scripted model with OpenAI tool
calling instead: {"calls": [{"name", "args"}...]} are emitted one per round
(the n-th call after n tool results), then a text reply. "reply" sets that
text; "on_correction" is the text used when the next user message is an
`[A.W.I.N.O. correction]`. Every request is appended to $FAKE_GATEWAY_LOG
(JSON lines) when set."""
import base64, json, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = os.environ.get("FAKE_GATEWAY_KEY", "test-key")
LOG = []
DIRECTIVE = re.compile(r"^AWINO_TEST (\{.*\})\s*$|AWINO_TEST_B64 ([A-Za-z0-9+/=]+)", re.M)
STANCE = re.compile(r"\[A\.W\.I\.N\.O\. stance: ([^\]]+)\]")
MISSION = re.compile(r"\[A\.W\.I\.N\.O\. mission: ([^\]]+)\]")
CORRECTION = "[A.W.I.N.O. correction]"


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict))
    return ""


def _directive(text):
    m = DIRECTIVE.search(text or "")
    if not m:
        return None
    return json.loads(m.group(1) or base64.b64decode(m.group(2)).decode())


def script_turn(messages):
    """The scripted model's next move: ("call", name, args) or ("text", s).
    None when no directive applies (fall back to the TurnContract reply)."""
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not users:
        return None
    last = users[-1]
    text = _text(messages[last].get("content"))
    d = _directive(text)
    if d is None and CORRECTION in text:
        for i in reversed(users[:-1]):
            prev = _directive(_text(messages[i].get("content")))
            if prev is not None:
                return ("text", prev.get("on_correction", "Corrected."))
        return None
    if d is None:
        return None
    results = [_text(x.get("content")) for x in messages[last + 1:]
               if x.get("role") == "tool"]
    calls = d.get("calls", [])
    if len(results) < len(calls):
        c = calls[len(results)]
        return ("call", c["name"], c.get("args", {}))
    reply = d.get("reply", "AWINO_FAKE_DONE")
    if results:
        reply += "\n" + "\n".join("TOOL_RESULT: " + r.replace("\n", " ")[:400]
                                   for r in results)
    return ("text", reply)


def _chunk(delta, finish=None):
    return {"id": "chatcmpl-fake", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": "fake-model",
            "choices": [{"index": 0, "delta": delta,
                         "finish_reason": finish}]}

def turn_for(prompt_text):
    m = re.search(r"^\[A\.W\.I\.N\.O\. \|[^\n]*\]$", prompt_text, re.M)
    header = m.group(0) if m else ""
    users = re.findall(r'"role": "user", "content": "(.*?)"', prompt_text)
    stance = "advisor" if "stance: advisor" in header else None
    t = {"header": header, "objective": "respond", "plan": [],
         "tool_calls": [], "questions": [], "assumptions": [],
         "progress_delta": "", "done_claim": False}
    if "stance: advisor" in header:
        now = re.search(r"Local time: ([^\n(]+)", prompt_text)
        t["progress_delta"] = "[Certain] It is " + (now.group(1).strip() if now else "unknown") + "."
    elif "list the files" in prompt_text and "prior tool results this turn: 0" in prompt_text.replace("\n", " ") + " prior tool results this turn: 0" * ("## ROUND" not in prompt_text):
        # A read-only tool call, first round only: gives the engine a second
        # round (where per-turn budgets are checked) without looping.
        t["progress_delta"] = "Listing the workspace."
        t["plan"] = ["List the workspace files", "Summarize what is there"]
        t["tool_calls"] = [{"name": "list_dir", "args": {"path": "."}}]
    else:
        t["progress_delta"] = "Asked the one frontier question."
        t["questions"] = ["What outcome should this mission create?"]
    return t

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/__log":  # test-only: the request log, no auth
            return self._send(200, LOG)
        ok = self.headers.get("Authorization") == "Bearer " + KEY
        LOG.append({"path": self.path, "auth": ok, "method": "GET"})
        if not ok: return self._send(401, {"error": "unauthorized"})
        self._send(200, {"data": [{"id": "fake-model"}]})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        ok = self.headers.get("Authorization") == "Bearer " + KEY
        raw = json.dumps(body)
        msgs = body.get("messages", [])
        system = "\n".join(_text(m.get("content")) for m in msgs
                           if m.get("role") == "system")
        users = [_text(m.get("content")) for m in msgs if m.get("role") == "user"]
        entry = {"path": self.path, "auth": ok, "method": "POST",
                 "tools": bool(body.get("tools")), "stream": bool(body.get("stream")),
                 "has_now": "## NOW" in raw,
                 "tool_names": [t.get("function", {}).get("name")
                                for t in body.get("tools") or []],
                 "stance": STANCE.findall(system),
                 "mission": MISSION.findall(system),
                 "last_user": (users[-1] if users else "")[:300],
                 "n_messages": len(msgs)}
        LOG.append(entry)
        if os.environ.get("FAKE_GATEWAY_LOG"):
            with open(os.environ["FAKE_GATEWAY_LOG"], "a") as f:
                f.write(json.dumps(entry) + "\n")
        if not ok: return self._send(401, {"error": "unauthorized"})
        move = script_turn(msgs)
        if move is not None:
            return self._script(move, bool(body.get("stream")))
        text = "\n".join(m.get("content") or "" for m in body.get("messages", []) if isinstance(m.get("content"), str))
        content = json.dumps(turn_for(text))
        if body.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for i in range(0, len(content), 200):
                chunk = {"choices": [{"delta": {"content": content[i:i+200]}}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: " + json.dumps(_chunk({}, "stop")).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n"); return
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    def _script(self, move, stream):
        if move[0] == "call":
            call = {"index": 0, "id": "call_%d" % int(time.time() * 1e6),
                    "type": "function",
                    "function": {"name": move[1],
                                 "arguments": json.dumps(move[2])}}
            first, finish = {"role": "assistant", "tool_calls": [call]}, "tool_calls"
            message = {"role": "assistant", "content": None,
                       "tool_calls": [{k: v for k, v in call.items() if k != "index"}]}
        else:
            first, finish = {"role": "assistant", "content": move[1]}, "stop"
            message = {"role": "assistant", "content": move[1]}
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if not stream:
            return self._send(200, {"id": "chatcmpl-fake", "object": "chat.completion",
                                    "created": int(time.time()), "model": "fake-model",
                                    "choices": [{"index": 0, "message": message,
                                                 "finish_reason": finish}],
                                    "usage": usage})
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        end = _chunk({}, finish)
        end["usage"] = usage
        for c in (_chunk(first), end):
            self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


def start(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    # Standalone for the extension-host harness: print the port, serve forever.
    srv = start(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(srv.server_port, flush=True)
    threading.Event().wait()
