"""awino chat — the loop-owner REPL.

Normal chat turns go through the owned loop. Slash commands operate the harness.
/project, /mission, /status, /approve, /deny, /done, /inspect, /replay,
/journal, /learnings, /rollback, /help, /quit.
Ctrl-C / Ctrl-D are safe: state is persisted every turn, restart resumes.

Backend selection (env AWINO_BACKEND):
  echo     (default) safe echo backend, no model calls
  local    OllamaBackend via OLLAMA_HOST/OLLAMA_MODEL (local only, never paid)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from loop import Loop
from backends import EchoBackend, OllamaBackend
from judges import build_judge_panel


def make_backend():
    """Phase E: choose backend via AWINO_BACKEND env (echo|local)."""
    which = os.environ.get("AWINO_BACKEND", "echo").lower()
    if which == "local":
        return OllamaBackend()
    elif which == "echo":
        return EchoBackend()
    else:
        raise ValueError(f"unknown AWINO_BACKEND={which!r} (use echo|local)")

HOME = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".awino-loop"

HELP = """\
/project <id>            switch project (state dir switches, missions persist)
/mission <text> ;; <criterion>[; ...]
                         set mission + done criteria.
                         criterion: artifact:<path> | event:<type>[:<tool>] | manual
/status                  mission, phase, mode, criteria, questions, approvals
/approve [id]            grant a pending approval (default: latest)
/deny [id]               deny a pending approval
/approve-contract [scope]  approve the contract (DEFINE->PLAN; PLAN->BUILD with SCOPE file list)
/done                    request completion (criteria verified by code)
/inspect <call_id>       show tool call args + result/digest
/replay [n]              show last n events (default 10)
/journal                 show the effect journal (ordered tool executions)
/learnings               list the learning record
/synthesize [n]          synthesize learning n into a verified skill
                         (refused unless sandbox-verified; latest if n omitted)
