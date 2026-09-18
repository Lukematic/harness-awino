"""Scripted two-project human+AI session through the owned loop.

Project A (login-bug): bugfix mission -> plan -> read -> write (approval) ->
  forged done_claim rejected -> operator /done -> SHIP.
Project B (q3-research): research mission, read-only turns.
Mid-way switch proves projects are isolated; switching back proves missions
persist across a simulated restart.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from loop import Loop
from backends import ScriptedBackend, ScriptedJudge

APP_PY = '''def do_login(user, password):
    # BUG: crashes when password is empty
    return check(password.strip())
'''


def T(**kw):
    base = {"header": "echo",  # cooperative mock echoes the harness header
            "objective": "Fix the login bug",
            "plan": ["Locate the fault", "Patch it", "Verify with evidence"],
            "tool_calls": [], "questions": [], "assumptions": [],
            "progress_delta": "", "done_claim": False}
    base.update(kw)
    return base


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="awino-demo-"))
    print(f"demo home: {home}\n")

    # ---------------- Project A ----------------
    backend_a = ScriptedBackend([
        T(plan=[],
          questions=["Which file holds the login form?"],
          progress_delta="Need to know which file holds the login form before acting."),
        T(tool_calls=[{"name": "list_dir", "args": {}},
                      {"name": "read_file", "args": {"path": "app.py"}}],
          progress_delta="Found app.py; do_login crashes on empty password."),
        T(tool_calls=[{"name": "write_file",
                       "args": {"path": "login_fixed.py",
                                "content": "def do_login(user, password):\n"
                                           "    return check((password or '').strip())\n"}}],
          progress_delta="Patch ready; writing login_fixed.py (needs approval).",
          assumptions=["Cause: do_login calls password.strip() without guarding "
                       "empty input; the patch guards it."]),
        # forged done: artifact exists but manual criterion needs the operator
        T(progress_delta="All fixed and verified. Done.", done_claim=True,
          assumptions=["Cause: the unguarded strip() call, now patched and written."]),
    ])
    loop_a = Loop(home, "login-bug", backend_a, ScriptedJudge())
    (loop_a.sandbox.root / "app.py").write_text(APP_PY)
    loop_a.set_mission("Fix login crash on empty password",
                       ["artifact:login_fixed.py", "manual"])

    def say(who, text):
        print(f"{who}: {text}")

    say("human", "The login form crashes when the password is empty.")
    r = loop_a.run_user_turn("The login form crashes when the password is empty.")
    say("awino", f"[{r['status']}] {r['said']}")
    print(f"awino: turn header: {backend_a.calls[0]['contract'].splitlines()[0]}\n")

    say("human", "It's in app.py, function do_login.")
    r = loop_a.run_user_turn("It's in app.py, function do_login.")
    say("awino", f"[{r['status']}] {r['said']}\n")

    # elevator gates (code-enforced): DEFINE -> PLAN -> BUILD (scoped).
    # No code can be touched until the scoped contract is approved.
    say("human", "/approve-contract")
    r = loop_a.approve_contract()
    say("awino", f"[{r['status']}] {r['said']}\n")

    say("human", "/approve-contract login_fixed.py")
    r = loop_a.approve_contract(["login_fixed.py"])
    say("awino", f"[{r['status']}] {r['said']} "
                 f"(phase: {loop_a.state.snapshot['phase']})\n")

    say("human", "Go ahead and patch it.")
    r = loop_a.run_user_turn("Go ahead and patch it.")
    say("awino", f"[{r['status']}] {r['said']}\n")

    # operator approves the consequential write (bound to exact args)
    ap = r["approvals"][0]
    say("human", f"/approve {ap}")
    r = loop_a.approve(ap)
    say("awino", f"[{r['status']}] {r['said']}\n")

    say("human", "Is it done?")
    r = loop_a.run_user_turn("Is it done?")
    say("awino", f"[{r['status']}] {r['said']}\n")

    say("human", "/done")
    r = loop_a.request_done()
    say("awino", f"[{r['status']}] {r['said']}\n")

    # ---------------- Project B (switch mid-way) ----------------
    backend_b = ScriptedBackend([
        T(objective="Research sync options",
          plan=["Read local notes", "Summarize options"],
          tool_calls=[{"name": "list_dir", "args": {}}],
          progress_delta="Surveying available notes."),
        T(objective="Research sync options",
          progress_delta="Options: manual rsync, Syncthing, git. Recommend git for history."),
    ])
    loop_b = Loop(home, "q3-research", backend_b, ScriptedJudge())
    loop_b.set_mission("Research file-sync options", ["manual"])

    say("human", "/project q3-research — quick research task")
    r = loop_b.run_user_turn("What are my file-sync options?")
    say("awino", f"[{r['status']}] {r['said']}\n")
    r = loop_b.run_user_turn("Summarize with a recommendation.")
    say("awino", f"[{r['status']}] {r['said']}\n")

    # ---------------- back to A: simulated restart ----------------
    loop_a2 = Loop(home, "login-bug", ScriptedBackend([]), ScriptedJudge())
    st = loop_a2.status()
    say("human", "/project login-bug (restarted process)")
    print(f"awino: project={st['project']} phase={st['phase']} done={st['done']}")
    print(f"awino: mission: {st['mission']}")
    for c in st["criteria"]:
        print(f"awino:   [{'x' if c['ok'] else ' '}] {c['label']}")

    na = sum(1 for _ in (home / "projects" / "login-bug" / "events.jsonl").read_text().splitlines())
    nb = sum(1 for _ in (home / "projects" / "q3-research" / "events.jsonl").read_text().splitlines())
    print(f"\nevent log sizes: login-bug={na} events, q3-research={nb} events (isolated)")
    print("demo complete. home kept at:", home)
    shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
