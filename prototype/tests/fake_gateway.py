"""Fake OpenAI-compatible gateway: 401 without the right Bearer key; replies
with a TurnContract that echoes the harness header. Logs every request."""
import json, re, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "test-key"
LOG = []

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
        LOG.append({"path": self.path, "auth": ok, "method": "POST",
                    "tools": bool(body.get("tools")), "stream": bool(body.get("stream")),
                    "has_now": "## NOW" in raw})
        if not ok: return self._send(401, {"error": "unauthorized"})
        text = "\n".join(m.get("content") or "" for m in body.get("messages", []) if isinstance(m.get("content"), str))
        content = json.dumps(turn_for(text))
        if body.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for i in range(0, len(content), 200):
                chunk = {"choices": [{"delta": {"content": content[i:i+200]}}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n"); return
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    # Standalone for the extension-host harness: print the port, serve forever.
    srv = start()
    print(srv.server_port, flush=True)
    threading.Event().wait()
