"""awino console entry point.

Dispatches subcommands; `awino chat` starts the loop-owner REPL.
Installed as the `awino` console script (see pyproject.toml).
"""
from __future__ import annotations

import sys

USAGE = "usage: awino chat [home]"


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return
    if args[0] != "chat":
        print(f"unknown command: {args[0]}\n{USAGE}", file=sys.stderr)
        raise SystemExit(2)
    # chat.py reads its optional home dir from sys.argv[1]; strip "chat"
    # so `awino chat [home]` behaves exactly like `python chat.py [home]`.
    sys.argv = [sys.argv[0], *args[1:]]
    from chat import main as chat_main
    chat_main()
