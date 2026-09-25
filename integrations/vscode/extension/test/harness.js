/**
 * Node harness test for the VS Code extension's sidecar spawn path.
 *
 * Imports the COMPILED out/sidecar.js (the same module extension.ts uses),
 * spawns the real prototype/awino_sidecar.py, and asserts:
 *   1. hello -> ready event (spawn path from the extension host code path)
 *   2. user_message -> turn_result through the real harness pipeline
 *   3. approval round-trip: write_file pauses -> approval_requested with a
 *      diff -> approve -> file on disk + journal entry; deny -> no file.
 *
 * This exercises the exact SidecarClient class the extension uses. The GUI
 * half (webview rendering, modals) is compiled-not-run — a headless VS Code
 * run is out of scope in this environment (see EXTENSION_SPEC.md §11).
 */
"use strict";
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const { SidecarClient } = require("../out/sidecar.js");

const REPO = path.resolve(__dirname, "..", "..", "..", "..", "prototype");
const SIDECAR = path.join(REPO, "awino_sidecar.py");

// Windows CI: the interpreter on PATH is `python` (setup-python), not
// `python3` — which does not exist on stock Windows.
const PYTHON = process.platform === "win32" ? "python" : "python3";
// run_command executes with shell=True (cmd.exe on Windows), so `touch`
// — a Unix-only command — must become a cmd-native file creation.
const touchCmd = (f) => (process.platform === "win32" ? `type nul > ${f}` : `touch ${f}`);

// Step 3 streaming fixtures: >4096 chars so the 4KB payload split is exercised.
const BIG_THINK = "THINK-" + "t".repeat(5000);
const BIG_SAID = "SAID-" + "s".repeat(5000);

