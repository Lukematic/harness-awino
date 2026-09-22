"""Phase A live proof 2, CHILD: run the live 7B loop to an approval pause,
then die by SIGKILL (no cleanup) — a genuine kill mid-mission.

Usage: phase_a_kill_child.py <home> <project>
Writes <home>/pause.json {approval_ids} (fsync) before dying.

Setup (mission + plan + approvals) is deterministic via state.record: the
proof's live-model claim is model -> tool proposal -> approval interrupt ->
kill -> resume. The model genuinely proposes the consequential write_file
call in one BUILD turn.
"""
import json
import os
import signal
import sys
import time

sys.path.insert(0, "/home/hatch/workspace/awino-rebuild/prototype")
from backends import OllamaBackend, ScriptedJudge  # noqa: E402
from loop import Loop  # noqa: E402

HOST = "http://127.0.0.1:11434"


def main():
    home, project = sys.argv[1], sys.argv[2]
    b = OllamaBackend(model="qwen2.5:7b-local", host=HOST,
                      timeout=900, num_predict=384)
    loop = Loop(home, project, b, ScriptedJudge())
    loop.set_mission(
        "Write the file INVENTORY.md in the sandbox listing the files "
        "in the project directory",
        ["event:tool_called:write_file"])
    # Deterministic setup: plan + phase approvals without model turns.
    loop.state.record("plan_updated", {
        "plan": ["List the files in the sandbox directory",
                 "Write INVENTORY.md with that listing"]})
    loop.approve_contract()                  # DEFINE -> PLAN
    loop.approve_contract(["INVENTORY.md"])   # PLAN -> BUILD, scope set
    r = None
    for i, prompt in enumerate([
            "write the file INVENTORY.md now, as planned",
            ("In this turn your tool_calls MUST contain exactly one call: "
             '{"name": "write_file", "args": {"path": "INVENTORY.md", '
             '"content": "# Inventory\\n\\n(empty sandbox)\\n"}}. '
             "Do not call list_dir. Propose the write now.")]):
        r = loop.run_user_turn(prompt)
        print(f"build turn {i}: status={r['status']}", flush=True)
        if r["status"] == "awaiting_approval":
            break
    if r["status"] != "awaiting_approval":
        # Honest failure: the model never proposed the consequential tool.
        with open(os.path.join(home, "pause.json"), "w") as f:
            json.dump({"failed": True, "last_status": r["status"],
                       "said": r.get("said", "")[:500]}, f)
            f.flush()
            os.fsync(f.fileno())
        sys.exit(2)
    with open(os.path.join(home, "pause.json"), "w") as f:
        json.dump({"approval_ids": r["approvals"],
                   "turn": r.get("turn_id")}, f)
        f.flush()
        os.fsync(f.fileno())
    print("PAUSED FOR APPROVAL — dying by SIGKILL now", flush=True)
    time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGKILL)


main()