/rollback <seq>          rollback to sequence number (operator only)
/help                    this text
/quit                    exit (state is saved; restart resumes)
Anything else is a chat turn through the owned loop.
"new objective: ..." sets/updates the objective mid-conversation."""


def parse_mission_arg(arg: str):
    if ";;" in arg:
        text, crit = arg.split(";;", 1)
        criteria = [c.strip() for c in crit.split(";") if c.strip()]
    else:
        text, criteria = arg, ["manual"]
    return text.strip(), criteria


def session_start(project_dir=None) -> dict | None:
    """Session-start auto-init (Track A/H).

    When a chat session starts in a directory that is not an awino project
    yet (no `.awino/project.yaml`), run the full init flow automatically —
    the user never has to type `awino init` by hand — then print one brief
    plain-language summary of what was set up, before the mission proceeds.
    Returns the auto-init result, or None when the directory was already a
    project (silent). Never raises.
    """
    from bootstrap import session_start_auto_init  # lazy: keep REPL start fast
    result = session_start_auto_init(
        Path(project_dir) if project_dir else Path.cwd())
    if result:
        for line in result["summary"]:
            print(line)
        print()
    return result


def main() -> None:
    project = "inbox"
    session_start()  # auto-init: the primary path; `awino init` is the override
    loop = Loop(HOME, project, make_backend(), build_judge_panel())
    print(f"awino chat — home={HOME} project={project}")
    print("Type /help for commands. Ctrl-C/D exits safely; restart resumes.\n")
    while True:
        try:
            line = input(f"awino:{project}> ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye — state saved.")
            break
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, arg = line[1:].partition(" ")
            arg = arg.strip()
            if cmd == "quit":
                print("bye — state saved.")
                break
            elif cmd == "help":
                print(HELP)
            elif cmd == "project":
                if not arg:
                    print("usage: /project <id>")
                    continue
                project = arg
                loop = Loop(HOME, project, make_backend(), build_judge_panel())
                st = loop.status()
                print(f"switched to project '{project}' "
                      f"(phase={st['phase']}, mission={st['mission']!r})")
            elif cmd == "mission":
                if not arg:
                    print("usage: /mission <text> ;; <criterion>[; ...]")
                    continue
                text, criteria = parse_mission_arg(arg)
                try:
                    m = loop.set_mission(text, criteria)
                except ValueError as e:
                    print(f"bad criteria: {e}")
                    continue
                print(f"mission set (kind={m['kind']}, revision={m['revision']}): {text}")
            elif cmd == "status":
                st = loop.status()
                print(f"project={st['project']} phase={st['phase']} mode={st['mode']} "
                      f"turns={st['turns']} done={st['done']}")
                print(f"mission: {st['mission']}")
                for c in st["criteria"]:
                    print(f"  [{'x' if c['ok'] else ' '}] {c['label']}")
                for q in st["open_questions"]:
                    print(f"  ? {q}")
                for p in st["progress"]:
                    print(f"  · {p}")
                for l in st["learnings"]:
                    print(f"  ◈ [{l['kind']}] {l['text'][:120]}")
                if st["pending_approvals"]:
                    print(f"  pending approvals: {st['pending_approvals']}")
                print(f"  contract approved: {st['contract_approved']} | stance: {st['stance']}")
                print(f"  {st['next_action']}")
                for f in st["flags"]:
                    print(f"  ! {f}")
            elif cmd == "learnings":
                learnings = loop.state.snapshot.get("learnings", [])
                if not learnings:
                    print("(no learnings recorded yet)")
                for l in learnings:
                    print(f"[{l['kind']}] {l['text']}")
            elif cmd == "synthesize":
                try:
                    idx = int(arg) if arg else -1
                except ValueError:
                    print("usage: /synthesize [n]  (learning index, default: latest)")
                    continue
                r = loop.synthesize_learning(idx)
                if r["status"] == "admitted":
                    print(f"admitted skill {r['name']} "
                          f"(sha256 {r['sha256'][:16]}..., "
                          f"{len(r['checks'])} checks passed)")
                else:
                    print(f"refused [{r['code']}]: {r['detail']}")
            elif cmd == "approve":
                print(loop.approve(arg or None)["said"])
            elif cmd == "deny":
                print(loop.deny(arg or None)["said"])
            elif cmd == "approve-contract":
                scope = [p.strip() for p in (arg or "").split(",") if p.strip()]
                print(loop.approve_contract(scope or None)["said"])
            elif cmd == "done":
                print(loop.request_done()["said"])
            elif cmd == "inspect":
                # Phase E: show tool_called args + result for a call_id
                if not arg:
                    print("usage: /inspect <call_id>")
                    continue
                called = loop.state.find_event("tool_called", arg)
                if not called:
                    print(f"no tool_called for {arg}")
                    continue
                print(f"tool: {called['data']['tool']}")
                print(f"args: {called['data']['args']}")
                # find the result
                for e in loop.state.events:
                    if (e["type"] == "tool_result" and
                            e["data"]["call_id"] == arg):
                        res = e["data"]["result"]
                        print(f"result: {str(res)[:500]}")
                        if isinstance(res, dict) and "digest" in res:
                            print(f"digest: {res['digest']}")
                        break
            elif cmd == "replay":
                # Phase E: show last n events compactly
                n = int(arg) if arg.isdigit() else 10
                for e in loop.state.events[-n:]:
                    print(f"{e['seq']:4d} {e['type']:20s} "
                          f"{str(e['data'])[:80]}")
            elif cmd == "journal":
                # Phase E: show the effect journal (Phase B)
                for j in loop.effect_journal():
                    print(f"{j['seq']:4d} {j['tool']:12s} "
                          f"{str(j['args'])[:60]} "
                          f"{'(reused)' if j['reused'] else ''}")
            elif cmd == "rollback":
                # Phase E: rollback to a sequence number (see E4)
                if not arg.isdigit():
                    print("usage: /rollback <seq>")
                    continue
                r = loop.rollback(int(arg))
                print(r["said"])
            else:
                print(f"unknown command /{cmd} — /help")
            continue
        res = loop.run_user_turn(line)
        print(f"[harness:{res['status']}] {res.get('said', '')}\n")


if __name__ == "__main__":
    main()
