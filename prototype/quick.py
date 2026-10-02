"""Quick path: the plain agent loop, like Cline/Kilo.

For direct asks ("add X", "fix this test", "explain this"): the model gets
read, write and command tools straight away in a native tool-calling
conversation. No reply format, no phases, no mission ceremony.

  1. model replies with text and/or tool calls
  2. read-only calls run in parallel; each edit shows a diff card for
     approval; commands run unless destructive (then they ask)
  3. results go back to the model; repeat until it answers without tools
  4. when files changed, the project's `test` recipe runs; a failure goes
     back to the model to fix (up to MAX_TEST_FIXES times)

Everything is journaled through the Loop's executor (tool_called /
tool_result, idempotency, delegated edits), so receipts and history still
see it. Bigger work ("plan this") uses the mission flow instead.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

READ_TOOLS = ("read_file", "list_dir", "search_files", "find_symbol",
              "git_status", "git_diff", "diagnostics")
WRITE_TOOLS = ("write_file", "patch_file")
QUICK_TOOLS = READ_TOOLS + WRITE_TOOLS + ("run_command",)
MAX_STEPS = 40
MAX_TEST_FIXES = 2
RESULT_CHARS = 12000
PARALLEL = 6
# A reply that shows code or promises an edit, with no edit made: weaker
# models narrate changes instead of calling the tools. One nudge, like
# Cline's "you did not use a tool".
_CLAIMS_EDIT = re.compile(
    r"```|\bI (?:will|'ll|have) (?:now )?(?:create|write|add|update|modif|chang|fix)"
    r"|review the diffs?\b", re.I)
NUDGE = ("You described file changes but did not make them: nothing was "
         "written. Make the edits now with write_file or patch_file. If no "
         "change is needed, say so plainly without code blocks.")

SYSTEM = """You are Awino, a coding agent working inside the user's project in VS Code.
Workspace: {root}

