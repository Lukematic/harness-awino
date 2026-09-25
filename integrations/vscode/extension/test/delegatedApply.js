/**
 * Node test for native tool application (delegated apply + terminal).
 *
 * Part 1 (pure, no sidecar): delegatedPure helpers — sha256Hex,
 * stripAnsi, resolveDelegatedPath — from the compiled out/.
 *
 * Part 2 (protocol, real sidecar): drives the full delegated round-trip
 * through SidecarClient with capabilities { delegated_apply: true }:
 *   1. scripted turn with two write_file calls -> approval_requested
 *      (patch_file carries proposed_content)
 *   2. approve both -> ONE apply_requested event (single batch)
 *   3. the test (playing the extension) answers apply_result with honest
 *      digests -> turn_result ok, journal records the extension's digest,
 *      and the sidecar itself wrote NOTHING (no direct write in
 *      delegated mode)
 *   4. scripted run_command -> approval -> ONE terminal_requested event;
 *      the test streams terminal_output chunks, then terminal_result;
 *      the tool_result carries the streamed stdout + exit code
 *   5. checkpoint_created is journaled; revert_checkpoint restores
 *
 * The vscode-dependent half (WorkspaceEdit, vscode.diff, terminal shell
 * integration) cannot run headless; it is proven by the Windows GUI
 * workflow (NATIVE_APPLY_GUI_ASSERTIONS.md).
 */
"use strict";
const assert = require("assert");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const { SidecarClient } = require("../out/sidecar.js");
const pure = require("../out/delegatedPure.js");

const REPO = path.resolve(__dirname, "..", "..", "..", "..", "prototype");
const SIDECAR = path.join(REPO, "awino_sidecar.py");
const PYTHON = process.platform === "win32" ? "python" : "python3";

function sha256(s) {
  return crypto.createHash("sha256").update(s, "utf8").digest("hex");
}

function turn(kw) {
  const t = {
    header: "echo",
    objective: "Test objective",
    plan: ["Step one"],
    tool_calls: [],
    questions: [],
    assumptions: ["Working hypothesis: the test needs this action."],
    progress_delta: "Working on the test objective.",
    done_claim: false,
  };
  return Object.assign(t, kw);
}

async function commandResult(client, name, args) {
  const p = client.waitFor(
    (e) => e.event === "command_result" && e.name === name,
    60000
  );
  client.command(name, args || {});
  return p;
}

function testPure() {
  // sha256Hex matches node's crypto
  assert.strictEqual(pure.sha256Hex("hello\n"), sha256("hello\n"));
  console.log("ok P1 - sha256Hex matches node crypto");

  // stripAnsi removes CSI colors and OSC 633 markers
  const dirty = "\x1b]633;C\x07\x1b[32mgreen\x1b[0m plain";
  assert.strictEqual(pure.stripAnsi(dirty), "green plain");
  assert.strictEqual(pure.stripAnsi("no escapes"), "no escapes");
  console.log("ok P2 - stripAnsi removes ANSI/OSC sequences");

  // resolveDelegatedPath: ok inside, rejects escapes
  const root = path.resolve(os.tmpdir(), "awino-pure-test");
  let r = pure.resolveDelegatedPath(root, {
    call_id: "c1", idem_key: "k1", tool: "write_file",
    path: "sub/a.txt", content: "x", old_digest: null,
  });
  assert.ok(!r.error, "inside path accepted: " + r.error);
  assert.ok(r.absPath.endsWith(path.join("sub", "a.txt")));
  for (const bad of ["../evil.txt", "/abs/path.txt", "C:\\win.txt", "a/../../b"]) {
    r = pure.resolveDelegatedPath(root, {
      call_id: "c1", idem_key: "k1", tool: "write_file",
      path: bad, content: "x", old_digest: null,
    });
    assert.ok(r.error, "escape rejected: " + bad);
  }
  r = pure.resolveDelegatedPath(root, {
    call_id: "c1", idem_key: "k1", tool: "frobnicate",
    path: "a.txt", content: "x", old_digest: null,
  });
  assert.ok(r.error, "unknown tool rejected");
  console.log("ok P3 - resolveDelegatedPath accepts inside, rejects escapes");
}

