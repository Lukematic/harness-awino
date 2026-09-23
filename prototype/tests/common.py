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


# Absolute path of prototype/. Tests must not depend on the process cwd:
# the suite is run from prototype/ on Linux CI but from the repo root
# (or elsewhere) on Windows. Evidence links like "tests/common.py" are
# resolved against this, never against ".".
PROTOTYPE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def T(**kw):
    base = {"header": "echo",  # cooperative mock echoes the harness header
            "objective": "Fix the login bug",
            "plan": ["Locate the fault", "Patch it", "Verify"],
            "tool_calls": [], "questions": [], "assumptions": [],
            "progress_delta": "Working.", "done_claim": False}
    base.update(kw)
    return base


def drive_verification(loop, evidence_links=None, recipe_exit=0):
    """Drive the Track G verifier flow: begin -> turn -> complete.

    Returns the complete_verification result. evidence_links maps
    criterion text (see verify.criterion_text) to proof-link paths.
    """
    b = loop.begin_verification()
    assert b["status"] == "ok", b
    wid = b["worker_id"]
    r = loop.run_verifier_turn(
        wid, {"evidence_links": evidence_links or {},
              "recipe_result": {"runner": "just", "recipe": "test",
                                "exit_code": recipe_exit, "output": "stubbed"},
              "project_root": PROTOTYPE_ROOT})
    assert r["status"] == "ok", r
    return loop.complete_verification(wid)
