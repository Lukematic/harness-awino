/**
 * Fixture tests for the Awino Rigor Coach VS Code surface
 * (integrations/vscode/extension/src/extension.ts).
 *
 * Covers: command registration, disconnected truth (no session -> honest
 * warning, never a throw), the rigor status-bar item wiring, journaled
 * report rendering, and the status-bar score thresholds.
 *
 * Run: node test/rigor.js   (from integrations/vscode/extension)
 */
"use strict";
const assert = require("assert");
const Module = require("module");

// ---------------------------------------------------------------- vscode stub
const registered = {};          // command id -> handler
const warnings = [];
const errors = [];
const infos = [];
const channels = {};            // name -> { lines: [], shown: bool }
const statusBars = [];          // created status bar items
const treeProviders = [];

function makeChannel(name) {
  const ch = {
    name,
    lines: [],
    shown: false,
    appendLine(l) { ch.lines.push(String(l)); },
    append(l) { ch.lines.push(String(l)); },
    show(preserve) { ch.shown = true; },
    dispose() {},
  };
  channels[name] = ch;
  return ch;
}

function makeStatusBar() {
  const bar = {
    text: "",
    tooltip: "",
    command: undefined,
    hidden: true,
    show() { bar.hidden = false; },
    hide() { bar.hidden = true; },
    dispose() {},
  };
  statusBars.push(bar);
  return bar;
}

const vscodeStub = {
  StatusBarAlignment: { Left: 1, Right: 2 },
  window: {
    showWarningMessage(m) { warnings.push(String(m)); return Promise.resolve(undefined); },
    showErrorMessage(m) { errors.push(String(m)); return Promise.resolve(undefined); },
    showInformationMessage(m) { infos.push(String(m)); return Promise.resolve(undefined); },
    showQuickPick() { return Promise.resolve(undefined); },
    showInputBox() { return Promise.resolve(undefined); },
    createOutputChannel(name) { return makeChannel(name); },
    createStatusBarItem() { return makeStatusBar(); },
    registerTreeDataProvider(viewId, provider) { treeProviders.push(viewId); return { dispose() {} }; },
    registerWebviewViewProvider() { return { dispose() {} }; },
    withProgress() { return Promise.resolve(undefined); },
  },
  commands: {
    registerCommand(id, fn) { registered[id] = fn; return { dispose() {} }; },
    executeCommand() { return Promise.resolve(undefined); },
    getCommands() { return Promise.resolve(Object.keys(registered)); },
  },
  workspace: {
    // No folder open: connect() exits early, no sidecar spawned.
    workspaceFolders: undefined,
    getConfiguration() {
      return { get: () => undefined, has: () => false, update: () => Promise.resolve() };
    },
    onDidChangeConfiguration() { return { dispose() {} }; },
    onDidChangeWorkspaceFolders() { return { dispose() {} }; },
  },
  env: { machineId: "test-machine", sessionId: "test-session", uiKind: 1 },
  Uri: { file: (p) => ({ fsPath: p }), parse: (s) => ({ fsPath: s }) },
  EventEmitter: class { event() {} fire() {} dispose() {} },
  TreeItem: class {},
  ThemeIcon: class {},
  ViewColumn: { One: 1 },
  ProgressLocation: { Notification: 1 },
  ConfigurationTarget: { Global: 1 },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "vscode") return vscodeStub;
  return origLoad.call(this, request, parent, isMain);
};

let ext;
try {
  ext = require("../out/extension.js");
} finally {
  Module._load = origLoad;
}

// ---------------------------------------------------------------- harness
let passed = 0, failed = 0;
function t(name, fn) {
  try {
    const r = fn();
    if (r && typeof r.then === "function") {
      return r.then(
        () => { passed++; },
        (e) => { failed++; console.log("FAIL - " + name + "  (rejected: " + (e && e.message) + ")"); });
    }
    passed++;
  } catch (e) {
    failed++;
    console.log("FAIL - " + name + "  (threw: " + (e && e.message) + ")");
  }
  return Promise.resolve();
}

