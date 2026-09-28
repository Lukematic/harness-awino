# hooks

`awino_gate.py` is a Claude Code PreToolUse hook. It forwards to
`prototype/awino_gate.py`, which checks each tool call against the project's
Awino state. The hook does nothing until a project opts in by having an
`.awino/` folder.

Wire it in with the snippet in `../settings.json`. Tests:
`prototype/tests/test_awino_gate.py`. See [../README.md](../README.md).
