"""Sandbox run: the agentic-learning literature-review mission.

Real: the sidecar, loop, router, stances + rubrics, phase gates, approvals,
file writes, the test command, the verifier worker, stories, brag board.
Scripted: the model's turns (no provider key in this environment) — every
one is written out below, so what the "model" said is fully visible.

Writes timeline.json (user actions + every sidecar event, in order) for
record_video.js, and report.md (which concepts fired, from the journal).
"""
import json
import os
import queue
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "prototype"))
os.environ.pop("AWINO_HOME", None)

import awino_sidecar as S  # noqa: E402
from registry import Registry  # noqa: E402
from tests.common import T  # noqa: E402

SRC = {
    "ReAct": "https://arxiv.org/abs/2210.03629",
    "Reflexion": "https://arxiv.org/abs/2303.11366",
    "Voyager": "https://arxiv.org/abs/2305.16291",
    "MemGPT": "https://arxiv.org/abs/2310.08560",
    "Multi-agent debate": "https://arxiv.org/abs/2305.14325",
    "SPIN (self-play fine-tuning)": "https://arxiv.org/abs/2401.01335",
    "RL with verifiable rewards (GRPO, DeepSeek-R1)": "https://arxiv.org/abs/2501.12948",
}
ROWS = {
    "ReAct": ("2022", "Interleave reasoning traces with tool actions",
              "Same 20 web tasks, success rate"),
    "Reflexion": ("2023", "Verbal self-critique stored as episodic memory",
                  "Retry budget of 3, gain per retry"),
    "Voyager": ("2023", "Growing skill library + automatic curriculum",
                "Skills reused across 5 task families"),
    "MemGPT": ("2023", "Tiered memory the agent pages in and out",
               "Long-horizon tasks past the context window"),
    "Multi-agent debate": ("2023", "Several agents argue, then converge",
                           "Accuracy vs single agent at equal tokens"),
    "SPIN (self-play fine-tuning)": ("2024", "Model improves against its own past outputs",
                                     "Win rate vs base after 2 rounds"),
    "RL with verifiable rewards (GRPO, DeepSeek-R1)": (
        "2025", "RL on checkable outcomes, no reward model",
        "Pass@1 on unit-tested coding tasks"),
}


def review(names):
    lines = ["# Agentic learning: seven approaches",
             "",
             "Sources are the original papers (arXiv). Years are first versions.",
             "",
             "| Approach | Year | Core idea | Source | How we'd test it |",
             "|---|---|---|---|---|"]
    for n in names:
        y, idea, test = ROWS[n]
        lines.append(f"| {n} | {y} | {idea} | {SRC[n]} | {test} |")
    return "\n".join(lines) + "\n"


APPROACHES_YAML = "".join(f"- name: {n}\n  source: {SRC[n]}\n" for n in ROWS)
ALL = list(ROWS)
SIX = ALL[:6]

evq: "queue.Queue[dict]" = queue.Queue()
timeline: list[dict] = [{"kind": "meta", "live": False, "provider": "scripted",
                          "model": "hand-written turns in run_mission.py"}]


def capture(obj):
    obj = json.loads(json.dumps(S.redact(obj), default=str))
    timeline.append({"kind": "event", "ev": obj})
    evq.put(obj)


S._emit = capture