function fakeContext() {
  return {
    subscriptions: [],
    globalState: { get: () => undefined, update: () => Promise.resolve() },
    workspaceState: { get: () => undefined, update: () => Promise.resolve() },
    secrets: { get: () => Promise.resolve(undefined), store: () => Promise.resolve(), delete: () => Promise.resolve() },
    extensionPath: "/tmp/fake-ext",
    extensionUri: { fsPath: "/tmp/fake-ext" },
    asAbsolutePath: (p) => "/tmp/fake-ext/" + p,
  };
}

async function main() {
  // Activate with no workspace folder: connect() must exit early and the
  // rigor command must still register.
  ext.activate(fakeContext());
  await new Promise((r) => setImmediate(r));

  await t("awino.showRigorReport is registered", () => {
    assert.ok(registered["awino.showRigorReport"], "command not registered");
  });

  await t("package.json declares the rigor command", () => {
    const pkg = require("../package.json");
    const cmds = (pkg.contributes && pkg.contributes.commands) || [];
    assert.ok(cmds.some((c) => c.command === "awino.showRigorReport"),
              "command missing from package.json contributes.commands");
  });

  await t("disconnected: warning, no throw", async () => {
    warnings.length = 0;
    await registered["awino.showRigorReport"](); // must not reject
    assert.ok(warnings.some((w) => /not connected/i.test(w)),
              "expected a 'not connected' warning, got: " + JSON.stringify(warnings));
  });

  await t("rigor status bar item wired to the command, starts hidden", () => {
    const bar = statusBars.find((b) => b.command === "awino.showRigorReport");
    assert.ok(bar, "no status bar item points at awino.showRigorReport");
    assert.strictEqual(bar.hidden, true, "rigor bar must start hidden");
  });

  await t("renderJournaledRigor: score, checks, failing, overrides", () => {
    const out = ext.renderJournaledRigor({
      mission_id: "m-1", score: 0.823, scored: "12/14",
      checks: { observable_proof: { status: "pass" }, tests_run: { status: "fail" } },
      failing: ["tests_run"], overrides: 2,
    });
    assert.ok(out.includes("m-1"), "mission id missing");
    assert.ok(out.includes("0.82"), "score missing: " + out);
    assert.ok(out.includes("[pass] observable_proof"), "check line missing");
    assert.ok(out.includes("Failing checks: tests_run"), "failing line missing");
    assert.ok(/overrides.*2/i.test(out), "override count missing");
  });

  await t("setRigorBar: thresholds and hide-on-bad-data", () => {
    const bar = statusBars.find((b) => b.command === "awino.showRigorReport");
    ext.setRigorBar({ score: 0.9, failing: [], mission_id: "m-1", scored: "14/14" });
    assert.strictEqual(bar.hidden, false);
    assert.ok(bar.text.includes("$(check)"), ">=0.8 should use check icon: " + bar.text);
    assert.ok(bar.text.includes("0.90"), "score text wrong: " + bar.text);
    ext.setRigorBar({ score: 0.6, failing: ["x"], mission_id: "m-2", scored: "1/2" });
    assert.ok(bar.text.includes("$(alert)"), "0.5-0.8 should use alert icon: " + bar.text);
    assert.ok(bar.tooltip.includes("Failing: x"), "tooltip should list failing: " + bar.tooltip);
    ext.setRigorBar({ score: 0.2, failing: [], mission_id: "m-3", scored: "1/1" });
    assert.ok(bar.text.includes("$(x)"), "<0.5 should use x icon: " + bar.text);
    ext.setRigorBar({ score: NaN, failing: [], mission_id: "m-4" });
    assert.strictEqual(bar.hidden, true, "non-finite score must hide the bar");
  });

  console.log(`\nrigor extension tests: ${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.log("FATAL: " + (e && e.stack)); process.exit(1); });