async function main() {
  testPure();
  assert.ok(fs.existsSync(SIDECAR), "sidecar exists: " + SIDECAR);

  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "awino-delegated-test-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "awino-deleghome-test-"));
  // Git repo so the checkpoint path is exercised for real.
  execFileSync("git", ["init", "-q", ws]);
  execFileSync("git", ["-C", ws, "config", "user.email", "t@t"]);
  execFileSync("git", ["-C", ws, "config", "user.name", "t"]);
  fs.writeFileSync(path.join(ws, "seed.txt"), "seed\n");
  execFileSync("git", ["-C", ws, "add", "-A"]);
  execFileSync("git", ["-C", ws, "commit", "-qm", "init"]);

  const PATCH = [
    "--- a/seed.txt",
    "+++ b/seed.txt",
    "@@ -1 +1 @@",
    "-seed",
    "+seed-patched",
    "",
  ].join("\n");

  const script = [
    turn({
      plan: ["Write two files"],
      assumptions: ["Hypothesis: two writes in one drain batch together."],
      progress_delta: "Planning the writes.",
    }),
    turn({
      tool_calls: [
        { name: "write_file", args: { path: "a.txt", content: "alpha\n" } },
        { name: "patch_file", args: { path: "seed.txt", diff: PATCH } },
      ],
      assumptions: ["Hypothesis: a.txt is new; seed.txt exists with 'seed'."],
      progress_delta: "Writing a.txt and patching seed.txt.",
    }),
    // v0.6: the recursive loop continues after the approval drain, so a
    // no-op turn lets "do the writes" exit cleanly with "ok" after the
    // delegated apply completes.
    turn({
      assumptions: ["Hypothesis: no further action is needed; the delegated writes already address the objective."],
      progress_delta: "Writes delegated; nothing further.",
    }),
    turn({
      tool_calls: [{ name: "run_command", args: { cmd: "echo hello-terminal" } }],
      assumptions: [
        "Attack: the command could fail silently, so its absence afterwards is the falsifier.",
      ],
      progress_delta: "Running the terminal command.",
    }),
    // v0.6: no-op for the terminal round-trip to exit cleanly.
    turn({
      assumptions: ["Hypothesis: no further action is needed; the terminal command already address the objective."],
      progress_delta: "Terminal command done; nothing further.",
    }),
  ];

  const client = new SidecarClient();
  const ready = await client.start(
    {
      python: PYTHON,
      sidecarPath: SIDECAR,
      workspace: ws,
      provider: "scripted",
      script,
      home,
      capabilities: { delegated_apply: true, terminal_stream: true },
    },
    60000
  );
  assert.strictEqual(ready.event, "ready", "hello yields ready");
  console.log("ok 1 - hello with capabilities -> ready");

  let r = await commandResult(client, "mission", {
    text: "Delegated apply test",
    criteria: ["manual"],
  });
  assert.ok(r.ok, "mission ok");
  r = await commandResult(client, "approve-contract", { scope: [] });
  assert.ok(r.ok, "approve-contract ok");

  // Plan turn.
  let p = client.waitFor((e) => e.event === "turn_result", 60000);
  client.userMessage("plan the writes");
  await p;
  r = await commandResult(client, "approve-contract", {
    scope: ["a.txt", "seed.txt"],
  });
  assert.ok(r.ok, "scoped approve-contract ok");

  // --- Delegated write round-trip ---
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  const pap = client.waitFor((e) => e.event === "approval_requested", 60000);
  client.userMessage("do the writes");
  const tr1 = await p;
  assert.strictEqual(tr1.result.status, "awaiting_approval");
  const ap = await pap;
  assert.strictEqual(ap.approvals.length, 2, "two approvals");
  const patchAp = ap.approvals.find((a) => a.tool === "patch_file");
  assert.ok(patchAp, "patch_file approval present");
  assert.ok(
    typeof patchAp.proposed_content === "string" &&
      patchAp.proposed_content.includes("seed-patched"),
    "patch_file approval carries proposed_content for vscode.diff"
  );
  console.log("ok 2 - approval_requested: 2 approvals, patch has proposed_content");

  // Approve both; the sidecar must emit ONE apply_requested batch.
  // (The first approve returns awaiting_approval — the drain defers until
  // the round is complete.)
  const par = client.waitFor((e) => e.event === "apply_requested", 60000);
  const ptr = client.waitFor(
    (e) => e.event === "turn_result" && e.result.status === "ok",
    90000
  );
  for (const a of ap.approvals) {
    client.approve(a.id, "approve");
  }
  const ar = await par;
  assert.ok(ar.request_id, "apply_requested carries request_id");
  assert.ok(ar.changeset_id, "apply_requested carries changeset_id");
  assert.strictEqual(ar.edits.length, 2, "ONE batch with both edits");
  for (const e of ar.edits) {
    assert.ok(e.call_id && e.path && typeof e.content === "string", "edit wire shape");
    assert.strictEqual(e.digest, sha256(e.content), "loop-computed digest matches content");
  }
  console.log("ok 3 - single apply_requested batch for both writes");

  // Play the extension: answer with honest digests.
  client.send({
    cmd: "apply_result",
    request_id: ar.request_id,
    results: ar.edits.map((e) => ({
      call_id: e.call_id,
      ok: true,
      result: { path: e.path, digest: e.digest, bytes: e.content.length },
    })),
  });
  const tr2 = await ptr;
  assert.strictEqual(tr2.result.status, "ok", "turn completes after apply_result");
  // Delegated mode: the sidecar itself wrote NOTHING.
  assert.ok(!fs.existsSync(path.join(ws, "a.txt")), "sidecar did not write a.txt directly");
  assert.strictEqual(fs.readFileSync(path.join(ws, "seed.txt"), "utf8"), "seed\n",
    "sidecar did not patch seed.txt directly");
  // Journal records the extension's digest.
  const j = await commandResult(client, "journal", {});
  const wr = j.result.journal.find(
    (x) => x.tool === "write_file" && x.args && x.args.path === "a.txt"
  );
  assert.ok(wr, "journal has write_file tool entry");
  assert.strictEqual(wr.digest, sha256("alpha\n"), "journal digest is the extension's");
  // checkpoint_created is an event (not an effect-journal entry).
  const evs = await commandResult(client, "events", {});
  const cp = evs.result.events.find((x) => x.type === "checkpoint_created");
  assert.ok(cp, "checkpoint_created event journaled before the delegated write");
  console.log("ok 4 - apply_result -> turn ok; no direct write; journal + checkpoint");

  // --- Delegated terminal round-trip ---
  p = client.waitFor((e) => e.event === "turn_result", 60000);
  const pap2 = client.waitFor((e) => e.event === "approval_requested", 60000);
  client.userMessage("run the terminal command");
  await p;
  const ap2 = await pap2;
  assert.strictEqual(ap2.approvals[0].tool, "run_command");
  const preq = client.waitFor((e) => e.event === "terminal_requested", 60000);
  const ptr2 = client.waitFor((e) => e.event === "turn_result", 90000);
  client.approve(ap2.approvals[0].id, "approve");
  const tq = await preq;
  assert.ok(tq.request_id, "terminal_requested carries request_id");
  assert.strictEqual(tq.cmd, "echo hello-terminal");
  assert.ok(tq.cwd, "terminal_requested carries cwd");
  console.log("ok 5 - terminal_requested emitted on approve");

  // Stream chunks, then the final result (playing the extension).
  client.send({ cmd: "terminal_output", request_id: tq.request_id, data: "hello-" });
  client.send({ cmd: "terminal_output", request_id: tq.request_id, data: "terminal\n" });
  client.send({
    cmd: "terminal_result",
    request_id: tq.request_id,
    exit_code: 0,
    timed_out: false,
    killed: false,
    reason: "completed",
  });
  const tr3 = await ptr2;
  assert.strictEqual(tr3.result.status, "ok", "turn completes after terminal_result");
  const j2 = await commandResult(client, "journal", {});
  const run = j2.result.journal.find((x) => x.tool === "run_command");
  assert.ok(run, "journal has run_command tool entry");
  assert.ok(
    run.result_summary.includes("hello-terminal"),
    "streamed stdout journaled: " + run.result_summary.slice(0, 200)
  );
  console.log("ok 6 - terminal_output streamed; terminal_result -> tool_result");

  // --- revert_checkpoint ---
  const rr = await commandResult(client, "revert_checkpoint", {});
  assert.ok(rr.ok, "revert_checkpoint ok: " + JSON.stringify(rr.result));
  assert.ok(rr.result.ok, "revert reports ok");
  console.log("ok 7 - revert_checkpoint command round-trips");

  client.close();
  await new Promise((r) => setTimeout(r, 500));
  console.log("delegated-apply: all protocol tests passed");
}

main().catch((e) => {
  console.error("delegated-apply FAILED:", e);
  process.exit(1);
});
