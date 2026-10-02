"""Parallel agents, each in its own git worktree.

`start(task)` creates ../.awino-worktrees/<repo>/<name> (beside the repo,
never inside it, so it can't end up in your commits) on a new branch
awino/<name> (from HEAD), gives it its own Loop (own journal) and a Quick
session, and runs the task in a background thread while the main chat
stays free. Its events reach the client as {"event": "agent_event",
"agent": name, "payload": <the usual event>}; its approval cards are
ordinary approval_requested events carrying "agent".

When it finishes: `diff(name)` shows the change against the branch point,
`merge(name)` commits the work on its branch and merges it into the main
checkout (a merge commit; conflicts are reported, never forced), and
`remove(name)` deletes the worktree and its branch.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
import uuid
from pathlib import Path


def _git(cwd, *args, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, timeout=timeout)


# Build output the agent's test runs leave behind; never part of its work
# (repos that already gitignore these are unaffected).
_JUNK = (":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc",
         ":(exclude,glob)**/node_modules/**", ":(exclude,glob).awino/**",
         ":(exclude,glob)**/.pytest_cache/**")


def _add_work(path) -> None:
    _git(path, "add", "-A", "--", ".", *_JUNK)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:28].strip("-") or "agent") + "-" + uuid.uuid4().hex[:4]


class AgentManager:
    def __init__(self, root: Path, home: Path, project: str, make_backend,
                 emit, approve, judge_factory=None, sandbox_factory=None):
        """make_backend() -> a fresh backend (one per agent: token counts
        and per-turn state never mix). approve(item, agent) -> decision."""
        self.root = Path(root)
        self.home = Path(home)
        self.project = project
        self.make_backend = make_backend
        self.emit = emit
        self.approve = approve
        self.judge_factory = judge_factory
        self.sandbox_factory = sandbox_factory
        self.agents: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- start
    def start(self, task: str, name: str | None = None) -> dict:
        task = (task or "").strip()
        if not task:
            return {"status": "error", "said": "An agent needs a task."}
        if _git(self.root, "rev-parse", "--verify", "HEAD").returncode != 0:
            return {"status": "error",
                    "said": ("Agents work in git worktrees, which need a "
                             "repository with at least one commit.")}
        name = name or _slug(task)
        path = self.root.parent / ".awino-worktrees" / self.root.name / name
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = f"awino/{name}"
        base = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        r = _git(self.root, "worktree", "add", "-b", branch, str(path), base)
        if r.returncode != 0:
            return {"status": "error",
                    "said": f"Couldn't create the worktree: {r.stderr.strip()[:300]}"}
        from loop import Loop
        from quick import QuickSession
        loop = Loop(str(self.home), f"{self.project}/agent-{name}",
                    self.make_backend(),
                    self.judge_factory() if self.judge_factory else None,
                    sandbox_dir=str(path))
        if self.sandbox_factory is not None:
            loop.sandbox = self.sandbox_factory(path)
        agent = {"name": name, "task": task, "branch": branch, "base": base,
                 "path": str(path), "status": "running", "said": "",
                 "started": time.time(), "loop": loop}

        def emit(ev):
            self.emit({"event": "agent_event", "agent": name, "payload": ev})

        def approve(item):
            return self.approve(dict(item, agent=name), name)

        q = QuickSession(loop, loop.backend, emit, approve)
        agent["quick"] = q
        with self._lock:
            self.agents[name] = agent

        def run():
            try:
                res = q.run(task, "a" + uuid.uuid4().hex[:8])
            except Exception as ex:  # noqa: BLE001 - reported, never fatal
                res = {"status": "error", "said": f"{type(ex).__name__}: {ex}"}
            agent["status"] = "done" if res.get("status") == "ok" else res.get("status")
            agent["said"] = res.get("said", "")
            agent["finished"] = time.time()
            self.emit({"event": "agent_done", "agent": name,
                       "status": agent["status"], "said": agent["said"],
                       "changes": self._changed(path)})

        threading.Thread(target=run, daemon=True, name=f"agent-{name}").start()
        return {"status": "ok", "agent": self._public(agent),
                "said": f"Agent {name} started on branch {branch}."}

    # ------------------------------------------------------------- query
    def _changed(self, path) -> list[str]:
        r = _git(path, "status", "--porcelain")
        return [ln[3:] for ln in r.stdout.splitlines() if ln.strip()]

    def _public(self, a: dict) -> dict:
        return {k: a.get(k) for k in ("name", "task", "branch", "path",
                                      "status", "said", "started", "finished")}

    def list(self) -> list[dict]:
        out = []
        for a in self.agents.values():
            row = self._public(a)
            row["changes"] = self._changed(a["path"]) if Path(a["path"]).exists() else []
            out.append(row)
        return out

    def diff(self, name: str) -> dict:
        a = self.agents.get(name)
        if not a:
            return {"status": "error", "said": f"No agent {name!r}."}
        _add_work(a["path"])
        d = _git(a["path"], "diff", "--cached", a["base"])
        _git(a["path"], "reset", "-q")
        return {"status": "ok", "diff": d.stdout, "changes": self._changed(a["path"])}

    # ------------------------------------------------------------- finish
    def merge(self, name: str) -> dict:
        a = self.agents.get(name)
        if not a:
            return {"status": "error", "said": f"No agent {name!r}."}
        if a["status"] == "running":
            return {"status": "error", "said": "That agent is still working."}
        if self._changed(a["path"]):
            _add_work(a["path"])
        if _git(a["path"], "diff", "--cached", "--quiet").returncode != 0:
            c = _git(a["path"], "commit", "-m",
                     f"Awino agent {name}: {a['task'][:60]}",
                     "--no-verify")
            if c.returncode != 0:
                return {"status": "error",
                        "said": f"Couldn't commit the agent's work: {c.stderr.strip()[:300]}"}
        if _git(self.root, "status", "--porcelain",
                "--untracked-files=no").stdout.strip():
            return {"status": "error",
                    "said": ("Your main checkout has uncommitted changes; "
                             "commit or stash them, then merge the agent.")}
        m = _git(self.root, "merge", "--no-ff", "-m",
                 f"Merge Awino agent {name}", a["branch"])
        if m.returncode != 0:
            _git(self.root, "merge", "--abort")
            return {"status": "conflict",
                    "said": ("The agent's branch conflicts with your checkout; "
                             f"nothing was changed. Merge {a['branch']} by hand. "
                             + (m.stdout + m.stderr).strip()[-300:])}
        a["status"] = "merged"
        return {"status": "ok", "said": f"Merged {a['branch']} into your checkout."}

    def remove(self, name: str) -> dict:
        a = self.agents.pop(name, None)
        if not a:
            return {"status": "error", "said": f"No agent {name!r}."}
        _git(self.root, "worktree", "remove", "--force", a["path"])
        if a["status"] != "merged":
            _git(self.root, "branch", "-D", a["branch"])
        return {"status": "ok", "said": f"Removed agent {name}."}
