"""Headless end-to-end scenarios for the A.W.I.N.O. OpenCode bundle.

Runs the real `opencode` binary (`opencode run`) with OPENCODE_CONFIG_DIR
pointed at this bundle, against prototype/tests/fake_gateway.py playing a
scripted OpenAI-compatible model. Every assertion is on files the run left
behind, the CLI output, or the gateway's request log, never on what the
model says about itself.

  python opencode/tests/e2e.py --out e2e-out            # fake-gateway scenarios
  python opencode/tests/e2e.py --out e2e-out --real     # + real model (env)

Writes <out>/results.json and <out>/summary.md; exits 1 if any scenario fails.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "opencode"
sys.path.insert(0, str(REPO / "prototype"))
from tests import fake_gateway  # noqa: E402

OPENCODE = os.environ.get("OPENCODE_BIN", "opencode")
RUN_TIMEOUT = int(os.environ.get("E2E_RUN_TIMEOUT", "180"))


def directive(**d) -> str:
    return "AWINO_TEST_B64 " + base64.b64encode(json.dumps(d).encode()).decode()


class Ctx:
    def __init__(self, out: Path, name: str, port: int, log: Path):
        self.name, self.port, self.log = name, port, log
        self.dir = Path(tempfile.mkdtemp(prefix=f"awino-{name}-"))
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=False)
        self.out = out / name
        self.out.mkdir(parents=True, exist_ok=True)
        self.transcript: list[str] = []
        self.checks: list[tuple[str, bool, str]] = []
        self.log_start = self._log_len()

    def _log_len(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def requests(self) -> list[dict]:
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [json.loads(x) for x in lines[self.log_start:]]

    def env(self, key="test-key", model="fake-model", base=None) -> dict:
        e = dict(os.environ)
        e.update({
            "OPENCODE_CONFIG_DIR": str(BUNDLE),
            "AWINO_BASE_URL": base or f"http://127.0.0.1:{self.port}/v1",
            "AWINO_API_KEY": key,
            "AWINO_MODEL": model,
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        })
        return e

    def run(self, message: str, *args, env=None) -> tuple[int, str]:
        cmd = [OPENCODE, "run", "--title", self.name, *args, message]
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=self.dir, env=env or self.env(),
                               capture_output=True, text=True,
                               timeout=RUN_TIMEOUT)
            code, out = p.returncode, p.stdout + "\n--- stderr ---\n" + p.stderr
        except subprocess.TimeoutExpired as ex:
            code = 124
            out = f"TIMEOUT after {RUN_TIMEOUT}s\n{ex.stdout or ''}\n{ex.stderr or ''}"
        self.transcript.append(f"$ {' '.join(cmd[:-1])} <message>\n# message:\n{message}\n"
                               f"# exit {code} in {time.time() - t0:.1f}s\n{out}\n")
        return code, out

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((label, bool(ok), detail))
        return bool(ok)

    def finish(self) -> dict:
        (self.out / "transcript.txt").write_text("\n".join(self.transcript))
        (self.out / "requests.jsonl").write_text(
            "\n".join(json.dumps(r) for r in self.requests()))
        files = self.out / "project"
        shutil.copytree(self.dir, files, ignore=shutil.ignore_patterns(".git"),
                        dirs_exist_ok=True)
        return {"name": self.name, "passed": all(ok for _, ok, _ in self.checks)
                and bool(self.checks),
                "checks": [{"check": c, "ok": ok, "detail": d}
                           for c, ok, d in self.checks]}


def read(p: Path) -> str:
    return p.read_text() if p.exists() else ""


def write_mission(d: Path):
    (d / ".awino").mkdir(exist_ok=True)
    (d / ".awino" / "mission.json").write_text(json.dumps(
        {"objective": "fixture mission", "done_criteria": ["hi.txt exists"]}))


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

def s1_bearer(c: Ctx):
    code, out = c.run("say hi\n" + directive(reply="HELLO_FROM_FAKE"))
    reqs = [r for r in c.requests() if r["method"] == "POST"]
    c.check("run exits 0", code == 0, f"exit {code}")
    c.check("model was called", len(reqs) >= 1, f"{len(reqs)} POSTs")
    c.check("every request carried Bearer test-key",
            reqs and all(r["auth"] for r in reqs),
            json.dumps([r["auth"] for r in reqs]))
    c.check("requests streamed", reqs and all(r["stream"] for r in reqs), "")
    c.check("reply reached the CLI", "HELLO_FROM_FAKE" in out, "")
    before = len(c.requests())
    code, out = c.run("say hi\n" + directive(reply="SHOULD_NOT_APPEAR"),
                      env=c.env(key="wrong-key"))
    bad = [r for r in c.requests()[before:] if r["method"] == "POST"]
    c.check("wrong key: gateway saw unauthenticated requests",
            bad and not any(r["auth"] for r in bad),
            json.dumps([r["auth"] for r in bad]))
    c.check("wrong key: no reply reached the CLI",
            "SHOULD_NOT_APPEAR" not in out, "")


def s2_blocked_before_mission(c: Ctx):
    code, out = c.run("make the file\n" + directive(
        calls=[{"name": "write", "args": {"filePath": str(c.dir / "hi.txt"),
                                          "content": "hello"}}],
        reply="AFTER_WRITE"))
    c.check("write tool was offered to the model",
            any("write" in r["tool_names"] for r in c.requests()), "")
    c.check("hi.txt NOT written", not (c.dir / "hi.txt").exists(), "")
    c.check("block reason returned to the model",
            "no mission" in out.lower() and "set_mission" in out, "")
    # A mission cannot be forged by writing the file directly.
    code, out = c.run("forge it\n" + directive(
        calls=[{"name": "write", "args": {
            "filePath": str(c.dir / ".awino" / "mission.json"),
            "content": '{"objective": "forged"}'}}], reply="AFTER_FORGE"))
    c.check(".awino/mission.json NOT forged via write",
            not (c.dir / ".awino" / "mission.json").exists(), "")


def s3_allowed_after_mission(c: Ctx):
    code, out = c.run("define then build\n" + directive(calls=[
        {"name": "set_mission", "args": {
            "objective": "Create hi.txt saying hello",
            "done_criteria": ["hi.txt exists", "hi.txt contains hello"]}},
        {"name": "write", "args": {"filePath": str(c.dir / "hi.txt"),
                                   "content": "hello"}}],
        reply="BUILT"))
    m = c.dir / ".awino" / "mission.json"
    c.check("set_mission offered as a tool",
            any("set_mission" in r["tool_names"] for r in c.requests()), "")
    c.check(".awino/mission.json written by set_mission", m.exists(), read(m))
    ok = False
    if m.exists():
        j = json.loads(m.read_text())
        ok = (j.get("objective") == "Create hi.txt saying hello"
              and len(j.get("done_criteria", [])) == 2)
    c.check("mission holds objective + done criteria", ok, "")
    c.check("hi.txt written after mission",
            read(c.dir / "hi.txt") == "hello", read(c.dir / "hi.txt"))
    c.check("run exits 0", code == 0, f"exit {code}")


def s4_story_close(c: Ctx):
    write_mission(c.dir)
    code, out = c.run("close it\n" + directive(calls=[
        {"name": "story_close", "args": {
            "title": "Say hello", "outcome": "hi.txt now greets the user",
            "evidence": ["cat hi.txt -> hello"]}}], reply="CLOSED"))
    receipts = sorted((c.dir / ".awino" / "receipts").glob("*.md"))
    brag = read(c.dir / "BRAG.md")
    c.check("story_close offered as a tool",
            any("story_close" in r["tool_names"] for r in c.requests()), "")
    c.check("receipt .md written", len(receipts) == 1,
            ", ".join(p.name for p in receipts))
    rc = read(receipts[0]) if receipts else ""
    c.check("receipt has promise + proof",
            "fixture mission" in rc and "cat hi.txt -> hello" in rc, rc[:400])
    c.check("brag board has the entry",
            "Say hello" in brag and "hi.txt now greets the user" in brag, brag)
    before = brag
    code, out = c.run("tamper\n" + directive(calls=[
        {"name": "write", "args": {"filePath": str(c.dir / "BRAG.md"),
                                   "content": "fake brag"}}], reply="T1"))
    c.check("direct write to BRAG.md denied", read(c.dir / "BRAG.md") == before, "")
    if receipts:
        rbefore = read(receipts[0])
        code, out = c.run("tamper2\n" + directive(calls=[
            {"name": "edit", "args": {"filePath": str(receipts[0]),
                                      "oldString": "Say hello",
                                      "newString": "Forged"}}], reply="T2"))
        c.check("direct edit of the receipt denied",
                read(receipts[0]) == rbefore, "")
    code, out = c.run("close again\n" + directive(calls=[
        {"name": "story_close", "args": {"title": "Second", "outcome": "more"}}],
        reply="CLOSED2"))
    brag2 = read(c.dir / "BRAG.md")
    c.check("second close appends (both entries present)",
            "Say hello" in brag2 and "Second" in brag2, "")


def s_interviewer(c: Ctx):
    write_mission(c.dir)
    code, out = c.run("interview\n" + directive(calls=[
        {"name": "write", "args": {"filePath": str(c.dir / "hi.txt"),
                                   "content": "x"}}], reply="IV"),
        "--agent", "interviewer")
    reqs = [r for r in c.requests() if r["method"] == "POST"]
    offered = set(n for r in reqs for n in r["tool_names"])
    c.check("interviewer model calls happened", len(reqs) >= 1, "")
    c.check("write/edit/patch not offered to interviewer",
            not ({"write", "edit", "patch", "apply_patch"} & offered),
            ", ".join(sorted(offered)))
    c.check("hi.txt NOT written by interviewer",
            not (c.dir / "hi.txt").exists(), "")


def u_a_stance_injection(c: Ctx):
    """Unknown (a): routed stance in the prompt on every model call."""
    (c.dir / "notes.txt").write_text("notes\n")
    turns = [
        ("should we use postgres vs sqlite?", "steel-man", []),
        ("teach me how does indexing work", "feynman", []),
        ("fix the bug in notes", "first-principles",
         [{"name": "read", "args": {"filePath": str(c.dir / "notes.txt")}}]),
    ]
    per_turn = []
    for i, (text, want, calls) in enumerate(turns):
        before = len(c.requests())
        args = ["--continue"] if i else []
        c.run(text + "\n" + directive(calls=calls, reply=f"T{i}"), *args)
        reqs = [r for r in c.requests()[before:] if r["method"] == "POST"]
        per_turn.append((text, want, reqs))
    for text, want, reqs in per_turn:
        stances = [r["stance"] for r in reqs]
        c.check(f"'{text}': every call ({len(reqs)}) carries stance {want}",
                reqs and all(any(want in s for s in st) for st in stances),
                json.dumps(stances))
    c.check("tool round-trip turn made >1 model call",
            len(per_turn[2][2]) >= 2, f"{len(per_turn[2][2])} calls")


def u_b_redo(c: Ctx):
    """Unknown (b): a reply that breaks a rule gets caught and redone."""
    code, out = c.run("let's build a todo app\n" + directive(
        reply="What is the deadline? Who are the users? What platform?",
        on_correction="Who are the users?"))
    reqs = c.requests()
    corr = [r for r in reqs if "[A.W.I.N.O. correction]" in r["last_user"]]
    c.check("grill stance routed", any(any("planning-grill" in s for s in r["stance"])
                                       for r in reqs), "")
    c.check("plugin sent a correction follow-up to the model", len(corr) >= 1,
            f"{len(corr)} correction requests")
    exp = subprocess.run([OPENCODE, "export"], cwd=c.dir, env=c.env(),
                         capture_output=True, text=True, timeout=60)
    (c.out / "export.json").write_text(exp.stdout)
    texts = []
    try:
        data = json.loads(exp.stdout[exp.stdout.index("{"):])
        for m in data.get("messages", []):
            if m.get("info", {}).get("role") == "assistant":
                texts.append("".join(p.get("text", "") for p in m.get("parts", [])
                                     if p.get("type") == "text"))
    except Exception as ex:  # noqa: BLE001
        texts = [f"export parse failed: {ex}"]
    c.check("final assistant reply is the redone one (one question)",
            texts and texts[-1].strip() == "Who are the users?", json.dumps(texts))
    c.check("no redo loop (<=2 corrections)", len(corr) <= 2, "")


def s5_real_model(c: Ctx):
    key = os.environ.get("REAL_API_KEY", "")
    base = os.environ.get("REAL_BASE_URL", "")
    model = os.environ.get("REAL_MODEL", "")
    env = c.env(key=key, model=model, base=base)
    code, out = c.run(
        "Create a file named hello.txt containing exactly the word hello. "
        "If a tool is blocked, read the reason and do what it says, then retry.",
        env=env)
    m = c.dir / ".awino" / "mission.json"
    c.check("run exits 0", code == 0, f"exit {code}")
    c.check("model set a mission before writing", m.exists(), read(m)[:300])
    c.check("hello.txt written", (c.dir / "hello.txt").exists(),
            read(c.dir / "hello.txt")[:100])


SCENARIOS = [
    ("1-bearer-key", s1_bearer),
    ("2-write-blocked-before-mission", s2_blocked_before_mission),
    ("3-write-allowed-after-set_mission", s3_allowed_after_mission),
    ("4-story_close-receipt-and-brag", s4_story_close),
    ("interviewer-cannot-edit", s_interviewer),
    ("unknown-a-stance-every-message", u_a_stance_injection),
    ("unknown-b-redo-on-rule-break", u_b_redo),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="e2e-out")
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log = out / "gateway.jsonl"
    log.unlink(missing_ok=True)
    os.environ["FAKE_GATEWAY_LOG"] = str(log)
    srv = fake_gateway.start()
    port = srv.server_port
    todo = [s for s in SCENARIOS if a.only in s[0]]
    results = []
    if a.real:
        if os.environ.get("REAL_API_KEY") and os.environ.get("REAL_MODEL"):
            todo.append(("5-real-model", s5_real_model))
        else:
            results.append({"name": "5-real-model", "passed": None,
                            "checks": [], "skipped":
                            "REAL_API_KEY / REAL_MODEL not set (no repo secret)"})
    for name, fn in todo:
        c = Ctx(out, name, port, log)
        try:
            fn(c)
        except Exception:  # noqa: BLE001
            c.check("scenario raised", False, traceback.format_exc())
        r = c.finish()
        results.append(r)
        print(("PASS " if r["passed"] else "FAIL ") + name, flush=True)
        for ch in r["checks"]:
            print(f"   [{'x' if ch['ok'] else ' '}] {ch['check']}"
                  + ("" if ch["ok"] else f"  -- {ch['detail'][:300]}"), flush=True)
    srv.shutdown()
    ver = subprocess.run([OPENCODE, "--version"], capture_output=True, text=True)
    (out / "results.json").write_text(json.dumps(
        {"opencode": ver.stdout.strip(), "results": results}, indent=2))
    lines = [f"# A.W.I.N.O. OpenCode e2e", "",
             f"opencode {ver.stdout.strip()}", "",
             "| scenario | result |", "|---|---|"]
    for r in results:
        res = "SKIPPED: " + r["skipped"] if r.get("skipped") else (
            "PASS" if r["passed"] else "FAIL")
        lines.append(f"| {r['name']} | {res} |")
    for r in results:
        lines += ["", f"## {r['name']}", ""]
        for ch in r["checks"]:
            lines.append(f"- [{'x' if ch['ok'] else ' '}] {ch['check']}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    failed = [r for r in results if r["passed"] is False]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
