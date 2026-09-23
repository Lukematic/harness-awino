# A.W.I.N.O. × Claude Code

Run missions the A.W.I.N.O. way inside Claude Code: per-turn contracts,
turn validation, a judge panel, approval gates, and evidence-based
completion — with a real code gate on tool execution.

## What you get

| Piece | File | What it does |
|---|---|---|
| Agent Skill | `skills/awino-loop-owner/SKILL.md` | Loop-owner discipline, auto-discovered by Claude Code |
| Subagent | `agents/awino-loop-owner.md` | Delegatable `awino-loop-owner` with the MCP tools wired in |
| Slash command | `commands/awino.md` | `/awino [objective]` boots the loop-owner in-session |
| Gate hook | `hooks/awino_gate.py` → `prototype/awino_gate.py` | **Code-level** PreToolUse gate on tool calls |
| MCP server | `prototype/awino_mcp.py` (shared with the Kilo track) | The harness's real contract/validate/judge/synthesis code |
| Settings snippet | `settings.json` | Wires the hook into `.claude/settings.json` |
| MCP config | `.mcp.json` | Example MCP server config |

## Install

1. **Install the package** (stdlib only, zero dependencies):
   `pip install /path/to/harness-awino/prototype` — or just work from the repo.
2. **Skill** (pick one scope): copy `skills/awino-loop-owner/` to
   `~/.claude/skills/` (global) or `<project>/.claude/skills/` (project).
3. **Subagent**: copy `agents/awino-loop-owner.md` to
   `~/.claude/agents/` or `<project>/.claude/agents/`.
4. **Slash command**: copy `commands/awino.md` to
   `~/.claude/commands/` or `<project>/.claude/commands/`.
5. **MCP server** (needs `prototype/awino_mcp.py` from this repo):
   `claude mcp add awino -- python3 /ABS/PATH/harness-awino/prototype/awino_mcp.py`
   or copy `.mcp.json` into your project and replace the path. Restart
   Claude Code afterwards.
6. **Gate hook**: merge `settings.json`'s `hooks` block into your
   `.claude/settings.json`, replacing the hook command path. The wrapper
   locates `prototype/awino_gate.py` itself; alternatively point the command
   straight at `prototype/awino_gate.py` (it is a single stdlib-only file).
7. **Opt a project in**: `mkdir <project>/.awino` and write
   `.awino/contract.json`:
   ```json
   {
     "mission_id": "m-1",
     "stage": "BUILD",
     "tools_offered": ["Read", "Bash", "Glob", "Grep"],
     "turn": 1,
     "expires_turn": 5
   }
   ```
   `tools_offered` uses Claude Code tool names. Without `.awino/`, the hook
   is completely inert and Claude Code behaves normally.

Then: `/awino Fix the login bug` — the loop-owner boots, interviews you,
compiles the contract every turn, validates and judges each turn, and the
hook blocks any tool the contract does not offer.

## The enforcement story

Three layers, honest about what each buys:

1. **Prompt discipline** (skill / subagent / `/awino`): the agent follows
   the loop-owner rules because its instructions say so. Strong, but
   voluntary.
2. **Code-backed self-checks** (MCP tools): contract compilation,
   validation, and judging run in the harness's real code, not in the
   model's head. The model can still choose not to call them.
3. **Code gate on execution** (PreToolUse hook): when `.awino/` is present,
   a tool call the contract does not offer is blocked *before it executes* —
   exit 2, with the reason. An expired or malformed contract blocks
   everything until recompiled. This is real enforcement at the boundary
   where damage happens, and it is fail-closed: any hook error blocks rather
   than crashing open.

## Honest caveats

- The hook gates **tool execution**, not text generation. The model can still
  *say* anything; it cannot *do* anything outside the contract.
- The hook only fires for projects with `.awino/` — opt-in by design, so it
  can never brick normal Claude Code usage.
- The model's turn loop still belongs to Claude Code. The full no-bypass
  guarantee — turns that *cannot* skip the harness — exists only in
  `awino chat`, where the loop itself is code.
- `awino_mcp.py` keeps its missions in memory: restarting the MCP server
  loses mission state. For long missions, prefer `awino chat`.
