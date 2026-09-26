"""LIVE run of the agentic-learning mission against a real model.

Nothing is scripted on the model side. This script plays only the human:
it types the user's messages, approves the contract and the scope, and
clicks Approve on each approval card — the same things you would do in
VS Code. Every model reply, tool call, file and verdict comes from the
provider you point it at, through the same sidecar the extension runs.

It does not assert success. report.md records what fired and what did not.

Usage (the live-mission GitHub workflow runs this with your key as a
secret):
  AWINO_API_KEY=...  python run_live.py --provider openai \\
      --endpoint https://openrouter.ai/api/v1 --model <model> --out live-out
  ANTHROPIC_API_KEY=... python run_live.py --provider anthropic \\
      --model <model> --out live-out
"""
import argparse
import json
import os
import queue
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
os.environ.pop("AWINO_HOME", None)

import awino_sidecar as S  # noqa: E402

MESSAGES = [
    "I want to build an experiment comparing 7 approaches to agentic learning.",
    "Goal: a written literature review paper on 7 agentic learning approaches, plus "
    "the experiment to compare them. review.md must be a real paper of at least 1,500 "
    "words with these sections: Abstract; Scope and method; one ### subsection per "
    "approach (mechanism, evidence, where it breaks); Comparison (a table); Challenge "
    "(is newest best?); Experiment (controls, tasks, metrics, success and failure "
    "criteria); Recommendation; References with arXiv links. "
    "experiment/approaches.yaml lists the 7 as '- name: ...' entries. "
    "Done when `python check_paper.py` passes.",
    "Is comparing these 7 a good idea, or are there newer and better approaches? "
    "Challenge me before we plan.",
    "Plan it with me. Honda first, keep it small, and record the plan.",
    "Build it: write the paper to review.md and write experiment/approaches.yaml, then "
    "run `python check_paper.py` and fix anything it reports until it passes.",
]
CONTINUE = "Keep going until check_paper.py passes and verification signs off."
SHIP = "Ship it."
SCOPE = ["review.md", "experiment/approaches.yaml"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=["openai", "anthropic"])
    ap.add_argument("--endpoint", default="")
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--turn-timeout", type=int, default=420)
    ap.add_argument("--max-continues", type=int, default=4)
    a = ap.parse_args()

    key_env = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "AWINO_API_KEY"
    if not os.environ.get(key_env):
        raise SystemExit(f"{key_env} is not set — add it as a repository secret.")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    evq: "queue.Queue[dict]" = queue.Queue()
    timeline: list[dict] = [{"kind": "meta", "live": True, "provider": a.provider,
                             "model": a.model, "endpoint": a.endpoint,
                             "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]
    notes: list[str] = []

    def capture(obj):
        obj = json.loads(json.dumps(S.redact(obj), default=str))
        timeline.append({"kind": "event", "t": time.time(), "ev": obj})
        evq.put(obj)

    S._emit = capture

    def wait(kinds, timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                e = evq.get(timeout=0.5)
            except queue.Empty:
                continue
            if e.get("event") in kinds:
                return e
        return None

    ws = Path(tempfile.mkdtemp(prefix="agentic-learning-live-"))
    (ws / "experiment").mkdir()
    shutil.copy(HERE / "check_paper.py", ws / "check_paper.py")

    sc = S.Sidecar()
    hello = {"cmd": "hello", "workspace": str(ws), "provider": a.provider, "model": a.model}
    if a.endpoint:
        hello["endpoint"] = a.endpoint
    sc._dispatch(hello)
    if not wait({"ready"}, 60):
        raise SystemExit("sidecar never became ready")

    def phase():
        return sc.loop.state.snapshot["phase"]

    def status():
        timeline.append({"kind": "status",
                         "status": json.loads(json.dumps(sc.loop.status(), default=str))})

    def user(text):
        timeline.append({"kind": "user", "text": text})
        sc._dispatch({"cmd": "user_message", "text": text})
        while True:
            e = wait({"turn_result"}, a.turn_timeout)
            if e is None:
                notes.append(f"turn timed out after {a.turn_timeout}s: {text[:60]}")
                return None
            status()
            if e["result"].get("status") != "awaiting_approval":
                return e
            time.sleep(0.2)
            for ap_ in [x for x in sc.loop.state.snapshot["approvals"]
                        if x.get("status") == "pending"]:
                timeline.append({"kind": "approve", "id": ap_["id"],
                                 "tool": ap_.get("tool")})
                sc._dispatch({"cmd": "approve", "id": ap_["id"], "decision": "approve"})

    def human(label, fn):
        timeline.append({"kind": "command", "label": label})
        r = fn()
        notes.append(f"{label}: {r.get('status') if isinstance(r, dict) else r}")
        status()

    user(MESSAGES[0])
    user(MESSAGES[1])
    if not sc.loop.state.snapshot.get("mission"):
        notes.append("mission not set after the goal message; nudged once")
        user("That's the goal and the done criteria — please record the mission.")
    user(MESSAGES[2])
    if phase() == "DEFINE":
        human("Approve contract", lambda: sc.loop.approve_contract())
    user(MESSAGES[3])
    if phase() == "PLAN":
        human("Approve scope: " + ", ".join(SCOPE), lambda: sc.loop.approve_contract(SCOPE))
    user(MESSAGES[4])
    for _ in range(a.max_continues):
        if phase() not in ("BUILD", "VERIFY"):
            break
        user(CONTINUE)
    if phase() == "REVIEW":
        user(SHIP)

    # --------------------------------------------------------- report
    ev = sc.loop.state.events

    def of(t):
        return [e["data"] for e in ev if e["type"] == t]

    phases = ["IDLE"] + (["DEFINE"] if of("mission_set") else []) + \
        [d["phase"] for d in of("phase_changed")]
    stances, modes = [], []
    for d in of("stance_routed"):
        for s in d.get("chain", [d["stance"]]):
            if s not in stances:
                stances.append(s)
    for d in of("mode_routed"):
        if d.get("mode") and d["mode"] not in modes:
            modes.append(d["mode"])
    declared = [(d["stance"], d["trigger"][7:]) for d in of("stance_routed")
                if str(d.get("trigger", "")).startswith("model: ")]
    runs = [d["result"].get("exit_code") for d in of("tool_result")
            if d["tool"] == "run_command"]
    tools = [d["tool"] for d in of("tool_result")]
    rejected = [e for d in of("turn_rejected") for e in d.get("errors", [])]
    ok, problems = sc.loop.verify_journal()
    fired = {
        "Interview (planning-grill)": "planning-grill" in stances,
        "Model called set_mission": "set_mission" in tools,
        "Challenge (steel-man)": "steel-man" in stances,
        "Model called story_plan": "story_plan" in tools,
        "Model pitched a stretch goal": "stretch_goal" in tools,
        "Writes paused for approval": bool(of("approval_requested")),
        "Reached VERIFY": "VERIFY" in phases,
        "Failing check routed back to BUILD": 1 in runs and phases.count("BUILD") >= 2,
        "check_paper.py passed (a real paper)": 0 in runs,
        "Independent verifier passed": bool(of("verify_passed")),
        "Reached SHIP": sc.loop.state.snapshot["phase"] == "SHIP",
        "Journal chain intact": ok,
    }
    lines = [f"# LIVE run: agentic-learning mission ({a.provider} · {a.model})", "",
             "No model turns were scripted. The human side (messages, contract and scope "
             "approval, Approve clicks) is in run_live.py.", "",
             "| What | Observed |", "|---|---|"]
    lines += [f"| {k} | {'yes' if v else 'no'} |" for k, v in fired.items()]
    lines += ["", f"Phases: {' -> '.join(phases)}",
              f"Final phase: {sc.loop.state.snapshot['phase']}",
              f"Modes routed: {', '.join(modes)}",
              f"Stances fired: {', '.join(stances)}",
              f"check_paper.py exit codes: {runs}",
              f"Tools the model called: {', '.join(tools) or 'none'}",
              f"Tokens charged (approx): {sum(d.get('tokens', 0) for d in of('tokens_charged'))}",
              "", "## Stances the model chose, and why"]
    lines += [f"- {s}: {why}" for s, why in declared] or ["- (none declared; router fallback)"]
    lines += ["", "## Turns the harness rejected"]
    lines += [f"- {r[:300]}" for r in rejected] or ["- none"]
    lines += ["", "## Driver notes"] + ([f"- {n}" for n in notes] or ["- none"])
    lines += ["", f"Journal problems: {problems or 'none'}"]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    (out / "timeline.json").write_text(json.dumps(timeline, indent=1))
    shutil.copytree(ws, out / "workspace", dirs_exist_ok=True)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
