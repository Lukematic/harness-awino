"""awino console entry point.

Commands:
  awino chat [home]   start the loop-owner REPL (full no-bypass guarantee)
  awino init [dir]    one-command project bootstrap: checklist -> venv ->
                      just/make -> seeds/registry -> project.yaml -> scaffold.
                      Works in empty dirs AND adopts existing projects
                      without clobbering.
  awino status [dir]  plain-language dashboard: environment, active role
                      mode + why, DAG progress, blockers, last breadcrumb.
  awino plan [dir]    the mission's task DAG, simply: what's next, what's
                      blocked, by what.

You never have to type `awino init` by hand: when a chat session starts in
a directory without `.awino/project.yaml`, the harness runs this same init
flow automatically and reports what it set up in one brief summary. `awino
init` remains as the explicit manual command / override.

Errors are plain language — what happened, what it means, the one next
action. Never a bare traceback.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

USAGE = """usage:
  awino chat [home]    start the loop-owner REPL
  awino init [dir]     bootstrap a project (empty or existing)
  awino status [dir]  plain-language project dashboard
  awino plan [dir]    task DAG: what's next, what's blocked"""


def _plain_error(context: str, exc: BaseException) -> int:
    """What happened / what it means / one next action. No traceback."""
    print(f"awino: {context} failed.\n"
          f"  what happened: {type(exc).__name__}: {exc}\n"
          f"  what it means: the command stopped before changing anything "
          f"it hadn't already finished.\n"
          f"  next action: fix the cause above and re-run; nothing was "
          f"left half-written.",
          file=sys.stderr)
    return 1


def _resolve_dir(args: list[str]) -> Path:
    d = Path(args[0]) if args else Path.cwd()
    return d.resolve()


def cmd_init(args: list[str]) -> int:
    """awino init [dir] — the 'boom' entry point."""
    target = _resolve_dir(args)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"awino init: can't create {target}: {e}\n"
              f"  next action: pick a writable directory and retry.",
              file=sys.stderr)
        return 1
    # Import here so `awino --help` never pays import cost / crashes.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        # The shared one-command flow: checklist -> registry -> seed import.
        # Identical to what a chat session runs automatically on start, so
        # the manual command and the auto path can never drift apart.
        from bootstrap import full_init_flow
        report = full_init_flow(target)
    except Exception as e:  # noqa: BLE001 — plain language, no traceback
        return _plain_error("init", e)
    print(f"awino init: {target}")
    for c in report["checks"]:
        mark = {"ok": "ok", "warn": "!", "fail": "X"}.get(c["status"], "?")
        fixed = " (fixed)" if c.get("fixed") else ""
        print(f"  [{mark}] {c['name']}: {c['detail']}{fixed}")
    if report.get("seeds_imported"):
        n = report["seeds_imported"]
        print(f"  [ok] seeds: imported {n} seed task"
              f"{'s' if n != 1 else ''} into the registry")
    if report.get("breadcrumbs"):
        print("  notes:")
        for b in report["breadcrumbs"]:
            print(f"  - {b}")
    if report["ok"]:
        print("ready: project bootstrapped — start a mission with `awino chat`.")
        return 0
    print("not ready: one or more checks failed (see X above).\n"
          "  next action: address the failed check, then `awino init` again —\n"
          "  it only fills in what's missing.",
          file=sys.stderr)
    return 1


def _read_yaml(path: Path) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from bootstrap import read_project_yaml
        return read_project_yaml(path)
    except Exception:
        return {}


def cmd_status(args: list[str]) -> int:
    """awino status [dir] — plain-language dashboard, no jargon."""
    target = _resolve_dir(args)
    awino_dir = target / ".awino"
    if not awino_dir.is_dir():
        print(f"awino status: {target} is not an awino project yet "
              f"(no .awino/ folder).\n"
              f"  next action: run `awino init` there first.",
              file=sys.stderr)
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from registry import Registry
        import modes as _modes
        proj = _read_yaml(awino_dir / "project.yaml")
        reg = Registry(awino_dir)
        reg.ensure()

        print(f"project: {proj.get('project', target.name)}")
        # Environment, in plain words.
        py = shutil.which("python3")
        print("environment:")
        print(f"  python3: {'found' if py else 'missing'}")
        venv = target / ".venv"
        print(f"  virtual env: {'ready (.venv)' if (venv / 'bin' / 'python').exists() else 'not set up'}")
        runner = ("just" if (target / "justfile").exists()
                  or (target / "Justfile").exists()
                  else ("make" if (target / "Makefile").exists() else "none yet"))
        print(f"  task runner: {runner}")
        print(f"  version control: {'git repo' if (target / '.git').is_dir() else 'no git repo'}")

        # Active role lens + why.
        role = _modes.read_role_state(awino_dir)
        if role.get("role"):
            print(f"role lens: {role['role']}")
            print(f"  why: {role.get('reason', '(no reason recorded)')}")
        else:
            print("role lens: none yet (routed at mission start)")

        # DAG progress in plain words.
        dag = reg.dag_summary()
        total = dag["total"]
        if total:
            print(f"work: {dag['done']} of {total} tasks done, "
                  f"{dag['doing']} in progress, {dag['open']} open, "
                  f"{dag['blocked']} blocked")
            nxt = reg.whats_next(limit=3)
            if nxt:
                print("  up next:")
                for t in nxt:
                    print(f"  - {t['text'][:70]}")
            blocked = reg.unblock_report()
            if blocked:
                print("  blocked:")
                for b in blocked[:3]:
                    by = ", ".join(x["text"][:40] for x in b["blocked_by"])
                    print(f"  - {b['task']['text'][:60]} (waiting on: {by})")
        else:
            print("work: no tasks yet (the task list is built at mission start)")

        # Last breadcrumb: the exact stop point.
        crumbs = reg.breadcrumbs()
        if crumbs:
            last = crumbs[-1]
            sp = last.get("stop_point") or ""
            print(f"last stop: {last.get('note', '')}"
                  + (f" — stopped at: {sp}" if sp else ""))
        else:
            print("last stop: no breadcrumbs yet")
        return 0
    except Exception as e:  # noqa: BLE001
        return _plain_error("status", e)


def cmd_plan(args: list[str]) -> int:
    """awino plan [dir] — the mission's task DAG, simply."""
    target = _resolve_dir(args)
    awino_dir = target / ".awino"
    if not awino_dir.is_dir():
        print(f"awino plan: {target} is not an awino project yet.\n"
              f"  next action: run `awino init` there first.",
              file=sys.stderr)
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from registry import Registry
        reg = Registry(awino_dir)
        if not reg.exists:
            print("awino plan: the task list isn't built yet.\n"
                  "  next action: start a mission (`awino chat`) — the "
                  "task list is compiled from the mission automatically.")
            return 1
        try:
            order = reg.topological_order()
            tasks = {t["id"]: t for t in reg.tasks()}
        except ValueError as e:
            print(f"awino plan: the task list has a problem: {e}\n"
                  f"  next action: fix the dependency cycle in "
                  f".awino/registry/tasks.json, then retry.")
            return 1
        nxt = reg.whats_next(limit=10)
        print("what's next (nothing blocks these):")
        if nxt:
            for t in nxt:
                print(f"  - {t['text'][:80]}")
        else:
            print("  nothing unblocked — remaining work is blocked or done")
        blocked = reg.unblock_report()
        if blocked:
            print("blocked, and by what:")
            for b in blocked:
                by = "; ".join(f"{x['text'][:50]} ({x['state']})"
                               for x in b["blocked_by"])
                print(f"  - {b['task']['text'][:70]}\n    blocked by: {by}")
        print(f"full order ({len(order)} tasks):")
        for i, tid in enumerate(order, 1):
            t = tasks.get(tid, {})
            print(f"  {i}. [{t.get('state', '?')}] {t.get('text', tid)[:70]}")
        return 0
    except Exception as e:  # noqa: BLE001
        return _plain_error("plan", e)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return
    cmd, rest = args[0], args[1:]
    if cmd == "chat":
        # chat.py reads its optional home dir from sys.argv[1]; strip "chat"
        # so `awino chat [home]` behaves exactly like `python chat.py [home]`.
        sys.argv = [sys.argv[0], *rest]
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from chat import main as chat_main
        try:
            chat_main()
        except KeyboardInterrupt:
            print("\nbye.")
        except Exception as e:  # noqa: BLE001
            raise SystemExit(_plain_error("chat", e))
        return
    if cmd == "init":
        raise SystemExit(cmd_init(rest))
    if cmd == "status":
        raise SystemExit(cmd_status(rest))
    if cmd == "plan":
        raise SystemExit(cmd_plan(rest))
    print(f"unknown command: {cmd}\n{USAGE}", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
