"""LIVE test of the Quick path and a worktree agent against a real model.

Plays only the human (types the request, clicks Approve on every card);
the model does everything else through the real sidecar. Reports what
happened; never forces a pass.

  ANTHROPIC_API_KEY=... python run_quick_live.py --provider anthropic \\
      --model claude-haiku-4-5 --out live-out
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIDECAR = ROOT / "prototype" / "awino_sidecar.py"
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="awino-live",
               GIT_AUTHOR_EMAIL="live@awino", GIT_COMMITTER_NAME="awino-live",
               GIT_COMMITTER_EMAIL="live@awino")
TASK = ("Add a function add(a, b) to calc.py that returns their sum, and a "
        "unittest for it in test_calc.py.")
AGENT_TASK = "Write a short README.md that explains what calc.py does."


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True,
                          text=True, env=GIT_ENV)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=["openai", "anthropic"])
    ap.add_argument("--endpoint", default="")
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    key = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "AWINO_API_KEY"
    if not os.environ.get(key):
        raise SystemExit(f"{key} is not set — add it as a repository secret.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    ws = Path(tempfile.mkdtemp(prefix="quick-live-")) / "calc"
    ws.mkdir()
    (ws / "calc.py").write_text('"""Tiny calculator."""\n')
    (ws / "Makefile").write_text("test:\n\tpython -m unittest -q\n")
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "base")

    p = subprocess.Popen([sys.executable, str(SIDECAR)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True,
                         env=dict(GIT_ENV, GIT_CEILING_DIRECTORIES=str(ws.parent)))
    log = []

    def send(o):
        p.stdin.write(json.dumps(o) + "\n")
        p.stdin.flush()

    def until(pred, timeout=600):
        end = time.time() + timeout
        while time.time() < end:
            line = p.stdout.readline()
            if not line:
                return None
            e = json.loads(line)
            log.append(e)
            if e.get("event") == "approval_requested":
                send({"cmd": "approve", "id": e["approvals"][0]["id"],
                      "decision": "approve"})
            if pred(e):
                return e
        return None

    hello = {"cmd": "hello", "workspace": str(ws), "provider": a.provider,
             "model": a.model, "stream": True}
    if a.endpoint:
        hello["endpoint"] = a.endpoint
    send(hello)
    until(lambda e: e.get("event") == "ready", 60)

    t0 = time.time()
    send({"cmd": "user_message", "text": TASK, "stream": True})
    res = until(lambda e: e.get("event") == "turn_result")
    quick_s = round(time.time() - t0, 1)
    result = (res or {}).get("result", {})
    # Like a user would: commit the Quick change so the agent can merge.
    git(ws, "add", "calc.py", "test_calc.py")
    git(ws, "commit", "-qm", "quick: add()")

    send({"cmd": "command", "name": "agent_start", "id": "a1",
          "args": {"task": AGENT_TASK}})
    started = until(lambda e: e.get("event") == "command_result")
    agent = ((started or {}).get("result") or {}).get("agent") or {}
    done = until(lambda e: e.get("event") == "agent_done") if agent else None
    merged = None
    if done and done.get("changes"):
        send({"cmd": "command", "name": "agent_merge", "id": "m1",
              "args": {"name": agent["name"]}})
        merged = (until(lambda e: e.get("event") == "command_result") or {}).get("result")
    send({"cmd": "bye"})

    tests = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=ws,
                           capture_output=True, text=True)
    cards = [e["approvals"][0]["tool"] for e in log
             if e.get("event") == "approval_requested"]
    tools = [e.get("tool") for e in log if e.get("event") == "tool_progress"
             and e.get("phase") == "start"]
    meters = [e for e in log if e.get("event") == "token_meter"]
    checks = {
        "Quick reply came back (status ok)": result.get("status") == "ok",
        "Model used tools natively": bool(tools),
        "Edits went through diff cards": "write_file" in cards or "patch_file" in cards,
        "calc.py has add()": "def add" in (ws / "calc.py").read_text(),
        "test_calc.py exists": (ws / "test_calc.py").exists(),
        "Harness ran the project tests": "Tests passed" in result.get("said", "")
                                          or "Tests still fail" in result.get("said", ""),
        "Tests pass now": tests.returncode == 0,
        "Agent started in a worktree": bool(agent),
        "Agent finished": bool(done) and done.get("status") == "done",
        "Agent work merged": bool(merged) and merged.get("status") == "ok",
        "README.md in the main checkout": (ws / "README.md").exists(),
    }
    lines = [f"# LIVE Quick + agent run ({a.provider} · {a.model})", "",
             "No model replies were scripted. The script typed the request and "
             "clicked Approve on every card.", "",
             "| Check | Observed |", "|---|---|"]
    lines += [f"| {k} | {'yes' if v else 'no'} |" for k, v in checks.items()]
    lines += ["", f"Quick reply time: {quick_s}s",
              f"Tool calls (in order): {', '.join(tools) or 'none'}",
              f"Approval cards: {', '.join(cards) or 'none'}",
              f"Tokens (last meter): {meters[-1].get('turn_tokens') if meters else 'n/a'}"
              f" · cached {meters[-1].get('cached_pct') if meters else 0}%",
              "", "## Quick reply", "", result.get("said", "(none)"),
              "", "## Agent", "", json.dumps({k: done.get(k) for k in ("status", "said", "changes")}
                                             if done else {"started": bool(agent)}, indent=1),
              "", "## Final tests", "", "```", (tests.stdout + tests.stderr)[-1500:], "```"]
    (out / "quick-report.md").write_text("\n".join(lines) + "\n")
    (out / "quick-events.jsonl").write_text("\n".join(json.dumps(e) for e in log))
    subprocess.run(["cp", "-r", str(ws), str(out / "quick-workspace")])
    print("\n".join(lines))
    if not all(checks.values()):
        raise SystemExit(1)  # the job goes red; the report is still uploaded


if __name__ == "__main__":
    main()