def wait(kinds, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        try:
            e = evq.get(timeout=0.5)
        except queue.Empty:
            continue
        if e.get("event") in kinds:
            return e
    raise SystemExit(f"timed out waiting for {kinds}")


ws = Path(tempfile.mkdtemp(prefix="agentic-learning-"))
(ws / "experiment").mkdir()
shutil.copy(HERE / "check_review.py", ws / "check_review.py")
sc = S.Sidecar()
sc._dispatch({"cmd": "hello", "workspace": str(ws), "provider": "scripted",
              "script": []})
wait({"ready"})
script = sc.loop.backend._inner.script


def model(*turns):
    script.extend(turns)


def user(text):
    timeline.append({"kind": "user", "text": text})
    sc._dispatch({"cmd": "user_message", "text": text})
    return settle()


def settle():
    """Run until the turn really ends; approve every pause (the human
    clicks Approve on each card)."""
    while True:
        e = wait({"turn_result"})
        timeline.append({"kind": "status",
                         "status": json.loads(json.dumps(sc.loop.status(), default=str))})
        if e["result"].get("status") != "awaiting_approval":
            return e
        time.sleep(0.2)  # let the approval card event land first
        pending = [a["id"] for a in sc.loop.state.snapshot["approvals"]
                   if a.get("status") == "pending"]
        for aid in pending:
            timeline.append({"kind": "approve", "id": aid})
            sc._dispatch({"cmd": "approve", "id": aid, "decision": "approve"})


def command(name, args, label):
    timeline.append({"kind": "command", "label": label})
    sc._dispatch({"cmd": "command", "name": name, "args": args})
    return wait({"command_result"})


CAUSE = ["Cause: no shared benchmark exists, so claims across these papers "
         "are not comparable."]
DOUBT = ["The evidence could mislead if the checker only counts rows and "
         "never reads the sources."]

# 1. IDLE: an idea -> planning-grill interview (one question).
model(T(objective="Compare agentic learning approaches", plan=[],
        questions=["What should the review decide: which approach to "
                   "build on, or how to run a fair experiment?"],
        progress_delta="Before planning, one question to pin down the goal."))
user("I want to build an experiment comparing 7 approaches to agentic learning")

# 2. The answer converges: the model records the mission (IDLE -> DEFINE).
model(T(objective="Literature review of 7 agentic learning approaches",
        plan=["Record the mission"],
        tool_calls=[{"name": "set_mission", "args": {
            "text": "Literature review of 7 agentic learning approaches, "
                    "each with a source and a way to test it, plus an "
                    "experiment plan; check_review.py must pass",
            "criteria": "artifact:review.md; artifact:experiment/approaches.yaml"}}],
        progress_delta="The goal and done criteria are crisp; recording the mission."),
      T(objective="Literature review of 7 agentic learning approaches",
        plan=["Challenge the approach list", "Plan it together"],
        progress_delta="Mission recorded. Next I'd challenge the list before we plan."))
user("Both: a lit review that ranks them and a plan for the experiment. "
     "Done when review.md covers all 7 with sources and check_review.py passes.")

# 3. The user asks for a challenge -> steel-man + premortem with rubrics.
model(T(objective="Is comparing these 7 approaches a good idea",
        plan=["Keep the 7, add a shared benchmark", "Date every claim"],
        progress_delta=("Restating your idea: a review of 7 agentic learning "
                        "approaches to pick the newest and best. Strongest "
                        "counter-case below, then how this could fail."),
        assumptions=[
            "Newest is not best: RL with verifiable rewards leads on coding "
            "benchmarks but needs a checker most tasks do not have.",
            "The 7 were published against different benchmarks, so ranking "
            "them from the papers alone would compare apples to oranges.",
            "It fails if we rank by headline numbers instead of running "
            "one shared task set; the tripwire is any row without a test."]))
user("Is comparing these 7 a good idea, or are there newer and better approaches? Challenge me.")

# 4. Human approves the contract (DEFINE -> PLAN).
timeline.append({"kind": "command", "label": "Approve contract"})
sc.loop.approve_contract()

# 5. Plan together: story_plan (Honda first) + stretch_goal (NABC).
model(T(objective="Plan the review", plan=["Record the agreed plan",
                                           "Park the benchmark as a stretch goal"],
        tool_calls=[
            {"name": "story_plan", "args": {
                "title": "Agentic learning review",
                "problem": "No fair comparison of agentic learning approaches exists for our use.",
                "done_criteria": "review.md covers 7 with sources; check_review.py passes",
                "breakdown": "A review needs the same four facts per approach: idea, year, source, test.",
                "surveyed": "Survey papers list many methods but rarely a shared test.",
                "user_guidance": "User wants 7 approaches, newest and best, and to be challenged.",
                "proposal": ("A (Honda): a sourced table of 7 with a test per row [Certain]. "
                             "B: rank by paper benchmarks [Likely misleading]. Pick A."),
                "steps": ("Write review.md and approaches.yaml | 7 rows with sources | a row lacks a source\n"
                          "Run check_review.py | exit 0 | exit 1"),
                "bugatti_brief": "Run all 7 on one shared task suite and publish the leaderboard."}},
            {"name": "stretch_goal", "args": {
                "title": "Shared agentic-learning benchmark",
                "need": "Paper numbers are not comparable across the 7 approaches.",
                "approach": "Run all 7 on 20 fixed tasks with one model and one budget.",
                "benefits": "A defensible ranking and a reusable harness for new methods.",
                "competition": "Public leaderboards mix models and budgets.",
                "steps": ("Pick 20 tasks | tasks frozen | tasks keep changing\n"
                          "Implement 7 runners | each runs 1 task | any runner crashes\n"
                          "Run and score | 7x20 results | missing results")}}],
        progress_delta="Recording the plan we agreed and parking the bigger benchmark idea.",
        assumptions=CAUSE),
      T(objective="Plan the review", plan=["Await scoped approval"],
        progress_delta="Plan recorded: Honda is the sourced review; the benchmark is parked for later.",
        assumptions=CAUSE))
user("Plan it with me. Honda first, keep it small.")

# 6. Human approves the scope (PLAN -> BUILD).
timeline.append({"kind": "command", "label": "Approve scope: review.md, experiment/approaches.yaml"})
sc.loop.approve_contract(["review.md", "experiment/approaches.yaml"])
t1, t2 = [t["id"] for t in Registry(sc.loop.registry.awino_dir).tasks()
          if t.get("story_id")]

# 7. Build (6 of 7 first) -> VERIFY -> check fails -> back to BUILD -> fix
#    -> check passes -> verifier -> REVIEW.
model(
    T(objective="Write the review", plan=["Write review.md", "Write approaches.yaml"],
      tool_calls=[{"name": "task_update", "args": {"id": t1, "status": "doing"}},
                  {"name": "write_file", "args": {"path": "review.md", "content": review(SIX)}},
                  {"name": "write_file", "args": {"path": "experiment/approaches.yaml",
                                                  "content": APPROACHES_YAML}}],
      progress_delta="Writing the review table and the experiment list.", assumptions=CAUSE),
    T(objective="Check the review", plan=["Run the acceptance check"],
      tool_calls=[{"name": "task_update", "args": {"id": t1, "status": "done",
                                                   "evidence": "review.md"}},
                  {"name": "task_update", "args": {"id": t2, "status": "doing"}},
                  {"name": "run_command", "args": {"cmd": f"{sys.executable} check_review.py"}}],
      progress_delta="Running check_review.py.", assumptions=DOUBT),
    T(objective="Fix the review", plan=["Add the missing approach"],
      tool_calls=[{"name": "write_file", "args": {"path": "review.md", "content": review(ALL)}}],
      progress_delta="The check found 6 of 7: adding RL with verifiable rewards.",
      assumptions=["Cause: the newest approach (GRPO, 2025) was left out of the table."]),
    T(objective="Re-check the review", plan=["Re-run the acceptance check"],
      tool_calls=[{"name": "run_command", "args": {"cmd": f"{sys.executable} check_review.py "}},
                  {"name": "task_update", "args": {"id": t2, "status": "done",
                                                   "evidence": "check_review.py"}}],
      progress_delta="Re-running the check.", assumptions=DOUBT),
    T(objective="Review before ship", plan=["Hand over"],
      progress_delta="Verified by the independent check. Reviewing what could still go wrong.",
      assumptions=["It could still mislead if a source is cited but its claim "
                   "is paraphrased wrongly; spot-check two rows."]),
)
user("Build it.")

# 8. Evidence-gated completion (REVIEW -> SHIP).
model(T(objective="Complete", plan=["Claim completion with evidence"],
        tool_calls=[{"name": "attempt_completion", "args": {
            "summary": "review.md covers 7 approaches with sources and tests; check passes."}}],
        progress_delta="Claiming completion; the harness checks the evidence.",
        assumptions=["It could fail if review.md changed after the check ran."]))
user("Ship it.")

# 9. The user closes the story onto the brag board.
story_id = next(s["id"] for s in sc._cmd_stories({})["stories"]
                if s["title"] == "Agentic learning review")
command("story_close", {"id": story_id,
                        "outcome": "Sourced review of 7 agentic learning approaches + experiment plan"},
        "Close story (to brag board)")
stories = sc._cmd_stories({})
timeline.append({"kind": "stories", "ledger": stories})

# ---------------------------------------------------------------- report
ev = sc.loop.state.events


def of(t):
    return [e["data"] for e in ev if e["type"] == t]


phases = ["IDLE", "DEFINE"] + [d["phase"] for d in of("phase_changed")]
if sc.loop.state.snapshot["phase"] != phases[-1]:
    phases.append(sc.loop.state.snapshot["phase"])
stances = []
for d in of("stance_routed"):
    for s in d.get("chain", [d["stance"]]):
        if s not in stances:
            stances.append(s)
modes = []
for d in of("mode_routed"):
    if d.get("mode") not in modes:
        modes.append(d.get("mode"))
runs = [d["result"].get("exit_code") for d in of("tool_result")
        if d["tool"] == "run_command"]
ok, problems = sc.loop.verify_journal()
checks = {
    "Interview (planning-grill) fired": "planning-grill" in stances,
    "Model's set_mission moved IDLE -> DEFINE": bool(of("mission_set")),
    "Challenge fired (steel-man + premortem)": {"steel-man", "premortem"} <= set(stances),
    f"Stance rubrics ran every turn ({len(of('stance_rubric_passed'))} passed, "
    f"{len(of('stance_rubric_failed'))} rejected)": len(of("stance_rubric_passed")) > 0,
    "story_plan wrote the Honda-first plan (3 stances)": len(of("story_plan_stance")) == 3,
    "Stretch goal parked (NABC)": bool(of("stretch_goal_pitched")),
    "Writes paused for approval": len(of("approval_requested")) > 0,
    "BUILD -> VERIFY on write": "VERIFY" in phases,
    "Failing check routed back to BUILD": runs[:1] == [1] and phases.count("BUILD") >= 2,
    "Devil's-advocate on VERIFY": "devil's-advocate" in stances,
    "Independent verifier passed": bool(of("verify_passed")),
    "Evidence-gated completion -> SHIP": sc.loop.state.snapshot["phase"] == "SHIP",
    "Story closed onto the brag board": any(b["title"] == "Agentic learning review"
                                            for b in stories["brag"]),
    "Journal chain intact": ok,
}
lines = ["# SCRIPTED sandbox run: agentic-learning review mission", "",
         "**The model's replies were written by hand in run_mission.py** (no provider key "
         "in the build environment). The harness, tools, gates, files and journal are real. "
         "For a live model, use the live-mission workflow (proof/live_mission/).", "",
         f"Workspace: `{ws}`", "",
         "| Check | Result |", "|---|---|"]
lines += [f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in checks.items()]
lines += ["", f"Phases: {' -> '.join(phases)}",
          f"Modes routed: {', '.join(m for m in modes if m)}",
          f"Stances fired: {', '.join(stances)}",
          f"check_review.py exit codes: {runs}",
          f"Journal problems: {problems or 'none'}", "",
          "## Final review.md", "", (ws / "review.md").read_text()]
(HERE / "report.md").write_text("\n".join(lines))
(HERE / "timeline.json").write_text(json.dumps(timeline, indent=1))
shutil.copy(ws / "review.md", HERE / "review.md")
print("\n".join(lines[:len(checks) + 12]))
sys.exit(0 if all(checks.values()) else 1)
