"""Canonical tool schemas for the v0.6 agent loop (design doc section 2.2).

One entry per TOOL_DEFS key, in the OpenAI function-calling subset
(type/object/properties/required/enum/description) — the intersection all
four providers accept. The loop passes these to backend.generate(tools=...);
provider_tools.py translates them per provider.

Harness tools (attempt_completion, task_add, task_update) are included:
they are offered to the model like any other tool but intercepted by the
loop instead of reaching the sandbox.
"""

import re

from tools import TOOL_DEFS

TOOL_SCHEMAS: dict[str, dict] = {
    "read_file": {
        "description": "Read a text file from the workspace. Returns the full content.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string",
                                    "description": "Workspace-relative file path."}},
            "required": ["path"],
        },
    },
    "write_file": {
        "description": ("Write a complete file (consequential: needs approval). "
                        "Prefer patch_file for edits to existing files."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string",
                            "description": "Full new file content."},
            },
            "required": ["path", "content"],
        },
    },
    "patch_file": {
        "description": ("Apply a strict unified diff to a file (consequential: "
                        "needs approval). Zero fuzz: every hunk must match "
                        "exactly at its header line number."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "diff": {"type": "string",
                         "description": "Unified diff text."},
            },
            "required": ["path", "diff"],
        },
    },
    "list_dir": {
        "description": "List directory entries in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string",
                                    "description": "Directory, default workspace root."}},
            "required": [],
        },
    },
    "run_command": {
        "description": ("Run a shell command with cwd confined to the workspace. "
                        "Keep commands portable (sh on POSIX, cmd.exe on Windows)."),
        "parameters": {
            "type": "object",
            "properties": {"cmd": {"type": "string",
                                   "description": "Shell command to run."}},
            "required": ["cmd"],
        },
    },
    "search_files": {
        "description": ("Search file contents for a regex pattern "
                        "(ripgrep when available, stdlib fallback). Read-only."),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string",
                            "description": "Regular expression to search for."},
                "path": {"type": "string",
                         "description": "Directory to search, default workspace root."},
                "file_glob": {"type": "string",
                              "description": "Glob filter, e.g. '*.py'."},
                "top_k": {"type": "integer",
                          "description": "Max matches to return (1-200)."},
            },
            "required": ["pattern"],
        },
    },
    "find_symbol": {
        "description": ("Find definitions of a function/class by name "
                        "(AST for Python, pattern scan otherwise). Read-only."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Symbol name to find."},
                "path": {"type": "string",
                         "description": "Directory to search, default workspace root."},
            },
            "required": ["name"],
        },
    },
    "git_status": {
        "description": "Show git working-tree status (porcelain). Read-only.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "git_diff": {
        "description": "Show the unified diff of working-tree changes vs HEAD. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string",
                                    "description": "Optional path to limit the diff."}},
            "required": [],
        },
    },
    "diagnostics": {
        "description": ("Compile-check Python files and report syntax errors. "
                        "Read-only. Extension-supplied language diagnostics arrive later; "
                        "the shape is forward-compatible."),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string",
                                    "description": "File or directory, default workspace root."}},
            "required": [],
        },
    },
    "attempt_completion": {
        "description": ("Propose that the mission is complete. The harness verifies "
                        "every done criterion in code; unverified criteria are returned "
                        "as a missing-evidence list and the loop continues. Only call "
                        "this when you have evidence for every criterion. Honored only "
                        "on the REVIEW phase."),
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string",
                                       "description": "What was accomplished and the evidence."}},
            "required": ["summary"],
        },
    },
    "task_add": {
        "description": ("Add a task to the harness-owned TODO list. Keep 3-7 tasks; "
                        "duplicate titles return the existing id."),
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    "task_update": {
        "description": ("Update a task's status (todo|doing|done) and notes. Mark "
                        "exactly one task 'doing' at a time; mark 'done' only with "
                        "evidence in hand."),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["todo", "doing", "done"]},
                "notes": {"type": "string"},
                "evidence": {"type": "string",
                             "description": ("Workspace file(s) proving the "
                                             "task is done, comma-separated. "
                                             "Required to mark a plan task done.")},
            },
            "required": ["id", "status"],
        },
    },
    # 0.5.3 hotfix port: interview-convergence tool. NOT a HARNESS_TOOL
    # (those are offered in every mode); the contract offers it in
    # observe/plan only.
    "set_mission": {
        "description": ("Record the mission and its done criteria — call this "
                        "when the discovery interview has converged (objective "
                        "and done criteria are crisp). A worker's mission is "
                        "fixed by its parent; workers are refused."),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "The mission objective, in your own words."},
                "criteria": {"type": "string",
                             "description": ("One string: the done criteria "
                                             "separated by semicolons or newlines.")},
            },
            "required": ["text", "criteria"],
        },
    },
    "stretch_goal": {
        "description": ("Pitch one stretch goal beyond what the user asked "
                        "(the Bugatti), in NABC form, broken into 3-5 steps. "
                        "It is parked with a revisit date and never built "
                        "unless the user picks it up. Use it when you see a "
                        "better or bigger idea worth their time."),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short name for the idea."},
                "need": {"type": "string", "description": "Need: the problem or opportunity it addresses."},
                "approach": {"type": "string", "description": "Approach: how it would work, briefly."},
                "benefits": {"type": "string", "description": "Benefits: what the user or project gains, concretely."},
                "competition": {"type": "string", "description": "Competition: alternatives and why this beats them."},
                "steps": {"type": "string", "description": "3-5 steps, one per line: title | success criterion | failure criterion."},
                "revisit_on": {"type": "string", "description": "Optional revisit date YYYY-MM-DD; default 30 days."},
            },
            "required": ["title", "need", "approach", "benefits",
                         "competition", "steps"],
        },
    },
    "story_plan": {
        "description": ("Record the story plan you agreed with the user. Honda "
                        "first: the committed, working scope is the "
                        "recommendation; the Bugatti is pitched in brief and "
                        "never built unasked. Writes the story spine, puts it "
                        "on the story ledger (STORY.md, brag board on close) "
                        "and seeds the task list. Call it after the user "
                        "agrees to the plan."),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Story title; needed when no story is in progress."},
                "story_id": {"type": "string", "description": "Existing story id to plan; default the story in progress."},
                "problem": {"type": "string", "description": "The problem in one or two sentences."},
                "done_criteria": {"type": "string", "description": "Done criteria, separated by semicolons."},
                "breakdown": {"type": "string", "description": "First-principles breakdown of the problem."},
                "surveyed": {"type": "string", "description": "Existing approaches surveyed and why they fall short."},
                "user_guidance": {"type": "string", "description": "What the user asked for and decided, in their words."},
                "proposal": {"type": "string", "description": "Options A/B/C with the Honda recommended; tag claims [Certain], [Likely] or [Guessing]."},
                "steps": {"type": "string", "description": "Ordered steps, one per line: title | success criterion | failure criterion | forecast (time, e.g. 45m or 2h). The forecast is checked against the actual time on the story receipt."},
                "bugatti_brief": {"type": "string", "description": "The Bugatti (ambitious option) in two or three sentences."},
            },
            "required": ["breakdown", "surveyed", "user_guidance",
                         "proposal", "steps", "bugatti_brief"],
        },
    },
}


