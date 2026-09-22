"""Phase D proof: end-to-end + delegation.

1. Worker isolation: two workers, disjoint owned files; worker A cannot
   write B's files (ScopeViolation).
2. Shared budget: parent max_turns=4, two workers x2 each -> third spawn
   refused (budget exhausted).
3. Full journey: DEFINE->PLAN->BUILD->VERIFY->REVIEW->SHIP in order,
   no skips (illegal jumps refused).
4. Collect: worker artifacts merged into parent.

Appends to proof/PHASE_D_TRANSCRIPT.md. Exit 0 = proven.
"""
import sys
import tempfile

sys.path.insert(0, "/home/hatch/workspace/awino-rebuild/prototype")
from tests.common import make_loop
from loop import Loop
from backends import ScriptedBackend, ScriptedJudge

TRANSCRIPT = "/home/hatch/workspace/awino-rebuild/proof/PHASE_D_TRANSCRIPT.md"


def main():
    out = []

    # 1. Worker isolation
    loop, home = make_loop(config={"max_turns": 10})
    w1 = loop.spawn_worker("Task A", owned_files=["a/"],
                           budget_share={"max_turns": 2})
    w2 = loop.spawn_worker("Task B", owned_files=["b/"],
                           budget_share={"max_turns": 2})
    # w1 tries to write to b/ (w2's files)
    r = w1["loop"]._execute_single("t1.0", "write_file",
                                   {"path": "b/evil.txt", "content": "x"},
                                   "idem-d1")
    assert "ScopeViolation" in str(r["result"])
    out.append("1. worker A cannot write worker B's files (ScopeViolation)")

    # w1 CAN write its own files
    r = w1["loop"]._execute_single("t1.1", "write_file",
                                   {"path": "a/ok.txt", "content": "x"},
                                   "idem-d2")
    assert "ScopeViolation" not in str(r["result"])
    out.append("1b. worker A can write its own files (a/ok.txt)")

    # 2. Shared budget
    loop2, _ = make_loop(config={"max_turns": 4})
    loop2.spawn_worker("T1", ["a/"], {"max_turns": 2})
    loop2.spawn_worker("T2", ["b/"], {"max_turns": 2})
    try:
        loop2.spawn_worker("T3", ["c/"], {"max_turns": 1})
        out.append("2. FAIL: budget not enforced")
        raise SystemExit(1)
    except RuntimeError as e:
        assert "budget exhausted" in str(e).lower()
        out.append(f"2. third worker refused (budget exhausted: {e})")

    # 3. Full journey order
    loop3, _ = make_loop()
    loop3.set_mission("M", ["manual"])
    journey = ["PLAN", "BUILD", "VERIFY", "REVIEW", "SHIP"]
    for target in journey:
        if target == "PLAN":
            r = loop3.approve_contract()
        elif target == "BUILD":
            r = loop3.approve_contract(["out.txt"])
        else:
            r = loop3.request_phase(target, reason="proof")
        assert r["status"] == "ok", f"failed at {target}"
    phases = [e["data"]["phase"] for e in loop3.state.events
              if e["type"] == "phase_changed"]
    assert phases == journey, f"order wrong: {phases}"
    out.append(f"3. journey { '->'.join(phases) } in order, no skips")

    # illegal jump refused
    loop4, _ = make_loop()
    loop4.set_mission("M", ["manual"])
    r = loop4.request_phase("SHIP", reason="skip")
    assert r["status"] == "refused"
    out.append("3b. illegal DEFINE->SHIP jump refused")

    # 4. Collect
    w = loop.spawn_worker("Collect test", ["out/"], {"max_turns": 2})
    w["loop"]._execute_single("t1.0", "write_file",
                              {"path": "out/done.txt", "content": "x"},
                              "idem-d3")
    result = loop.collect_worker(w["worker_id"])
    assert "out/done.txt" in str(result["artifacts"])
    out.append(f"4. collect merged artifacts: {result['artifacts']}")

    with open(TRANSCRIPT, "a") as f:
        f.write("\n## Phase D proof (2026-09-22)\n\n")
        for line in out:
            f.write(f"- {line}\n")
    print("\n".join(out))
    print("PASS — Phase D end-to-end + delegation proven")


main()
