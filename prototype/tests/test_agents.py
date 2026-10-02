"""Parallel agents in git worktrees (10-02): start -> approve its edit ->
done -> diff -> merge into the main checkout; the main chat stays free."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

SIDECAR = os.path.join(os.path.dirname(__file__), "..", "awino_sidecar.py")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd, *a], capture_output=True,
                          text=True, env=GIT_ENV)


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="awino-agents-")
        git(self.ws, "init", "-q")
        with open(os.path.join(self.ws, "app.py"), "w") as f:
            f.write("print('hi')\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-qm", "base")
        script = [{"text": "Writing notes.", "tool_calls": [
                      {"name": "write_file",
                       "args": {"path": "NOTES.md", "content": "# Notes\n"}}]},
                  {"text": "Added NOTES.md."}]
        self.p = subprocess.Popen(
            [sys.executable, SIDECAR], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, text=True,
            env=dict(GIT_ENV, GIT_CEILING_DIRECTORIES=os.path.dirname(self.ws)))
        self.addCleanup(self.p.kill)
        self.send({"cmd": "hello", "workspace": self.ws,
                   "provider": "scripted", "script": script})
        self.wait("ready")

    def send(self, o):
        self.p.stdin.write(json.dumps(o) + "\n")
        self.p.stdin.flush()

    def wait(self, event, timeout=60, on=None):
        end = time.time() + timeout
        while time.time() < end:
            e = json.loads(self.p.stdout.readline())
            if on:
                on(e)
            if e.get("event") == event:
                return e
        self.fail(f"no {event}")

    def cmd(self, name, args=None, cid="c"):
        self.send({"cmd": "command", "name": name, "args": args or {}, "id": cid})
        e = self.wait("command_result")
        return e.get("result", e)

    def test_agent_runs_in_a_worktree_and_merges(self):
        r = self.cmd("agent_start", {"task": "add a notes file"})
        self.assertEqual(r["status"], "ok", r)
        name = r["agent"]["name"]
        wt = r["agent"]["path"]
        self.assertTrue(os.path.isdir(wt))

        def approve(e):
            if e.get("event") == "approval_requested":
                a = e["approvals"][0]
                self.assertEqual(a.get("agent"), name)
                self.send({"cmd": "approve", "id": a["id"], "decision": "approve"})
        done = self.wait("agent_done", on=approve)
        self.assertEqual(done["status"], "done", done)
        self.assertIn("NOTES.md", done["changes"])
        # the main checkout is untouched until merge
        self.assertFalse(os.path.exists(os.path.join(self.ws, "NOTES.md")))
        d = self.cmd("agent_diff", {"name": name}, "d")
        self.assertIn("+# Notes", d["diff"])
        m = self.cmd("agent_merge", {"name": name}, "m")
        self.assertEqual(m["status"], "ok", m)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "NOTES.md")))
        rm = self.cmd("agent_remove", {"name": name}, "r")
        self.assertEqual(rm["status"], "ok")
        self.assertFalse(os.path.isdir(wt))
        self.send({"cmd": "bye"})

    def test_no_commit_repo_is_explained(self):
        empty = tempfile.mkdtemp(prefix="awino-noc-")
        git(empty, "init", "-q")
        import agents
        m = agents.AgentManager(empty, empty, "p", None, None, None)
        r = m.start("do it")
        self.assertEqual(r["status"], "error")
        self.assertIn("at least one commit", r["said"])


if __name__ == "__main__":
    unittest.main()
