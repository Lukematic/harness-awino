#!/usr/bin/env python3
"""Thin wrapper: runs the canonical A.W.I.N.O. gate hook.

The real implementation lives at <repo>/prototype/awino_gate.py (stdlib-only,
self-contained). This wrapper resolves it relative to its own location, so
you can point Claude Code's PreToolUse hook command at this file.

If you prefer a single copyable file, copy prototype/awino_gate.py itself
instead — it has no dependencies and works standalone.
"""
import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_IMPL = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "prototype", "awino_gate.py")
)

if not os.path.isfile(_IMPL):
    print(
        "A.W.I.N.O. gate: implementation not found at " + _IMPL,
        file=sys.stderr,
    )
    sys.exit(2)

sys.argv = [_IMPL]
runpy.run_path(_IMPL, run_name="__main__")
