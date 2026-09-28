"use strict";
// test/toolSummary.js — tool results and command results read as one plain
// line instead of JSON.
const assert = require("assert");
const { toolResultSummary: s, commandResultText: c, statusChipText: chip, approvalResolvedText: res, tokenMeterText: meter } = require("../webview/chat.js");
let pass = 0, fail = 0;
function t(name, fn) {
  try { fn(); pass++; console.log("ok   - " + name); }
  catch (e) { fail++; console.log("FAIL - " + name + "\n  " + e.message); }
}
t("set_mission uses its said line", () => assert.deepStrictEqual(
  s({ tool: "set_mission", result: { ok: true, mission: { id: "m" }, said: "Mission set: review 7 approaches" } }),
  { ok: true, text: "Mission set: review 7 approaches" }));
t("run_command pass", () => assert.deepStrictEqual(
  s({ tool: "run_command", args: { cmd: "pytest" }, result: { exit_code: 0, stdout: "..\n2 passed" } }),
  { ok: true, text: "pytest → exit 0 · 2 passed" }));
t("run_command fail shows stderr tail", () => assert.deepStrictEqual(
  s({ tool: "run_command", args: { cmd: "pytest" }, result: { exit_code: 1, stderr: "x\nAssertionError: 3 != 4" } }),
  { ok: false, text: "pytest → exit 1 · AssertionError: 3 != 4" }));
t("error", () => assert.deepStrictEqual(
  s({ tool: "read_file", result: { error: "no such file: a.py\nstack" } }),
  { ok: false, text: "no such file: a.py" }));
t("write_file", () => assert.strictEqual(s({ tool: "write_file", args: { path: "review.md" }, result: { ok: true } }).text, "wrote review.md"));
t("write_file path from the result", () => assert.strictEqual(
  s({ tool: "write_file", result: { path: "review.md", digest: "b6", bytes: 1219 } }).text, "wrote review.md (1219 bytes)"));
t("task_update", () => assert.strictEqual(
  s({ tool: "task_update", result: { id: "t-c9", status: "done", dag: true } }).text, "task t-c9 → done"));
t("completion accepted", () => assert.ok(
  s({ tool: "attempt_completion", result: { completed: true, summary: "all 7 covered" } }).text.startsWith("completion accepted: all 7")));
t("completion rejected", () => assert.strictEqual(
  s({ tool: "attempt_completion", result: { completed: false, missing_evidence: ["x"] } }).ok, false));
t("status:error with a said line is a failure, not a ✓", () => assert.deepStrictEqual(
  s({ tool: "set_role_mode", result: { status: "error", said: "Unknown role 'x'." } }),
  { ok: false, text: "Unknown role 'x'." }));
t("ok:false without said is a failure", () => assert.strictEqual(
  s({ tool: "x", result: { ok: false, reason: "nope" } }).ok, false));
t("non-string error still reads as text", () => assert.strictEqual(
  s({ tool: "x", result: { error: { code: "E1", message: "bad" } } }).text.includes("[object Object]"), false));
t("unknown falls back to short JSON", () => assert.ok(s({ tool: "x", result: { a: 1 } }).text === '{"a":1}'));
t("command result prefers said", () => assert.strictEqual(
  c({ name: "story_close", ok: true, result: { status: "ok", said: "Closed 'Agentic learning review' — on the brag board." } }),
  "Closed 'Agentic learning review' — on the brag board."));
t("command result without said", () => { assert.strictEqual(c({ ok: true, result: { status: "ok" } }), "done");
  assert.strictEqual(c({ ok: false, result: {} }), "failed"); });
t("status chips speak plainly", () => {
  assert.strictEqual(chip("ok"), "");
  assert.strictEqual(chip("awaiting_approval"), "Waiting for approval");
  assert.strictEqual(chip("some_new_state"), "some new state");
});
t("approval resolution text", () => {
  assert.strictEqual(res("deny"), "Denied ✗");
  assert.strictEqual(res("approve"), "Approved ✓");
  assert.ok(res("always").includes("always allowed"));
});
t("token meter text", () => {
  assert.strictEqual(meter({ turn_tokens: 12345, turn_budget: 60000, cached_pct: 85, mission_tokens: 40210, mission_budget: 200000, measured: true }),
    "This reply: 12k / 60k tokens · 85% cached · mission 40k / 200k");
  assert.ok(meter({ turn_tokens: 900, measured: false }).startsWith("This reply: ~900 tokens"));
});
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
