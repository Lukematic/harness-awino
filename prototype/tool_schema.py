"""Canonical tool schemas for the v0.6 agent loop (design doc section 2.2).

One entry per TOOL_DEFS key, in the OpenAI function-calling subset
(type/object/properties/required/enum/description) — the intersection all
four providers accept. The loop passes these to backend.generate(tools=...);
provider_tools.py translates them per provider.

Harness tools (attempt_completion, task_add, task_update) are included:
they are offered to the model like any other tool but intercepted by the
loop instead of reaching the sandbox.
"""

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
            },
            "required": ["id", "status"],
        },
    },
}


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
