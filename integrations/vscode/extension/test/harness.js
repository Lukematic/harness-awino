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
      tool_calls: [{ name: "run_command", args: { cmd: "touch ran-check.txt" } }],
      assumptions: ["Attack: the command could fail silently, so its absence afterwards is the falsifier."],
      progress_delta: "Running the check command.",
    }),
  ];

  const client = new SidecarClient();

  // 1. spawn hello -> ready (the extension host code path)
  const ready = await client.start({
    python: "python3",
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
  assert.strictEqual(fs.readFileSync(path.join(ws, "notes.txt"), "utf8"), "Hello from the harness\n");
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

  await client.close();
  await sleep(500);
  assert.ok(!client.running, "client closed cleanly");
  console.log("ok 5 - bye/close clean");

  fs.rmSync(ws, { recursive: true, force: true });
  fs.rmSync(home, { recursive: true, force: true });
  console.log("\nALL HARNESS TESTS PASSED");
}

main().catch((e) => {
  console.error("HARNESS FAILED:", e);
  process.exit(1);
});
