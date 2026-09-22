"""TEST 1 retry: tightened system prompt vs the live 1.5B model."""
import os
import sys
import tempfile
import time

sys.path.insert(0, "/home/hatch/workspace/awino-rebuild/prototype")
from backends import OllamaBackend, ScriptedJudge  # noqa: E402
from contract import verify_done_criteria  # noqa: E402
from loop import Loop  # noqa: E402

HOST = "http://127.0.0.1:11434"
RAW = []


def main():
    b = OllamaBackend(model="qwen2.5:1.5b-local", host=HOST,
                      timeout=180, num_predict=384)
    orig = b._chat
    b._chat = lambda prompt, system: (RAW.append(orig(prompt, system)), RAW[-1])[1]

    home = tempfile.mkdtemp(prefix="awino-live2-")
    loop = Loop(home, "live-probe2", b, ScriptedJudge())
    loop.set_mission("Inventory the project directory",
                     ["event:tool_called:list_dir"])
    loop.state.record("plan_updated",
                      {"plan": ["List the directory", "Report what is inside"]})
    loop.approve_contract()
    loop.approve_contract(["sandbox"])
    loop.state.record("mode_routed",
                      {"mode": "build", "offered": ["read_file", "list_dir"],
                       "reason": "live-test2"})
    t0 = time.time()
    r = loop.run_user_turn("go")
    dt = time.time() - t0
    print(f"status={r['status']} http_calls={len(b.calls)} wall={dt:.1f}s")
    for e in loop.state.events:
        if e["type"] == "turn_rejected":
            print(f"  rejected attempt {e['data']['attempt']}: {e['data']['errors']}")
        elif e["type"] == "turn_validated":
            print(f"  VALIDATED on attempt {e['data']['attempt']}")
        elif e["type"] == "tool_called":
            print(f"  EXECUTED: {e['data'].get('tool')} {e['data'].get('args')}")
    ok, gaps = verify_done_criteria(loop.state.snapshot, loop.state.events,
                                    loop._search_dirs())
    print(f"criterion_met={ok} gaps={gaps}")
    print(f"said: {r.get('said', '')[:200]}")
    with open("/home/hatch/workspace/awino-rebuild/proof/LIVE_TRANSCRIPT.md", "a") as f:
        f.write(f"\n---\n\n## TEST 1 retry (tightened prompt) — {time.strftime('%H:%M')}\n\n")
        f.write(f"status={r['status']} http_calls={len(b.calls)} criterion_met={ok}\n\n")
        for i, raw in enumerate(RAW):
            f.write(f"### inference {i + 1}\n\n```\n{raw[:1500]}\n```\n\n")


main()
