"""Phase A live proof 2, PARENT: model -> tool -> approval -> SIGKILL -> resume.

1. Spawns phase_a_kill_child.py (real subprocess, real 7B local model).
   The child drives the loop to an approval pause, writes pause.json,
   then dies by SIGKILL — no cleanup, exactly like `kill -9`.
2. The parent (a NEW process from the harness's perspective) creates a
   fresh Loop over the same home dir — all state comes from disk.
3. The operator approves; the write executes; the turn finalizes.
Asserts: exactly one write (no duplicate), approval events ordered,
turn completed, criterion satisfied.

Writes proof/PHASE_A_TRANSCRIPT.md (appended). Exit 0 = proven.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "/home/hatch/workspace/awino-rebuild/prototype")
from backends import ScriptedBackend, ScriptedJudge  # noqa: E402
from loop import Loop  # noqa: E402

CHILD = "/home/hatch/workspace/awino-rebuild/proof/phase_a_kill_child.py"
TRANSCRIPT = "/home/hatch/workspace/awino-rebuild/proof/PHASE_A_TRANSCRIPT.md"


def main():
    home = tempfile.mkdtemp(prefix="awino-phasea-kill-")
    project = "kill-live"
    t0 = time.time()
    # The child is a genuinely separate process; it SIGKILLs itself.
    p = subprocess.run([sys.executable, CHILD, home, project],
                       capture_output=True, text=True, timeout=3600)
    dt = time.time() - t0
    print("child stdout tail:")
    print("\n".join(p.stdout.strip().split("\n")[-6:]))
    assert p.returncode == -9, \
        f"child did not die by SIGKILL (rc={p.returncode}): {p.stderr[-500:]}"
    pause = json.load(open(os.path.join(home, "pause.json")))
    if pause.get("failed"):
        raise SystemExit(
            "GAP (honest): the live 7B model never proposed the consequential "
            f"write within 3 turns (last status={pause['last_status']}). "
            "Approval-interrupt kill/resume is proven by the scripted-backend "
            "suite (test_recovery.py); the live model->tool->approval chain "
            "needs a more capable local model.")

    ap_id = pause["approval_ids"][0]
    # ---- NEW PROCESS: resume purely from disk ----
    loop2 = Loop(home, project, ScriptedBackend([]), ScriptedJudge())
    s = loop2.state.snapshot
    assert s["awaiting_approval"], "approval wait did not survive the kill"
    pend = [a["id"] for a in s["approvals"] if a["status"] == "pending"]
    assert ap_id in pend, f"{ap_id} not pending after resume"
    assert (s["active_turn"] or {}).get("turn_id"), \
        "paused turn routing did not survive the kill"
    target = os.path.join(home, "projects", project, "sandbox", "INVENTORY.md")
    assert not os.path.exists(target), "write executed before approval!"

    r = loop2.approve(ap_id)
    assert r["status"] == "ok", r
    assert os.path.isfile(target), "approved write did not execute on resume"
    content = open(target).read()
    assert len(content) > 0, "written file is empty"

    # exactly one execution: no duplicate writes across the kill
    writes = [e for e in loop2.state.events
              if e["type"] == "tool_result"
              and e["data"].get("tool") == "write_file"
              and not e["data"].get("reused")]
    assert len(writes) == 1, f"expected 1 write, got {len(writes)}"
    seqs = {}
    for e in loop2.state.events:
        t = e["type"]
        if t in ("approval_requested", "approval_granted") or \
                (t in ("tool_called", "tool_result")
                 and e["data"].get("tool") == "write_file"):
            seqs.setdefault(t, []).append(e["seq"])
    assert seqs["approval_requested"][-1] < seqs["approval_granted"][-1] \
        < seqs["tool_called"][-1] < seqs["tool_result"][-1], \
        "event order wrong across the kill boundary"
    assert any(e["type"] == "turn_completed" for e in loop2.state.events), \
        "paused turn never finalized after resume"
    s = loop2.state.snapshot
    assert not s["awaiting_approval"], "still awaiting approval after drain"

    with open(TRANSCRIPT, "a") as f:
        f.write(f"\n## Live proof 2 — approval interrupt -> SIGKILL -> resume "
                f"({time.strftime('%Y-%m-%d %H:%M')})\n\n")
        f.write(f"- child: real subprocess, real 7B local model over HTTP; "
                f"drove the loop to `awaiting_approval`, then SIGKILL "
                f"(rc=-9, no cleanup)\n")
        f.write(f"- parent: fresh Loop over the same home dir — all state "
                f"from disk (events.jsonl + snapshot.json)\n")
        f.write(f"- approval wait, pending approval id, and paused turn "
                f"routing all survived the kill\n")
        f.write(f"- operator approved `{ap_id}`; `write_file INVENTORY.md` "
                f"executed exactly once (no duplicate across the kill)\n")
        f.write(f"- event order across the kill: approval_requested -> "
                f"approval_granted -> tool_called -> tool_result\n")
        f.write(f"- paused turn finalized after resume; "
                f"criterion event:tool_called:write_file satisfied\n")
        f.write(f"- child wall time: {dt:.0f}s\n")
    print("PASS — model -> tool -> approval -> SIGKILL -> resume proven live")


main()
