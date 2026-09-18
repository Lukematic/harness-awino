"""Shared fixtures for the adversarial suite."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loop import Loop  # noqa: E402
from backends import ScriptedBackend, HostileBackend, ScriptedJudge  # noqa: E402


def make_loop(project="p1", backend=None, judge=None, config=None, home=None):
    home = home or tempfile.mkdtemp(prefix="awino-test-")
    backend = backend if backend is not None else ScriptedBackend([])
    judge = judge if judge is not None else ScriptedJudge()
    return Loop(home, project, backend, judge, config=config), home


def T(**kw):
    base = {"header": "echo",  # cooperative mock echoes the harness header
            "objective": "Fix the login bug",
            "plan": ["Locate the fault", "Patch it", "Verify"],
            "tool_calls": [], "questions": [], "assumptions": [],
            "progress_delta": "Working.", "done_claim": False}
    base.update(kw)
    return base
