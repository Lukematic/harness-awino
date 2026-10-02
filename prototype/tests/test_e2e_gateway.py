"""End-to-end smoke: the real sidecar against a fake OpenAI-compatible
gateway that enforces a Bearer key. Walks the flow that broke in the field:
401s on the native-tools path, "what time is it" grilled as a mission
question, no New chat, and a stale time budget."""
import json
import tempfile
import time
import unittest

from tests import fake_gateway
from tests.test_sidecar import SidecarClient


def until(c, pred, timeout=60):
    evs = []
    end = time.time() + timeout
    while time.time() < end:
        e = c.recv(timeout=max(1, end - time.time()))
        evs.append(e)
        if pred(e):
            return e, evs
    raise AssertionError("timeout")


def cmd(c, name, args=None):
    c.send({"cmd": "command", "name": name, "args": args or {}, "id": name})
    e, _ = until(c, lambda e: e.get("event") == "command_result"
                 and e.get("name") == name)
    return e


def say(c, text, stream=True):
    c.send({"cmd": "user_message", "text": text, "stream": stream})
    e, evs = until(c, lambda e: e.get("event") in ("turn_result", "error"),
                   timeout=90)
    return e, evs


class TestGatewayEndToEnd(unittest.TestCase):
    def test_flow(self):
        results = []

        def check(name, cond, detail=""):
            results.append((name, bool(cond), str(detail)[:300]))

        srv = fake_gateway.start()
        EP = f"http://127.0.0.1:{srv.server_port}/v1"


        home = tempfile.mkdtemp(prefix="awino-home-test-")
        c = SidecarClient(env={"AWINO_API_KEY": "test-key"}, home=home)
        try:
            r = c.hello(provider="openai", endpoint=EP, model="claude-haiku-4-5-20251001-v1-project")
            check("sidecar ready with openai provider", r.get("event") == "ready", r)
            n0 = len(fake_gateway.LOG)

            # 1. direct question before any mission
            e, evs = say(c, "what time is it")
            res = e.get("result", {})
            check("'what time is it' produces a valid turn", res.get("status") == "ok", json.dumps(e)[:400])
            check("answer uses the clock (no 'no clock' refusal)", "It is " in res.get("said", ""), res.get("said", "")[:300])
            check("routed advisor, not planning-grill", "stance: advisor" in res.get("said", ""), res.get("said", "")[:200])

            # 2. mission, then a statement -> interview question
            m = cmd(c, "mission", {"text": "testing", "criteria": ["manual"]})
            check("mission set", m["ok"], m)
            e, _ = say(c, "the export must be CSV")
            res = e.get("result", {})
            check("statement in DEFINE gets the interview", res.get("status") == "ok" and "Questions:" in res.get("said", ""), res.get("said", "")[:300])

            # 3. aside while a question is open
            e, _ = say(c, "what day is it?")
            res = e.get("result", {})
            check("aside in DEFINE answered directly", "stance: advisor" in res.get("said", "") and "It is " in res.get("said", ""), res.get("said", "")[:300])
            st = cmd(c, "status")
            stt = st["result"]["status"]
            oq = stt.get("open_questions", stt.get("questions"))
            check("open mission question survives the aside", oq == ["What outcome should this mission create?"], list(stt.keys()))

            # 4. new chat
            before = cmd(c, "status")["result"]
            sn = cmd(c, "session_new")
            check("session_new ok", sn["ok"] and sn["result"]["status"] == "ok", sn)
            check("mission kept across new chat", (sn["result"].get("mission") or {}).get("text") == "testing", sn)
            e, _ = say(c, "hi")
            res = e.get("result", {})
            check("turn works after new chat", res.get("status") == "ok", res)
            check("header carries the new run id", sn["result"]["conversation_id"] in res.get("said", ""), res.get("said", "")[:200])

            # 5. auth on every request, and NOW in every chat request
            reqs = fake_gateway.LOG[n0:]
            posts = [q for q in reqs if q["method"] == "POST"]
            check("every gateway request carried the Bearer key", reqs and all(q["auth"] for q in reqs), reqs)
            check("every chat request had ## NOW", posts and all(q["has_now"] for q in posts), posts)
        finally:
            c.close()

        # 6. wrong key -> readable error, not a crash
        c = SidecarClient(env={"AWINO_API_KEY": "wrong"})
        try:
            c.hello(provider="openai", endpoint=EP, model="m")
            e, _ = say(c, "what time is it")
            txt = json.dumps(e)
            check("bad key surfaces a 401 the user can read", "401" in txt, txt[:300])
        finally:
            c.close()


        bad = [(n, d) for n, ok, d in results if not ok]
        self.assertEqual(bad, [])
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()
