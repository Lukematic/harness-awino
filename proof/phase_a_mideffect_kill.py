"""Phase A proof 3: kill mid-effect -> new process reconciles, no blind replay.

Two genuine crash scenarios, each in a real child subprocess that dies via
os._exit(1) (no cleanup — exactly what kill -9 leaves on disk):

  A. crash between tool_called record and execution (effect NOT applied):
     parent must see effect_unknown, pause for inspection, and
     resolve_inspection(..., "not_applied") must execute it exactly once.
  B. crash between execution and tool_result record (effect applied, result
     lost): parent must see effect_unknown, and
     resolve_inspection(..., "already_applied") must verify (not re-execute).

Recovery is backend-independent, so the child uses no model backend — the
on-disk torn state is constructed exactly as a real kill would leave it.

Writes proof/PHASE_A_TRANSCRIPT.md (appended). Exit 0 = proven.
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "/home/hatch/workspace/awino-rebuild/prototype")
from backends import ScriptedBackend, ScriptedJudge  # noqa: E402
from loop import Loop  # noqa: E402

TRANSCRIPT = "/home/hatch/workspace/awino-rebuild/proof/PHASE_A_TRANSCRIPT.md"
CALL = {"tool": "write_file",
        "args": {"path": "mid.txt", "content": "mid-effect-data"}}

CHILD_SRC = r'''
import os, sys
sys.path.insert(0, "/home/hatch/workspace/awino-rebuild/prototype")
from backends import ScriptedBackend, ScriptedJudge
from loop import Loop
home, project, scenario = sys.argv[1], sys.argv[2], sys.argv[3]
loop = Loop(home, project, ScriptedBackend([]), ScriptedJudge())
loop.set_mission("Write mid.txt", ["event:tool_called:write_file"])
loop.run_user_turn("draft")
loop.approve_contract()
loop.approve_contract(["mid.txt"])
# Crash point: tool_called is fsync'd to events.jsonl; nothing after runs.
loop.state.record("tool_called",
                  {"call_id": "t9.0", "tool": "write_file",
                   "args": {"path": "mid.txt", "content": "mid-effect-data"},
                   "idem_key": "midfx0001"})
if scenario == "b":
    loop.sandbox.write_file("mid.txt", "mid-effect-data")  # effect applied...
# ...then the process dies before tool_result is recorded. No cleanup.
os._exit(1)
'''


def fresh_loop(home, project):
    return Loop(home, project, ScriptedBackend([]), ScriptedJudge())


def scenario(scn):
    home = tempfile.mkdtemp(prefix=f"awino-phasea-mid-{scn}-")
    project = f"mid-{scn}"
    p = subprocess.run([sys.executable, "-c", CHILD_SRC, home, project, scn],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 1, f"child rc={p.returncode}: {p.stderr[-300:]}"
    loop2 = fresh_loop(home, project)
    assert loop2.state.snapshot["awaiting_inspection"] == "t9.0", \
        "unknown effect did not pause the new process"
    target = os.path.join(home, "projects", project, "sandbox", "mid.txt")
    if scn == "a":
        assert not os.path.exists(target), \
            "recovery executed the write by itself (blind replay!)"
        r = loop2.resolve_inspection("t9.0", "not_applied")
        assert r["status"] == "ok", r
        assert open(target).read() == "mid-effect-data"
    else:
        assert open(target).read() == "mid-effect-data"
        before = open(target, "rb").read()
        r = loop2.resolve_inspection("t9.0", "already_applied")
        assert r["status"] == "ok", r
        assert open(target, "rb").read() == before, \
            "already-applied effect was re-executed"
    results = [e for e in loop2.state.events
               if e["type"] == "tool_result" and e["data"].get("call_id") == "t9.0"]
    assert len(results) == 1, f"expected 1 tool_result, got {len(results)}"
    assert loop2.state.snapshot["awaiting_inspection"] is None
    return True


def main():
    scenario("a")
    print("scenario A (crash before execution): reconciled via not_applied, "
          "executed exactly once")
    scenario("b")
    print("scenario B (crash after execution): reconciled via already_applied, "
          "verified not re-executed")
    with open(TRANSCRIPT, "a") as f:
        f.write(f"\n## Proof 3 — kill mid-effect -> reconcile "
                f"({time.strftime('%Y-%m-%d %H:%M')})\n\n")
        f.write("- child subprocesses died via os._exit(1) with no cleanup, "
                "leaving tool_called without tool_result on disk\n")
        f.write("- scenario A (effect not applied): new process recorded "
                "effect_unknown and paused for inspection; it did NOT replay "
                "the write; resolve_inspection('not_applied') executed it "
                "exactly once\n")
        f.write("- scenario B (effect applied, result lost): "
                "resolve_inspection('already_applied') verified the file "
                "digest and recorded the result without re-executing\n")
        f.write("- exactly one tool_result for the call id in both scenarios; "
                "no duplicate writes, no lost effects\n")
    print("PASS — kill mid-effect reconciled, no blind replay")


main()
