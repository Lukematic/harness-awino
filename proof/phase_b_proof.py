"""Phase B proof: trusted lifecycle + policy.

Exercises (scripted backend, deterministic):
1. Typed contract coercion at the validation boundary.
2. Immutable revision history across two missions.
3. Phase transition table: legal path ok, illegal jump refused.
4. Time budget exhaustion is terminal; budgets() view.
5. Effect journal + verify_journal (ok, forged duplicate detected).
6. Sandbox manifest + tamper detection.

Appends real output to proof/PHASE_B_TRANSCRIPT.md. Exit 0 = proven.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/hatch/workspace/awino-rebuild/prototype")
from backends import ScriptedBackend, ScriptedJudge
from contract import coerce_turn_contract, ContractTypeError
from loop import Loop
from state import ProjectState

TRANSCRIPT = "/home/hatch/workspace/awino-rebuild/proof/PHASE_B_TRANSCRIPT.md"


def T(**kw):
    d = {"header": "echo", "objective": "o", "plan": ["p"],
         "tool_calls": [], "questions": [], "assumptions": ["Cause: test."],
         "progress_delta": "d", "done_claim": False}
    d.update(kw)
    return d


def main():
    out = []
    home = tempfile.mkdtemp(prefix="awino-phaseb-")

    # 1. typed contract
    t = coerce_turn_contract(T())
    out.append(f"1. typed contract: {type(t).__name__} coerced; "
               f"plan={t.plan}, done_claim={t.done_claim}")
    try:
        coerce_turn_contract(T(plan="not-a-list"))
        out.append("1. FAIL: bad plan accepted")
        raise SystemExit(1)
    except ContractTypeError as e:
        out.append(f"1. type violation rejected: {e}")
    try:
        t.header = "mut"
        out.append("1. FAIL: mutation allowed")
        raise SystemExit(1)
    except ContractTypeError:
        out.append("1. immutability enforced (setattr raises)")

    # 2. immutable revisions
    loop = Loop(home, "pb", ScriptedBackend([T(progress_delta="x")]),
                ScriptedJudge())
    loop.set_mission("First mission", ["manual"])
    loop.set_mission("Second mission", ["manual"])
    hist = loop.state.snapshot["revision_history"]
    assert len(hist) == 2 and hist[0]["text"] == "First mission"
    assert hist[1]["text"] == "Second mission"
    out.append(f"2. revision_history: {len(hist)} entries, "
               f"rev1={hist[0]['revision']} {hist[0]['text']!r}, "
               f"rev2={hist[1]['revision']} {hist[1]['text']!r} (immutable)")

    # 3. phase transitions
    r = loop.request_phase("PLAN", reason="proof")
    assert r["status"] == "ok"
    r = loop.request_phase("REVIEW", reason="illegal jump")
    assert r["status"] == "refused"
    assert loop.state.snapshot["phase"] == "PLAN"
    refused = [e for e in loop.state.events if e["type"] == "transition_refused"]
    assert refused
    out.append(f"3. legal PLAN ok; illegal PLAN->REVIEW refused "
               f"(transition_refused event seq {refused[0]['seq']})")

    # 4. time budget + budgets() view
    loop2 = Loop(home, "pb2", ScriptedBackend([T(progress_delta="x")]),
                 ScriptedJudge(), config={"max_seconds": 3600})
    loop2.set_mission("M", ["manual"])
    loop2.state.snapshot["mission_start_ts"] = time.time() - 7200
    r = loop2.run_user_turn("hi")
    assert r["status"] == "budget_exhausted", r["status"]
    out.append(f"4. time budget exhausted -> {r['status']} (terminal)")
    loop3 = Loop(home, "pb3", ScriptedBackend([T(progress_delta="x")]),
                 ScriptedJudge())
    loop3.set_mission("M", ["manual"])
    loop3.run_user_turn("hi")
    b = loop3.budgets()
    out.append(f"4. budgets(): turns {b['turns']['used']}/{b['turns']['limit']} "
               f"(remaining {b['turns']['remaining']}), "
               f"tokens used {b['tokens']['used']}, "
               f"seconds remaining {b['seconds']['remaining']:.0f}")

    # 5. effect journal
    be = ScriptedBackend([
        T(tool_calls=[{"name": "list_dir", "args": {}}], progress_delta="l"),
        T(tool_calls=[{"name": "read_file", "args": {"path": "x"}}],
          progress_delta="r"),
    ])
    lj = Loop(home, "pb4", be, ScriptedJudge())
    lj.set_mission("M", ["manual"])
    lj.run_user_turn("go")
    lj.run_user_turn("go")
    j = lj.effect_journal()
    assert [e["tool"] for e in j] == ["list_dir", "read_file"]
    ok, problems = lj.verify_journal()
    assert ok, problems
    out.append(f"5. effect_journal: {[e['tool'] for e in j]} in order; "
               f"verify_journal ok")
    # forged duplicate
    orig = [e for e in lj.state.events if e["type"] == "tool_result"][0]
    lj.state.events.append({"seq": 999, "id": "forged", "ts": 1.0,
                            "type": "tool_result",
                            "data": dict(orig["data"], call_id="t9.9",
                                         reused=False)})
    ok, problems = lj.verify_journal()
    assert not ok and any("duplicate" in p for p in problems)
    out.append(f"5. forged duplicate detected: {problems[0][:70]}...")

    # 6. sandbox manifest
    sb = lj.sandbox
    sb.write_file("proof.txt", "phase b")
    m = sb.manifest()
    assert "proof.txt" in m
    ok, problems = sb.verify_manifest()
    assert ok
    out.append(f"6. manifest tracks {list(m)}; verify ok")
    Path(sb.root, "proof.txt").write_text("tampered")
    ok, problems = sb.verify_manifest()
    assert not ok
    out.append(f"6. external tamper detected: {problems[0]}")

    with open(TRANSCRIPT, "a") as f:
        f.write(f"\n## Phase B proof (2026-09-22)\n\n")
        for line in out:
            f.write(f"- {line}\n")
    print("\n".join(out))
    print("PASS — Phase B trusted lifecycle + policy proven")


main()
