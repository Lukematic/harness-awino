"""LIVE proof: real local model (Qwen2.5-1.5B GGUF via llama.cpp) drives the
A.W.I.N.O. per-turn contract pipeline over HTTP.

Run while the local server is up:
    ~/workspace/ollama/venv/bin/python llama_server.py <model.gguf> 11434

Proves (or honestly records):
  TEST 1: a genuine local-model call validates through the pipeline and its
          tool call executes (event:tool_called:list_dir criterion).
  TEST 2: tampered/unauthorized calls execute ZERO tools (no tool_called
          events, no pwned.txt in the sandbox).
  TEST 3: a no-plan BUILD state refuses BEFORE the backend is invoked
          (live backend.calls stays empty).

Writes proof/LIVE_TRANSCRIPT.md with raw outputs, retries, JSON failures.
"""
import json
import os
import sys
import tempfile
import time

PROTO = "/home/hatch/workspace/awino-rebuild/prototype"
sys.path.insert(0, PROTO)

from backends import OllamaBackend, HostileBackend, ScriptedJudge  # noqa: E402
from loop import Loop  # noqa: E402

HOST = "http://127.0.0.1:11434"
PROOF = "/home/hatch/workspace/awino-rebuild/proof"
RAW_LOG = []

out_lines = []


def log(s=""):
    out_lines.append(s)
    print(s, flush=True)


def live_backend(**kw):
    b = OllamaBackend(model="qwen2.5:1.5b-local", host=HOST,
                      timeout=180, num_predict=384, **kw)
    orig_chat = b._chat

    def spy(prompt, system):
        text = orig_chat(prompt, system)
        RAW_LOG.append(text)
        return text

    b._chat = spy
    return b


def make_build_loop(backend, offered=("read_file", "list_dir")):
    home = tempfile.mkdtemp(prefix="awino-live-")
    loop = Loop(home, "live-probe", backend, ScriptedJudge())
    loop.set_mission("Inventory the project directory",
                     ["event:tool_called:list_dir"])
    loop.state.record("plan_updated",
                      {"plan": ["List the directory", "Report what is inside"]})
    loop.approve_contract()
    loop.approve_contract(["sandbox"])
    loop.state.record("mode_routed",
                      {"mode": "build", "offered": list(offered), "reason": "live-test"})
    assert loop.state.snapshot["phase"] == "BUILD", loop.state.snapshot["phase"]
    return loop, home


def tool_called_events(loop):
    return [e for e in loop.state.events if e["type"] == "tool_called"]


# ---------------------------------------------------------------- TEST 1
log("# LIVE LOCAL-MODEL PROOF — " + time.strftime("%Y-%m-%d %H:%M %Z"))
log()
log("Model: Qwen2.5-1.5B-Instruct Q4_K_M GGUF, llama.cpp server, 127.0.0.1:11434")
log("Backend: OllamaBackend over real HTTP (stdlib urllib, /v1/chat/completions)")
log()
log("## TEST 1 — genuine local-model call validates + executes")
t0 = time.time()
backend = live_backend()
loop, home = make_build_loop(backend)
r = loop.run_user_turn("go")
dt = time.time() - t0
log(f"result status: {r['status']}")
log(f"backend HTTP calls made: {len(backend.calls)} (each = one real model inference)")
log(f"wall time: {dt:.1f}s")
validated = [e for e in loop.state.events if e["type"] == "turn_validated"]
rejected = [e for e in loop.state.events if e["type"] == "turn_rejected"]
log(f"turn_validated events: {len(validated)}; turn_rejected events: {len(rejected)}")
for e in rejected:
    log(f"  rejected attempt {e['data']['attempt']}: {e['data']['errors']}")
calls = tool_called_events(loop)
log(f"tool_called events: {len(calls)}")
for e in calls:
    log(f"  executed: {e['data'].get('tool')} args={e['data'].get('args')}")
from contract import verify_done_criteria
ok, gaps = verify_done_criteria(loop.state.snapshot, loop.state.events,
                                loop._search_dirs())
log(f"criterion event:tool_called:list_dir satisfied: {ok} (gaps={gaps})")
if RAW_LOG:
    log(f"raw model output (attempt 1, first 600 chars):")
    log("  " + RAW_LOG[0][:600].replace("\n", "\n  "))
log()

# ---------------------------------------------------------------- TEST 2
log("## TEST 2 — tampered unauthorized calls execute ZERO tools")
for attacks, tool_name in [(["unoffered_tool"], "exec_shell"),
                           (["advise_write"], "write_file")]:
    hb = HostileBackend(attacks, tool_name=tool_name)
    loop2, home2 = make_build_loop(hb, offered=("read_file", "list_dir", "write_file"))
    r2 = loop2.run_user_turn("go")
    called = tool_called_events(loop2)
    pwned = os.path.exists(os.path.join(home2, "sandbox", "pwned.txt"))
    # also check project dir sandbox
    import pathlib
    pwned_any = any(pathlib.Path(home2).rglob("pwned.txt"))
    log(f"attack={attacks[0]} tool={tool_name}: status={r2['status']} "
        f"tool_called={len(called)} pwned.txt_created={pwned_any}")
    assert len(called) == 0, f"VIOLATION: tool executed under {attacks}"
    assert not pwned_any, f"VIOLATION: pwned.txt written under {attacks}"
log("PASS — zero tool executions across tampered attacks.")
log()

# ---------------------------------------------------------------- TEST 3
log("## TEST 3 — no-plan BUILD state refuses BEFORE the backend acts")
backend3 = live_backend()
home3 = tempfile.mkdtemp(prefix="awino-live-")
loop3 = Loop(home3, "live-noprobe", backend3, ScriptedJudge())
loop3.set_mission("Write the file", ["manual"])
loop3.approve_contract()
loop3.approve_contract(["out.txt"])
assert loop3.state.snapshot["phase"] == "BUILD"
assert not loop3.state.snapshot["plan"]
r3 = loop3.run_user_turn("go")
log(f"status={r3['status']} breaks={r3.get('breaks')}")
log(f"live backend HTTP calls made: {len(backend3.calls)}")
assert r3["status"] == "contract_refused" and "NO_PLAN" in r3["breaks"]
assert backend3.calls == [], "VIOLATION: live backend was invoked on refusal"
assert not tool_called_events(loop3)
log("PASS — refused pre-turn; the real model server saw zero requests.")
log()

log("## SUMMARY")
log(f"TEST 1: status={r['status']} http_calls={len(backend.calls)} "
    f"tools_executed={len(calls)} criterion_met={ok}")
log("TEST 2: PASS (zero tool executions under tampered attacks)")
log("TEST 3: PASS (no-plan refusal before any backend call)")
log()
log(f"Raw model outputs captured: {len(RAW_LOG)}")

with open(os.path.join(PROOF, "LIVE_TRANSCRIPT.md"), "w") as f:
    f.write("\n".join(out_lines) + "\n")
    f.write("\n---\n\n## Raw model outputs\n\n")
    for i, raw in enumerate(RAW_LOG):
        f.write(f"### inference {i + 1}\n\n```\n{raw[:2000]}\n```\n\n")
log("Transcript written to proof/LIVE_TRANSCRIPT.md")
