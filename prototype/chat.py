"""awino chat — the loop-owner REPL.

Normal chat turns go through the owned loop. Slash commands operate the harness.
/project, /mission, /status, /approve, /deny, /done, /help, /quit.
Ctrl-C / Ctrl-D are safe: state is persisted every turn, restart resumes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from loop import Loop
from backends import EchoBackend, ScriptedJudge

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


def main() -> None:
    project = "inbox"
    loop = Loop(HOME, project, EchoBackend(), ScriptedJudge())
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
                loop = Loop(HOME, project, EchoBackend(), ScriptedJudge())
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
            elif cmd == "approve":
                print(loop.approve(arg or None)["said"])
            elif cmd == "deny":
                print(loop.deny(arg or None)["said"])
            elif cmd == "approve-contract":
                scope = [p.strip() for p in (arg or "").split(",") if p.strip()]
                print(loop.approve_contract(scope or None)["said"])
            elif cmd == "done":
                print(loop.request_done()["said"])
            else:
                print(f"unknown command /{cmd} — /help")
            continue
        res = loop.run_user_turn(line)
        print(f"[harness:{res['status']}] {res.get('said', '')}\n")


if __name__ == "__main__":
    main()
