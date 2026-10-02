"""Quick path (10-02): the plain agent loop for direct asks — native tool
calls, parallel reads, a diff card per edit, destructive commands ask,
the project's tests run when the model is done, and a failure goes back
to the model."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from backends import ScriptedBackend  # noqa: E402
from common import make_loop  # noqa: E402
from quick import NUDGE, QuickSession, project_rules  # noqa: E402


def session(script, decisions=None):
    loop, _ = make_loop(project="quick")
    (loop.sandbox.root / "app.py").write_text("print('hi')\n")
    events, asked = [], []
    decisions = list(decisions or [])

    def approve(item):
        asked.append(item)
        return decisions.pop(0) if decisions else "approve"

    q = QuickSession(loop, ScriptedBackend(script), events.append, approve)
    return q, loop, events, asked


def call(name, **args):
    return {"name": name, "args": args}


class QuickLoopTest(unittest.TestCase):
    def test_answer_without_tools_ends_the_turn(self):
        q, loop, events, asked = session([{"text": "It prints hi."}])
        r = q.run("what does app.py do?", "q1")
        self.assertEqual((r["status"], r["said"]), ("ok", "It prints hi."))
        self.assertEqual(asked, [])

    def test_edit_asks_once_then_writes(self):
        q, loop, events, asked = session([
            {"text": "Adding it.", "tool_calls": [call(
                "write_file", path="app.py", content="def greet():\n    return 1\n")]},
            {"text": "Added greet()."}])
        r = q.run("add greet", "q1")
        self.assertEqual([a["tool"] for a in asked], ["write_file"])
        self.assertIn("def greet", (loop.sandbox.root / "app.py").read_text())
        self.assertTrue(r["said"].startswith("Added greet()."))
        self.assertIn("No test recipe found", r["said"])

    def test_denied_edit_is_not_written_and_model_is_told(self):
        q, loop, events, asked = session([
            {"tool_calls": [call("write_file", path="app.py", content="x\n")]},
            {"text": "OK, I won't change it."}], decisions=["deny"])
        q.run("change it", "q1")
        self.assertEqual((loop.sandbox.root / "app.py").read_text(), "print('hi')\n")
        tool_msgs = [m for m in q.messages if m["role"] == "tool"]
        self.assertIn("denied", tool_msgs[0]["content"])

    def test_always_allow_skips_later_cards(self):
        q, loop, events, asked = session([
            {"tool_calls": [call("write_file", path="a.txt", content="1\n")]},
            {"tool_calls": [call("write_file", path="b.txt", content="2\n")]},
            {"text": "Both written."}], decisions=["always"])
        q.run("write two files", "q1")
        self.assertEqual(len(asked), 1)
        self.assertTrue((loop.sandbox.root / "b.txt").is_file())

    def test_safe_command_runs_destructive_asks(self):
        q, loop, events, asked = session([
            {"tool_calls": [call("run_command", cmd="ls"),
                            call("run_command", cmd="rm app.py")]},
            {"text": "Listed; did not delete."}], decisions=["deny"])
        q.run("tidy up", "q1")
        self.assertEqual([a["args"]["cmd"] for a in asked], ["rm app.py"])
        self.assertTrue((loop.sandbox.root / "app.py").is_file())

    def test_reads_in_one_step_run_in_parallel(self):
        q, loop, events, asked = session([
            {"tool_calls": [call("read_file", path="app.py")] * 4},
            {"text": "Read."}])
        started = []
        orig = loop._execute_single

        def slow(*a, **k):
            started.append(time.monotonic())
            time.sleep(0.3)
            return orig(*a, **k)
        loop._execute_single = slow
        t0 = time.monotonic()
        q.run("read it", "q1")
        self.assertLess(time.monotonic() - t0, 1.0)  # 4 x 0.3s sequential = 1.2s
        self.assertEqual(len(started), 4)

    def test_failing_tests_go_back_to_the_model(self):
        q, loop, events, asked = session([
            {"tool_calls": [call("write_file", path="ok.txt", content="1\n")]},
            {"text": "Done."},
            {"tool_calls": [call("write_file", path="ok.txt", content="2\n")]},
            {"text": "Fixed."}])
        (loop.sandbox.root / "Makefile").write_text(
            "test:\n\t@grep -q 2 ok.txt || (echo 'FAIL want 2' && false)\n")
        r = q.run("make ok.txt", "q1")
        feedback = [m["text"] for m in q.messages
                    if m["role"] == "user" and "tests failed" in m.get("text", "")]
        self.assertEqual(len(feedback), 1)
        self.assertIn("FAIL want 2", feedback[0])
        self.assertTrue(r["said"].startswith("Fixed."))
        self.assertIn("Tests passed: make test", r["said"])

    def test_narrated_edit_gets_one_nudge(self):
        q, loop, events, asked = session([
            {"text": "I will create greet.py:\n```python\ndef greet(): ...\n```"},
            {"tool_calls": [call("write_file", path="greet.py", content="def greet():\n    pass\n")]},
            {"text": "Created greet.py."}])
        r = q.run("add greet.py", "q1")
        self.assertTrue((loop.sandbox.root / "greet.py").is_file())
        self.assertTrue(r["said"].startswith("Created greet.py."))
        self.assertEqual(sum(1 for m in q.messages if m.get("text") == NUDGE), 1)

    def test_nudge_happens_at_most_once(self):
        q, loop, events, asked = session([
            {"text": "```python\nx = 1\n```"}, {"text": "```python\nx = 1\n```"}])
        r = q.run("show me x", "q1")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(sum(1 for m in q.messages if m.get("text") == NUDGE), 1)

    def test_model_error_is_explained_not_crashed(self):
        class Boom:
            def chat(self, *a, **k):
                raise RuntimeError("anthropic HTTP 401")
        loop, _ = make_loop(project="quick-err")
        q = QuickSession(loop, Boom(), lambda e: None, lambda i: "approve")
        r = q.run("hi", "q1")
        self.assertEqual(r["status"], "error")
        self.assertIn("The model call failed", r["said"])

    def test_cancel_stops(self):
        from cancel import CancelToken
        q, loop, events, asked = session([{"text": "never"}])
        tok = CancelToken()
        tok.set("stop")
        q.cancel = tok
        self.assertEqual(q.run("hi", "q1")["status"], "cancelled")

    def test_project_rules_are_read(self):
        loop, _ = make_loop(project="rules")
        (loop.sandbox.root / "AGENTS.md").write_text("Use tabs.")
        self.assertIn("Use tabs.", project_rules(loop.sandbox.root))

    def test_journal_has_the_record(self):
        q, loop, events, asked = session([
            {"tool_calls": [call("read_file", path="app.py")]}, {"text": "ok"}])
        q.run("look", "q1")
        types = [e["type"] for e in loop.state.events]
        for t in ("quick_user", "tool_called", "tool_result", "quick_done",
                  "tokens_charged"):
            self.assertIn(t, types)


class SidecarRoutingTest(unittest.TestCase):
    def _sidecar(self, **snap):
        import awino_sidecar as S
        sc = S.Sidecar()
        loop, _ = make_loop(project="route")
        loop.state.snapshot.update(snap)
        sc.loop = loop
        return sc

    def test_direct_asks_use_quick(self):
        sc = self._sidecar()
        for t in ("add a greet function", "fix the failing test",
                  "explain this repo", "what does app.py do?"):
            self.assertTrue(sc._use_quick(t), t)

    def test_planning_and_active_missions_use_the_mission_flow(self):
        sc = self._sidecar()
        for t in ("plan this feature with me", "/mission ship v2",
                  "let's set a mission", "Honda first please"):
            self.assertFalse(sc._use_quick(t), t)
        sc = self._sidecar(mission={"id": "m", "text": "x"})
        self.assertFalse(sc._use_quick("add a test"))
        sc = self._sidecar(mission={"id": "m", "text": "x"}, done=True)
        self.assertTrue(sc._use_quick("add a test"))

    def test_mission_default_flow_setting(self):
        sc = self._sidecar()
        sc._default_flow = "mission"
        self.assertFalse(sc._use_quick("add a greet function"))

    def test_approval_routing_wakes_the_waiter(self):
        sc = self._sidecar()
        w = {"event": threading.Event(), "decision": None}
        sc._quick_waits["qa-1"] = w
        self.assertTrue(sc._route_quick_approval(
            {"cmd": "approve", "id": "qa-1", "decision": "approve"}))
        self.assertTrue(w["event"].is_set())
        self.assertEqual(w["decision"], "approve")
        self.assertFalse(sc._route_quick_approval(
            {"cmd": "approve", "id": "ap-other", "decision": "approve"}))


if __name__ == "__main__":
    unittest.main()


class _Claude:
    """Anthropic stub that codes like a real model: read + list in one
    step, then an edit, then a summary."""
    import http.server as _hs

    class H(_hs.BaseHTTPRequestHandler):
        def do_POST(self):
            import json
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            n = sum(1 for m in body["messages"] if m["role"] == "user"
                    and isinstance(m["content"], list)
                    for b in m["content"] if b.get("type") == "tool_result")
            if n == 0:
                content = [{"type": "text", "text": "Looking."},
                           {"type": "tool_use", "id": "r1", "name": "read_file",
                            "input": {"path": "app.py"}},
                           {"type": "tool_use", "id": "r2", "name": "list_dir",
                            "input": {"path": "."}}]
            elif n == 2:
                content = [{"type": "tool_use", "id": "w1", "name": "write_file",
                            "input": {"path": "app.py",
                                      "content": "def greet():\n    return 'hi'\n"}}]
            else:
                content = [{"type": "text", "text": "Added greet() to app.py."}]
            out = json.dumps({"content": content, "usage": {
                "input_tokens": 100, "output_tokens": 10}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass


class SidecarQuickEndToEndTest(unittest.TestCase):
    def test_direct_ask_edits_after_one_card(self):
        import json
        import socketserver
        import subprocess
        import tempfile
        srv = socketserver.TCPServer(("127.0.0.1", 0), _Claude.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        ws = tempfile.mkdtemp(prefix="awino-quick-")
        with open(os.path.join(ws, "app.py"), "w") as f:
            f.write("print('hi')\n")
        sidecar = os.path.join(os.path.dirname(__file__), "..", "awino_sidecar.py")
        p = subprocess.Popen([sys.executable, sidecar], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True,
                             env=dict(os.environ, ANTHROPIC_API_KEY="k",
                                      GIT_CEILING_DIRECTORIES=os.path.dirname(ws)))
        self.addCleanup(p.kill)

        def send(o):
            p.stdin.write(json.dumps(o) + "\n")
            p.stdin.flush()
        send({"cmd": "hello", "workspace": ws, "provider": "anthropic",
              "model": "claude-haiku-4-5", "stream": True,
              "endpoint": f"http://127.0.0.1:{srv.server_address[1]}"})
        send({"cmd": "user_message", "text": "add greet() to app.py",
              "stream": True})
        cards, result, seen = [], None, []
        deadline = time.time() + 60
        while time.time() < deadline:
            e = json.loads(p.stdout.readline())
            seen.append(e.get("event"))
            if e.get("event") == "approval_requested":
                cards.append(e["approvals"][0])
                send({"cmd": "approve", "id": e["approvals"][0]["id"],
                      "decision": "approve"})
            if e.get("event") == "turn_result":
                result = e["result"]
                break
        send({"cmd": "bye"})
        self.assertEqual([c["tool"] for c in cards], ["write_file"])
        self.assertIn("+def greet", cards[0]["diff"])
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["said"].startswith("Added greet() to app.py."))
        self.assertIn("token_meter", seen)
        with open(os.path.join(ws, "app.py")) as f:
            self.assertIn("def greet", f.read())