def tool_catalog(names: list[str] | None = None) -> str:
    """One-line-per-tool catalog for JSON-turn prompts, generated from
    TOOL_SCHEMAS so the prompt can never drift from the real parameters.
    Optional parameters carry a trailing '?'; consequential tools say so."""
    lines = []
    for n in names if names is not None else TOOL_SCHEMAS:
        s = TOOL_SCHEMAS.get(n)
        if s is None:
            continue
        props = s["parameters"].get("properties", {})
        req = set(s["parameters"].get("required", []))
        params = ", ".join(f'"{p}"' + ("" if p in req else "?")
                           for p in props)
        desc = re.sub(r"\s*\([^)]*\)", "", s["description"].split(". ")[0]).rstrip(".")
        gate = (" [consequential: needs approval]"
                if TOOL_DEFS.get(n, {}).get("consequential") else "")
        lines.append(f"  - {n} {{{params}}}: {desc}{gate}")
    return "\n".join(lines)


def schemas_for(names: list[str]) -> list[dict]:
    """Provider-agnostic schema dicts for the named tools, in order.

    Each entry is {"name":..., "description":..., "parameters":...}.
    Unknown names are skipped (the loop's permission gate already rejected
    unoffered tools; this is belt-and-braces for hand-built callers).
    """
    out = []
    for n in names:
        s = TOOL_SCHEMAS.get(n)
        if s is None:
            continue
        out.append({"name": n, "description": s["description"],
                    "parameters": s["parameters"]})
    return out


def check_schema_coverage() -> list[str]:
    """Tools in TOOL_DEFS with no schema entry (dev-time invariant check)."""
    return [n for n in TOOL_DEFS if n not in TOOL_SCHEMAS]