function turn(kw) {
  const t = {
    header: "echo",
    objective: "Test objective",
    plan: ["Step one", "Step two"],
    tool_calls: [],
    questions: [],
    assumptions: ["Working hypothesis: the test needs this action."],
    progress_delta: "Working on the test objective.",
    done_claim: false,
  };
  return Object.assign(t, kw);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function commandResult(client, name, args) {
  const p = client.waitFor(
    (e) => e.event === "command_result" && e.name === name,
    60000
  );
  client.command(name, args || {});
  return p;
}

async function main() {
  assert.ok(fs.existsSync(SIDECAR), "sidecar exists: " + SIDECAR);
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "awino-harness-test-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "awino-home-test-"));

  const script = [
    turn({
      plan: ["Investigate the request", "Make the change", "Verify with evidence"],
      assumptions: ["Hypothesis: a small scoped change addresses the objective; the plan will say how."],
      progress_delta: "Planning the change.",
    }),
    turn({
      tool_calls: [{ name: "write_file", args: { path: "notes.txt", content: "Hello from the harness\n" } }],
      assumptions: ["Hypothesis: notes.txt does not exist yet; creating it addresses the objective."],
      progress_delta: "Creating notes.txt.",
    }),
    turn({
      tool_calls: [{ name: "run_command", args: { cmd: touchCmd("ran-check.txt") } }],
      assumptions: ["Attack: the command could fail silently, so its absence afterwards is the falsifier."],
      progress_delta: "Running the check command.",
    }),
    // --- Step 3 (sidecar streaming protocol) scripted turns ---
    turn({
      tool_calls: [{ name: "list_dir", args: {} }],
      chunks: [
        ["thinking", BIG_THINK],
        ["said", BIG_SAID],
      ],
      assumptions: ["Hypothesis: the workspace root lists the project files; reading it addresses the objective."],
      progress_delta: "Inspected the workspace.",
    }),
    turn({
      plan: ["Confirm the workspace state", "Report back"],
      assumptions: ["Hypothesis: the directory listing from the previous turn is sufficient context."],
      progress_delta: "Confirming workspace state.",
    }),
    turn({
      tool_calls: [{ name: "run_command", args: { cmd: touchCmd("streamed-run.txt") } }],
      chunks: [
        ["thinking", "The command needs approval; I will present it."],
        ["said", "Preparing the check command for approval."],
      ],
      assumptions: ["Attack: the command could fail silently, so its absence afterwards is the falsifier."],
      progress_delta: "Running the streamed check command.",
    }),
  ];

  const client = new SidecarClient();

  // 1. spawn hello -> ready (the extension host code path)
  const ready = await client.start({
    python: PYTHON,
    sidecarPath: SIDECAR,
    workspace: ws,
    provider: "scripted",
    script,
    home,
  }, 60000);
  assert.strictEqual(ready.event, "ready", "hello yields ready");
  assert.ok(ready.protocol, "ready carries protocol version");
  console.log("ok 1 - hello -> ready (protocol " + ready.protocol + ")");

  // mission + contract approval (mirrors test_sidecar.py)
  let r = await commandResult(client, "mission", { text: "Fix the login bug", criteria: ["manual"] });
  assert.ok(r.ok, "mission ok: " + JSON.stringify(r));
  r = await commandResult(client, "approve-contract", { scope: [] });
  assert.ok(r.ok, "approve-contract ok");

  // 2. user_message -> turn_result through the real pipeline
  let p = client.waitFor((e) => e.event === "turn_result", 60000);
  client.userMessage("plan the change");
  let tr = await p;
  assert.strictEqual(tr.result.status, "ok", "plan turn ok: " + JSON.stringify(tr.result));
  console.log("ok 2 - user_message -> turn_result (status ok)");

  r = await commandResult(client, "approve-contract", { scope: ["notes.txt"] });
  assert.ok(r.ok, "scoped approve-contract ok");
  const st = await commandResult(client, "status", {});
  assert.strictEqual(st.result.status.phase, "BUILD", "phase is BUILD");

  // 3a. approval round-trip: write_file pauses for approval with a diff
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  const pap = client.waitFor((e) => e.event === "approval_requested", 60000);
  client.userMessage("create the notes file");
  tr = await p;
  assert.strictEqual(tr.result.status, "awaiting_approval", "turn pauses for approval");
  const ap = await pap;
  assert.strictEqual(ap.approvals.length, 1, "one approval");
  const item = ap.approvals[0];
  assert.strictEqual(item.tool, "write_file", "approval is for write_file");
  assert.ok(item.diff && item.diff.includes("Hello from the harness"), "diff carries the new content");
  assert.strictEqual(item.old_exists, false, "old_exists false for new file");
  assert.ok(!fs.existsSync(path.join(ws, "notes.txt")), "not written before approval");
  console.log("ok 3a - approval_requested with diff, file not yet written");

  // approve -> file on disk, journal records it
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  client.approve(item.id, "approve");
  tr = await p;
  assert.strictEqual(tr.result.status, "ok", "resumed turn ok");
  // write_file uses Python text mode: on Windows the \n becomes \r\n on
  // disk, so normalize before comparing.
  const notesOnDisk = fs.readFileSync(path.join(ws, "notes.txt"), "utf8").replace(/\r\n/g, "\n");
  assert.strictEqual(notesOnDisk, "Hello from the harness\n");
  const j = await commandResult(client, "journal", {});
  const tools = j.result.journal.map((x) => x.tool);
  assert.ok(tools.includes("write_file"), "journal records write_file");
  console.log("ok 3b - approve -> file written + journal entry");

  // 3c. deny path: denied command must not execute
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  const pap2 = client.waitFor((e) => e.event === "approval_requested", 60000);
  client.userMessage("run the check");
  tr = await p;
  assert.strictEqual(tr.result.status, "awaiting_approval", "run_command pauses too");
  const ap2 = await pap2;
  assert.strictEqual(ap2.approvals[0].tool, "run_command", "approval is for run_command");
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  client.approve(ap2.approvals[0].id, "deny");
  tr = await p;
  assert.ok(!fs.existsSync(path.join(ws, "ran-check.txt")), "denied command must not execute");
  console.log("ok 3c - deny -> command not executed");

  // 4. malformed input never kills the extension's client
  p = client.waitFor((e) => e.event === "command_result" && e.name === "status", 60000);
  client.send({ cmd: "frobnicate" });
  const errEv = await client.waitFor((e) => e.event === "error", 30000);
  assert.ok(errEv.message.includes("frobnicate"), "unknown cmd -> error event");
  client.command("status", {});
  const okEv = await p;
  assert.ok(okEv.ok, "sidecar alive after unknown command");
  console.log("ok 4 - unknown command fails closed, sidecar survives");

  // 6. streamed turn: turn_start -> deltas/checks/tool_progress -> turn_result
  // (phase is VERIFY by now: the approved write in 3b advanced BUILD -> VERIFY)
  const stBefore = await commandResult(client, "status", {});
  const streamed = [];
  const collectStreamed = (e) => streamed.push(e);
  client.on("event", collectStreamed);
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  client.send({ cmd: "user_message", text: "list the workspace directory contents", stream: true });
  tr = await p;
  client.off("event", collectStreamed);

  const stypes = streamed.map((e) => e.event);
  assert.strictEqual(stypes[0], "turn_start", "first event is turn_start");
  assert.strictEqual(stypes[stypes.length - 1], "turn_result", "last event is turn_result");
  const ts = streamed[0];
  assert.ok(/^t\d+$/.test(ts.turn_id), "turn_start carries turn_id");
  assert.strictEqual(ts.phase, stBefore.result.status.phase,
    "turn_start phase matches the live phase");
  assert.ok(typeof ts.mode.id === "string" && ts.mode.id.length > 0,
    "turn_start carries the routed mode id");
  assert.strictEqual(ts.mode.source, "stage", "turn_start mode source is stage");
  assert.strictEqual(ts.persona, null, "turn_start persona null");

  // payload cap: every emitted text payload is at most 4096 chars
  for (const e of streamed) {
    if (typeof e.text === "string") {
      assert.ok(e.text.length <= 4096,
        `payload cap respected for ${e.event} (got ${e.text.length})`);
    }
  }

  // big thinking chunk (>4096) split into 2 deltas, reassembles exactly
  const thinkDeltas = streamed.filter((e) => e.event === "thinking_delta");
  assert.strictEqual(thinkDeltas.length, 2, "5006-char thinking chunk split into 2 deltas");
  assert.strictEqual(thinkDeltas.map((e) => e.text).join(""), BIG_THINK,
    "thinking deltas reassemble exactly");

  // said deltas: the scripted said chunk (>4096) split in 2, reassembles exactly.
  // (With explicit chunks, only the chunks stream — they are the simulated
  // token stream; progress_delta streams only on the no-chunks fallback path.)
  const saidDeltas = streamed.filter((e) => e.event === "said_delta");
  assert.strictEqual(saidDeltas.length, 2, "5006-char said chunk split into 2 deltas");
  assert.strictEqual(saidDeltas.map((e) => e.text).join(""), BIG_SAID,
    "said deltas reassemble exactly");

  // harness checks parallel the journal records: contract, judge votes, validation
  const checks = streamed.filter((e) => e.event === "harness_check");
  const checkNames = checks.map((e) => e.check);
  assert.ok(checkNames.includes("contract"), "contract check emitted");
  assert.ok(checkNames.includes("validation"), "validation check emitted");
  assert.ok(checkNames.some((n) => n.startsWith("judge:")),
    "per-vote judge check(s) emitted: " + JSON.stringify(checkNames));
  assert.ok(checks.every((e) => e.verdict === "pass"), "all streamed checks pass");
  assert.ok(checks.every((e) => e.turn_id === ts.turn_id), "checks carry the turn_id");

  // tool_progress wraps the list_dir execution with timing
  const tp = streamed.filter((e) => e.event === "tool_progress" && e.tool === "list_dir");
  assert.deepStrictEqual(tp.map((e) => e.phase), ["start", "done"],
    "tool_progress start -> done");
  assert.ok(tp.every((e) => e.turn_id === ts.turn_id), "tool_progress carries turn_id");
  assert.ok(typeof tp[1].ms === "number" && tp[1].ms >= 0, "tool_progress done carries ms");
  assert.ok(tp[0].summary.length > 0, "tool_progress start carries a summary");

  // ordering invariant: turn_start < deltas < turn_result
  const firstDelta = streamed.findIndex(
    (e) => e.event === "thinking_delta" || e.event === "said_delta");
  assert.ok(firstDelta > 0, "deltas were emitted");
  assert.ok(firstDelta < stypes.lastIndexOf("turn_result"), "deltas precede turn_result");

  // turn_result is enriched with the full thinking trace and finalized checks
  assert.strictEqual(tr.result.status, "ok", "streamed turn ok");
  assert.strictEqual(tr.result.thinking, BIG_THINK, "turn_result.thinking is the full trace");
  assert.ok(Array.isArray(tr.result.checks), "turn_result.checks present");
  assert.deepStrictEqual(tr.result.checks.map((c) => c.check).sort(),
    checkNames.slice().sort(), "turn_result.checks match the emitted harness checks");
  console.log("ok 6 - streamed turn: turn_start -> deltas/checks/tool_progress -> enriched turn_result");

  // 7. non-streamed turn: exactly the legacy event set, no new result keys
  const legacy = [];
  const collectLegacy = (e) => legacy.push(e);
  client.on("event", collectLegacy);
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  client.userMessage("confirm the workspace state");
  tr = await p;
  client.off("event", collectLegacy);
  assert.deepStrictEqual(legacy.map((e) => e.event), ["turn_result"],
    "non-streamed turn emits only turn_result");
  assert.ok(!("thinking" in tr.result), "no thinking key on legacy turn_result");
  assert.ok(!("checks" in tr.result), "no checks key on legacy turn_result");
  console.log("ok 7 - non-streamed turn: legacy event set only, no thinking/checks keys");

  // 8. streamed approval round-trip: pause card carries thinking/checks,
  //    resume emits approval_gate + tool_progress and enriches the result
  const appr = [];
  const collectAppr = (e) => appr.push(e);
  client.on("event", collectAppr);
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  const pap3 = client.waitFor((e) => e.event === "approval_requested", 60000);
  client.send({ cmd: "user_message", text: "run the streamed check", stream: true });
  tr = await p;
  const ap3 = await pap3;
  assert.strictEqual(tr.result.status, "awaiting_approval", "streamed turn pauses for approval");
  assert.strictEqual(tr.result.thinking, "The command needs approval; I will present it.",
    "paused card carries the thinking trace");
  assert.ok(tr.result.checks.some((c) => c.check === "validation" && c.verdict === "pass"),
    "paused card carries the checks so far");
  const item3 = ap3.approvals[0];
  assert.strictEqual(item3.tool, "run_command", "approval is for run_command");
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  client.approve(item3.id, "approve");
  tr = await p;
  client.off("event", collectAppr);
  assert.strictEqual(tr.result.status, "ok", "resumed streamed turn ok");
  const gate = tr.result.checks.find((c) => c.check === "approval_gate");
  assert.ok(gate && gate.verdict === "pass", "approval_gate pass in final checks");
  const wtp = appr
    .filter((e) => e.event === "tool_progress" && e.tool === "run_command")
    .map((e) => e.phase);
  assert.deepStrictEqual(wtp, ["start", "done"], "run_command tool_progress emitted after resume");
  assert.ok(fs.existsSync(path.join(ws, "streamed-run.txt")), "approved command executed");
  console.log("ok 8 - streamed approval round-trip: pause card + resume enrichment");

  await client.close();
  await sleep(500);
  assert.ok(!client.running, "client closed cleanly");
  console.log("ok 5 - bye/close clean");

  fs.rmSync(ws, { recursive: true, force: true });
  fs.rmSync(home, { recursive: true, force: true });
  console.log("\nALL 10 HARNESS TESTS PASSED");
}

main().catch((e) => {
  console.error("HARNESS FAILED:", e);
  process.exit(1);
});