Work like a careful senior engineer:
- Look before you change: read the relevant files (several reads at once is fine).
- Make the smallest change that fully solves the request. Prefer patch_file for edits to existing files; write_file for new files.
- Every file edit is shown to the user as a diff to approve. Destructive commands (deleting, force, git push/reset, installs, network) also ask; other commands run directly.
- After changing code, the project's tests run automatically. If they fail you get the output: fix the cause.
- When you are done, reply in a few plain sentences: what you changed and why. No tool call in that final reply.
- If the request is a question, just answer it (read files if you need to). Ask a short question only when you genuinely cannot proceed.
{rules}{lessons}"""


def project_rules(root: Path, limit: int = 6000) -> str:
    """AGENTS.md / CLAUDE.md / .clinerules / .cursorrules, if present."""
    parts = []
    for name in ("AGENTS.md", "CLAUDE.md", ".clinerules", ".cursorrules"):
        p = root / name
        if p.is_file():
            try:
                parts.append(f"## {name}\n{p.read_text(errors='replace')[:limit]}")
            except OSError:
                pass
    if not parts:
        return ""
    return "\nProject rules (from the repository — follow them):\n" + \
        "\n".join(parts)[:limit] + "\n"


def _clip(text: str, n: int = RESULT_CHARS) -> str:
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    return text if len(text) <= n else text[:n] + f"\n[... {len(text) - n} more chars]"


class QuickSession:
    """One conversation. `run(user_text)` drives one request to its end.

    approve(item) -> decision is supplied by the sidecar: it emits an
    approval card and blocks until the user decides ("approve" | "always"
    | "deny"). emit(event) streams progress to the chat.
    """

    def __init__(self, loop, backend, emit, approve, cancel=None):
        self.loop = loop
        self.backend = backend
        self.emit = emit
        self.approve = approve
        self.cancel = cancel
        self.messages: list[dict] = []
        self.allow: set[str] = set()  # "always allow" for this session
        self._n = 0

    # ------------------------------------------------------------- helpers
    def _system(self) -> str:
        root = Path(self.loop.sandbox.root)
        lessons = ""
        try:
            lines = self.loop.state.snapshot.get("lessons") or []
            if lines:
                lessons = ("\nLessons from this project's past work:\n"
                           + "\n".join(f"- {ln}" for ln in lines[:6]) + "\n")
        except Exception:  # noqa: BLE001
            pass
        return SYSTEM.format(root=root, rules=project_rules(root),
                             lessons=lessons)

    def _tools(self) -> list[dict]:
        from tool_schema import schemas_for
        return schemas_for(list(QUICK_TOOLS))

    def _call_id(self, turn_id: str) -> str:
        self._n += 1
        return f"{turn_id}.{self._n}"

    def _exec(self, turn_id: str, call: dict) -> str:
        call_id = self._call_id(turn_id)
        env = self.loop._execute_single(
            call_id, call["name"], call.get("args") or {},
            self.loop._idem({"name": call["name"], "args": call.get("args") or {}}))
        return _clip(self.loop._result_detail(env))

    def _needs_approval(self, call: dict) -> str | None:
        """Why this call must ask first, or None to run it now."""
        name = call["name"]
        if name in READ_TOOLS:
            return None
        if name in self.allow:
            return None
        if name in WRITE_TOOLS:
            return "edit"
        if name == "run_command":
            from approval_targets import destructive_reason
            return destructive_reason(str((call.get("args") or {}).get("cmd") or ""),
                                      str(self.loop.sandbox.root))
        return "unknown tool"

    def _charge(self, turn_id: str) -> None:
        """Journal this call's tokens (real counts when the provider sends
        them) and update the chat's token meter."""
        usage = getattr(self.backend, "last_usage", None)
        if isinstance(usage, dict):
            self.backend.last_usage = None
            data = {"tokens": usage["input"] + usage["output"],
                    "input": usage["input"], "output": usage["output"],
                    "cached": usage.get("cached", 0), "measured": True}
        else:
            data = {"tokens": sum(len(str(m.get("text") or m.get("content") or ""))
                                  for m in self.messages[-4:]) // 4,
                    "measured": False}
        data["turn_id"] = turn_id
        self.loop.state.record("tokens_charged", data)
        self.emit({"event": "token_meter", "turn_id": turn_id,
                   **self.loop.token_meter(turn_id)})

    def _changed_files(self, turn_id: str) -> bool:
        for e in reversed(self.loop.state.events):
            d = e.get("data") or {}
            if (e["type"] == "tool_result"
                    and str(d.get("call_id", "")).startswith(turn_id + ".")
                    and d.get("tool") in WRITE_TOOLS
                    and not (isinstance(d.get("result"), dict)
                             and d["result"].get("error"))):
                return True
        return False

    def _run_tests(self, turn_id: str) -> tuple[bool, str] | None:
        from verify import find_recipe, run_recipe
        found = find_recipe(self.loop.sandbox.root, "test")
        if not found:
            return None
        runner, recipe = found
        self.emit({"event": "tool_progress", "turn_id": turn_id,
                   "tool": "run_command", "phase": "start",
                   "summary": f"{runner} {recipe} (project tests)"})
        r = run_recipe(self.loop.sandbox.root, runner, recipe)
        self.loop.state.record("quick_tests", {
            "turn_id": turn_id, "cmd": f"{runner} {recipe}",
            "exit_code": r["exit_code"], "output": r["output"][-2000:]})
        self.emit({"event": "tool_progress", "turn_id": turn_id,
                   "tool": "run_command", "phase": "end",
                   "summary": f"{runner} {recipe} → exit {r['exit_code']}"})
        return r["exit_code"] == 0, f"{runner} {recipe}: exit {r['exit_code']}\n{r['output'][-3000:]}"

    # ---------------------------------------------------------------- loop
    def run(self, user_text: str, turn_id: str) -> dict:
        from cancel import Cancelled
        self.loop.state.record("quick_user", {"turn_id": turn_id,
                                              "text": user_text[:4000]})
        self.messages.append({"role": "user", "text": user_text})
        tools = self._tools()
        fixes = 0
        nudged = False
        tests_note = ""
        final = ""
        steps = 0
        t0 = time.time()
        while steps < MAX_STEPS:
            steps += 1
            if self.cancel is not None and self.cancel.is_set():
                return self._end(turn_id, "Stopped.", "cancelled", steps, t0)
            try:
                reply = self.backend.chat(self._system(), self.messages,
                                          tools=tools, cancel=self.cancel)
            except Cancelled:
                return self._end(turn_id, "Stopped.", "cancelled", steps, t0)
            except Exception as ex:  # noqa: BLE001 - shown to the user
                from backends import explain_backend_failure
                cause, fix = explain_backend_failure(f"{type(ex).__name__}: {ex}")
                return self._end(turn_id, f"The model call failed: {cause} {fix}",
                                 "error", steps, t0)
            self._charge(turn_id)
            text = (reply.get("text") or "").strip()
            calls = [c for c in reply.get("tool_calls") or []
                     if c.get("name")]
            self.messages.append({"role": "assistant", "text": text,
                                  "tool_calls": calls})
            if text:
                self.emit({"event": "said_delta", "turn_id": turn_id,
                           "text": text + ("\n\n" if calls else "")})
            if not calls:
                final = text
                if (not nudged and _CLAIMS_EDIT.search(text)
                        and not self._changed_files(turn_id)):
                    nudged = True
                    self.loop.state.record("quick_nudged", {"turn_id": turn_id})
                    self.messages.append({"role": "user", "text": NUDGE})
                    continue
                if self._changed_files(turn_id) and not tests_note:
                    res = self._run_tests(turn_id)
                    if res is None:
                        tests_note = ("No test recipe found (add one with "
                                      "Awino: Set Up Project).")
                    elif res[0]:
                        tests_note = "Tests passed: " + res[1].splitlines()[0]
                    elif fixes < MAX_TEST_FIXES:
                        fixes += 1
                        self.messages.append({"role": "user", "text": (
                            "The project's tests failed after your change. "
                            "Fix the cause, then reply with a short summary.\n\n"
                            + res[1])})
                        continue
                    else:
                        tests_note = ("Tests still fail after "
                                      f"{MAX_TEST_FIXES} fixes: "
                                      + res[1].splitlines()[0])
                break
            results = self._run_calls(turn_id, calls)
            for call, content in zip(calls, results):
                self.messages.append({"role": "tool", "id": call["id"],
                                      "name": call["name"], "content": content})
        else:
            final = (final or "") + f"\n\n(Stopped after {MAX_STEPS} steps.)"
        said = final or "Done."
        if tests_note:
            said += "\n\n" + tests_note
        return self._end(turn_id, said, "ok", steps, t0)

    def _run_calls(self, turn_id: str, calls: list[dict]) -> list[str]:
        """Read-only calls in parallel; edits and risky commands one at a
        time behind an approval card. Results keep call order."""
        results: dict[int, str] = {}
        free = [(i, c) for i, c in enumerate(calls)
                if not self._needs_approval(c)
                and c["name"] in READ_TOOLS]
        if free:
            with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
                futs = {i: pool.submit(self._exec, turn_id, c) for i, c in free}
                for i, f in futs.items():
                    try:
                        results[i] = f.result()
                    except Exception as ex:  # noqa: BLE001
                        results[i] = f"error: {type(ex).__name__}: {ex}"
        for i, c in enumerate(calls):
            if i in results:
                continue
            if c["name"] not in QUICK_TOOLS:
                results[i] = f"error: unknown tool {c['name']}"
                continue
            why = self._needs_approval(c)
            if why:
                decision = self.approve({"tool": c["name"],
                                         "args": c.get("args") or {},
                                         "reason": why, "turn_id": turn_id})
                if decision == "always":
                    self.allow.add(c["name"])
                if decision not in ("approve", "always"):
                    self.loop.state.record("quick_denied", {
                        "turn_id": turn_id, "tool": c["name"],
                        "args": c.get("args") or {}})
                    results[i] = ("The user denied this action. Do not retry "
                                  "it; ask or take another approach.")
                    continue
            results[i] = self._exec(turn_id, c)
        return [results[i] for i in range(len(calls))]

    def _end(self, turn_id, said, status, steps, t0) -> dict:
        self.loop.state.record("quick_done", {
            "turn_id": turn_id, "status": status, "steps": steps,
            "seconds": round(time.time() - t0, 1), "said": said[:2000]})
        self.loop.state.persist_snapshot()
        return {"status": status, "said": said, "turn_id": turn_id,
                "quick": True, "steps": steps,
                "phase": self.loop.state.snapshot.get("phase")}
