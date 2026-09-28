/**
 * hostE2E.js — drives the REAL extension end to end, headless.
 *
 * What is real:
 *   - out/extension.js (the compiled extension host code: activate, connect,
 *     key injection, message routing, commands, transcript history)
 *   - webview/chat.html + chat.js rendered in real Chromium (Playwright);
 *     the test clicks and types in that page like a user
 *   - the Python sidecar from bundled-sidecar/ (the same tree the VSIX
 *     ships), spawned by the extension's own SidecarClient
 *   - HTTP to an OpenAI-compatible gateway that enforces a Bearer key
 *
 * What is a stand-in (and why):
 *   - the `vscode` module: VS Code itself cannot be downloaded in this
 *     sandbox. The stub implements only what the extension calls; any other
 *     API access throws "MISSING vscode API", so nothing is silently faked.
 *   - the model: prototype/tests/fake_gateway.py answers with a TurnContract
 *     (it checks the key, echoes the harness header, answers time questions
 *     from the contract's ## NOW line, asks one question otherwise).
 *
 * Run (from integrations/vscode/extension, after `npm run compile` and
 * `node scripts/bundle-sidecar.js`):
 *   NODE_PATH=<dir containing playwright> node test/live/hostE2E.js <outDir>
 * Exit 0 only if every check passes. Screenshots of each step land in outDir.
 */
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const Module = require("module");
const { spawn, execFileSync } = require("child_process");
const { chromium } = require("playwright");

const EXT_DIR = path.resolve(__dirname, "..", "..");
const REPO = path.resolve(EXT_DIR, "..", "..", "..");
const OUT = path.resolve(process.argv[2] || path.join(os.tmpdir(), "awino-host-e2e"));
fs.mkdirSync(OUT, { recursive: true });

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond, detail: cond ? "" : String(detail || "").slice(0, 400) });
  console.log((cond ? "PASS " : "FAIL ") + name + (cond ? "" : "  [" + String(detail || "").slice(0, 300) + "]"));
}

// ------------------------------------------------------------ gateway
function startGateway() {
  return new Promise((resolve, reject) => {
    const p = spawn("python3", [path.join(REPO, "prototype", "tests", "fake_gateway.py")],
      { cwd: path.join(REPO, "prototype"), env: { ...process.env, PYTHONPATH: path.join(REPO, "prototype") } });
    p.stdout.once("data", (d) => resolve({ proc: p, port: parseInt(String(d).trim(), 10) }));
    p.stderr.on("data", (d) => process.stderr.write("[gateway] " + d));
    p.on("error", reject);
  });
}
function gatewayLog(port) {
  return new Promise((resolve) => {
    http.get(`http://127.0.0.1:${port}/__log`, (res) => {
      let b = ""; res.on("data", (c) => (b += c)); res.on("end", () => resolve(JSON.parse(b)));
    });
  });
}

// ------------------------------------------------------------ static server for webview assets
function startStatic() {
  const pages = {};
  const srv = http.createServer((req, res) => {
    const u = decodeURIComponent(req.url.split("?")[0]);
    if (pages[u]) { res.writeHead(200, { "Content-Type": "text/html" }); return res.end(pages[u]); }
    const f = path.join(EXT_DIR, u);
    if (!f.startsWith(EXT_DIR) || !fs.existsSync(f)) { res.writeHead(404); return res.end(); }
    const type = f.endsWith(".js") ? "text/javascript" : f.endsWith(".css") ? "text/css" : "application/octet-stream";
    res.writeHead(200, { "Content-Type": type }); fs.createReadStream(f).pipe(res);
  });
  return new Promise((r) => srv.listen(0, "127.0.0.1", () => r({ srv, pages, origin: `http://127.0.0.1:${srv.address().port}` })));
}

// ------------------------------------------------------------ vscode stand-in
function makeVscode(opts) {
  const commands = {};
  const settings = Object.assign({}, opts.settings);
  const cfgListeners = [];
  const ui = { infos: [], warnings: [], errors: [], inputs: opts.inputs, inputPrompts: [], quickPicks: [] };
  let webviewProvider = null;
  const disp = { dispose() {} };
  class EventEmitter {
    constructor() { this.l = []; this.event = (fn) => { this.l.push(fn); return disp; }; }
    fire(v) { this.l.forEach((fn) => fn(v)); } dispose() {}
  }
  const Uri = {
    file: (p) => ({ fsPath: p, path: p, scheme: "file", toString: () => "file://" + p }),
    parse: (s) => ({ fsPath: s, path: s, toString: () => s }),
    joinPath: (base, ...parts) => Uri.file(path.join(base.fsPath, ...parts)),
  };
  const api = {
    StatusBarAlignment: { Left: 1, Right: 2 },
    ConfigurationTarget: { Global: 1, Workspace: 2, WorkspaceFolder: 3 },
    TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 },
    ViewColumn: { One: 1, Beside: -2 },
    ProgressLocation: { Notification: 15 },
    EventEmitter, Uri,
    TreeItem: class { constructor(label, state) { this.label = label; this.collapsibleState = state; } },
    ThemeIcon: class { constructor(id) { this.id = id; } },
    ThemeColor: class { constructor(id) { this.id = id; } },
    Range: class {}, Position: class {}, WorkspaceEdit: class { replace() {} createFile() {} insert() {} },
    window: {
      showInformationMessage: (m) => { ui.infos.push(String(m)); return Promise.resolve(undefined); },
      showWarningMessage: (m) => { ui.warnings.push(String(m)); return Promise.resolve(undefined); },
      showErrorMessage: (m) => { ui.errors.push(String(m)); return Promise.resolve(undefined); },
      showInputBox: (o) => { ui.inputPrompts.push(o && o.prompt); return Promise.resolve(ui.inputs.shift()); },
      showQuickPick: (items) => { ui.quickPicks.push(items); return Promise.resolve(undefined); },
      setStatusBarMessage: () => disp,
      createOutputChannel: () => ({ append: (s) => opts.log(s), appendLine: (s) => opts.log(s + "\n"), show() {}, dispose() {} }),
      createStatusBarItem: () => ({ text: "", tooltip: "", show() {}, hide() {}, dispose() {} }),
      registerTreeDataProvider: () => disp,
      registerWebviewViewProvider: (id, p) => { if (id === "awino.chat") webviewProvider = p; return disp; },
      withProgress: (_o, fn) => fn({ report() {} }),
      onDidEndTerminalShellExecution: () => disp,
      onDidCloseTerminal: () => disp,
    },
    commands: {
      registerCommand: (id, fn) => { commands[id] = fn; return disp; },
      executeCommand: (id, ...a) => (commands[id] ? Promise.resolve(commands[id](...a)) : Promise.resolve(undefined)),
      getCommands: () => Promise.resolve(Object.keys(commands)),
    },
    workspace: {
      workspaceFolders: [{ uri: Uri.file(opts.workspace), name: path.basename(opts.workspace), index: 0 }],
      getConfiguration: (section) => ({
        get: (k, d) => { const v = settings[section ? section + "." + k : k]; return v === undefined ? d : v; },
        has: (k) => (section ? section + "." + k : k) in settings,
        inspect: (k) => {
          const key = section ? section + "." + k : k;
          return { key, defaultValue: undefined, globalValue: undefined,
            workspaceValue: settings[key], workspaceFolderValue: undefined };
        },
        update: (k, v) => {
          const key = section ? section + "." + k : k; settings[key] = v;
          cfgListeners.forEach((fn) => fn({ affectsConfiguration: (s) => key === s || key.startsWith(s + ".") }));
          return Promise.resolve();
        },
      }),
      onDidChangeConfiguration: (fn) => { cfgListeners.push(fn); return disp; },
      onDidChangeWorkspaceFolders: () => disp,
    },
    env: { machineId: "e2e", sessionId: "e2e", uiKind: 1, clipboard: { writeText: () => Promise.resolve(), readText: () => Promise.resolve("") }, openExternal: () => Promise.resolve(true) },
  };
  // Anything not implemented above is a loud failure, never a silent fake.
  function strict(obj, name) {
    return new Proxy(obj, {
      get(t, k) {
        if (k in t || typeof k === "symbol" || k === "then" || k === "__esModule" || k === "default") return t[k];
        const msg = `MISSING vscode API: ${name}.${String(k)}`;
        opts.missing.push(msg);
        throw new Error(msg);
      },
    });
  }
  const stub = strict(Object.assign({}, api, {
    window: strict(api.window, "vscode.window"),
    workspace: strict(api.workspace, "vscode.workspace"),
    commands: strict(api.commands, "vscode.commands"),
  }), "vscode");
  return { stub, commands, settings, ui, getProvider: () => webviewProvider };
}

function memento(init) {
  const m = Object.assign({}, init || {});
  return { get: (k, d) => (k in m ? m[k] : d), update: (k, v) => { m[k] = v; return Promise.resolve(); }, keys: () => Object.keys(m) };
}

// ------------------------------------------------------------ one scenario = one extension activation
async function runScenario(name, opts, steps) {
  console.log(`\n=== scenario: ${name} ===`);
  const workspace = opts.workspace;
  const hostLog = [];
  const missing = [];
  const vs = makeVscode({
    workspace, inputs: opts.inputs || [], missing,
    settings: Object.assign({ "awino.provider": "openai", "awino.endpoint": opts.endpoint, "awino.model": "claude-haiku-4-5-20251001-v1-project", "awino.pythonPath": "python3" }, opts.settings || {}),
    log: (s) => hostLog.push(String(s)),
  });
  // Fresh module instances per scenario (extension.ts keeps module state).
  Object.keys(require.cache).forEach((k) => { if (k.startsWith(path.join(EXT_DIR, "out"))) delete require.cache[k]; });
  const origLoad = Module._load;
  Module._load = function (request, parent, isMain) {
    if (request === "vscode") return vs.stub;
    return origLoad.call(this, request, parent, isMain);
  };
  const ext = require(path.join(EXT_DIR, "out", "extension.js"));
  Module._load = origLoad;

  const secrets = { "awino.apiKey.openai": opts.key };
  const context = {
    subscriptions: [], extensionPath: EXT_DIR, extensionUri: vs.stub.Uri.file(EXT_DIR),
    asAbsolutePath: (p) => path.join(EXT_DIR, p),
    globalState: memento({ "awino.onboarded": true, "awino.connectionImportOffered": true }),
    workspaceState: memento(),
    secrets: { get: (k) => Promise.resolve(secrets[k]), store: (k, v) => { secrets[k] = v; return Promise.resolve(); }, delete: (k) => { delete secrets[k]; return Promise.resolve(); }, onDidChange: () => ({ dispose() {} }) },
  };
  ext.activate(context);

  // Resolve the chat view into a real Chromium page.
  const stat = opts.stat;
  const browser = opts.browser;
  const page = await browser.newPage({ viewport: { width: 460, height: 900 } });
  page.on("pageerror", (e) => missing.push("WEBVIEW JS ERROR: " + e.message));
  let toWebview = [];
  let pageReady = false;
  let onFromWebview = null;
  const view = {
    visible: true, show() {}, onDidDispose: () => ({ dispose() {} }), onDidChangeVisibility: () => ({ dispose() {} }),
    webview: {
      options: {}, cspSource: stat.origin, html: "",
      asWebviewUri: (u) => ({ toString: () => stat.origin + "/" + path.relative(EXT_DIR, u.fsPath).split(path.sep).join("/") }),
      postMessage: (m) => { if (pageReady) page.evaluate((msg) => window.dispatchEvent(new MessageEvent("message", { data: msg })), m).catch(() => {}); else toWebview.push(m); return Promise.resolve(true); },
      onDidReceiveMessage: (fn) => { onFromWebview = fn; return { dispose() {} }; },
    },
  };
  await page.exposeBinding("__awinoToHost", (_src, json) => { if (onFromWebview) onFromWebview(JSON.parse(json)); });
  await page.addInitScript(() => {
    let st;
    window.acquireVsCodeApi = () => ({
      postMessage: (m) => window.__awinoToHost(JSON.stringify(m)),
      getState: () => st, setState: (s) => { st = s; },
    });
  });
  vs.getProvider().resolveWebviewView(view);
  stat.pages["/__chat.html"] = view.webview.html;
  await page.goto(stat.origin + "/__chat.html");
  pageReady = true;
  for (const m of toWebview) await page.evaluate((msg) => window.dispatchEvent(new MessageEvent("message", { data: msg })), m);
  toWebview = [];

  let shot = 0;
  const h = {
    page, vs, hostLog, missing,
    async shot(label) { const f = path.join(OUT, `${name}-${String(++shot).padStart(2, "0")}-${label}.png`); await page.screenshot({ path: f, fullPage: true }); return f; },
    async waitConnected() { await page.waitForFunction(() => /connected/.test(document.getElementById("statusline").textContent), null, { timeout: 30000 }); },
    cards() { return page.$$eval("#messages > .msg", (els) => els.map((e) => ({ cls: e.className, text: e.innerText }))); },
    async send(text) {
      const before = (await h.cards()).length;
      await page.fill("#input", text);
      await page.click("#send");
      await page.waitForFunction((n) => {
        const cards = document.querySelectorAll("#messages > .msg");
        if (cards.length <= n) return false;
        const last = cards[cards.length - 1];
        return /msg (turn|error|warn)/.test(last.className) && !document.getElementById("send").disabled;
      }, before, { timeout: 60000 });
      await page.waitForTimeout(150);
      const cards = await h.cards();
      return cards[cards.length - 1];
    },
  };
  try {
    await steps(h);
  } catch (e) {
    check(`${name}: scenario ran to completion`, false, e.stack);
    await h.shot("crash").catch(() => {});
  }
  check(`${name}: no missing vscode APIs / webview JS errors`, missing.length === 0, missing.join(" | "));
  fs.writeFileSync(path.join(OUT, `${name}-host.log`), hostLog.join(""));
  await vs.commands["awino.reconnect"] && null;
  try { ext.deactivate && (await ext.deactivate()); } catch (_) { /* ignore */ }
  await page.close();
}

function seedProject(workspace, pyBody) {
  // Write a project journal through the engine's own API (hash-chained),
  // exactly as an older extension version would have left it.
  const py = `
import sys; sys.path.insert(0, ${JSON.stringify(path.join(EXT_DIR, "bundled-sidecar"))})
from pathlib import Path
from loop import Loop
from backends import ScriptedBackend, ScriptedJudge
from state import default_home, project_slug
ws = ${JSON.stringify(workspace)}
loop = Loop(str(default_home(ws)), project_slug(ws), ScriptedBackend([]), ScriptedJudge())
${pyBody}
loop.state.persist_snapshot()
print("seeded", loop.state.snapshot["terminal"], loop.state.snapshot["terminal_reason"], loop.state.snapshot["turn_count"])
`;
  return execFileSync("python3", ["-c", py], { env: { ...process.env } }).toString().trim();
}

function mkWorkspace(label) {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), `awino-e2e-${label}-`));
  fs.writeFileSync(path.join(ws, "README.md"), "# e2e workspace\n");
  return ws;
}

// ------------------------------------------------------------ main
const asyncErrors = [];
process.on("unhandledRejection", (e) => { asyncErrors.push(String(e && e.stack || e)); console.log("UNHANDLED: " + (e && e.message)); });

(async () => {
  delete process.env.AWINO_HOME; // state lives in <workspace>/.awino like a real install
  delete process.env.AWINO_API_KEY; // the key must come from SecretStorage via the extension
  const gw = await startGateway();
  const stat = await startStatic();
  const browser = await chromium.launch();
  const endpoint = `http://127.0.0.1:${gw.port}/v1`;
  const base = { endpoint, stat, browser, key: "test-key" };
  try {
    // S1: fresh install, the everyday flow.
    await runScenario("s1-everyday", Object.assign({}, base, { workspace: mkWorkspace("s1"), inputs: ["testing", "manual"] }), async (h) => {
      await h.waitConnected();
      check("s1: extension activates and connects to the gateway", true);
      await h.shot("connected");
      let r = await h.send("what time is it");
      await h.shot("what-time");
      check("s1: 'what time is it' gets an answer", /It is /.test(r.text), r.text);
      check("s1: reply shows no raw harness header", !/\[A\.W\.I\.N\.O\. \|/.test(r.text), r.text);
      check("s1: reply is not the 'no clock' refusal", !/no clock/i.test(r.text), r.text);
      await h.vs.commands["awino.newMission"]();
      check("s1: New Mission asked for objective and criteria", h.vs.ui.inputPrompts.length >= 2, JSON.stringify(h.vs.ui.inputPrompts));
      r = await h.send("the export must be CSV");
      await h.shot("interview");
      check("s1: statement in DEFINE gets one interview question", /What outcome/.test(r.text), r.text);
      r = await h.send("what day is it?");
      check("s1: side question answered directly", /It is /.test(r.text), r.text);
      await h.page.click("#new-mission-btn");
      await h.page.waitForFunction(() => /New chat/.test(document.getElementById("messages").innerText), null, { timeout: 15000 });
      await h.shot("new-chat");
      const cards = await h.cards();
      check("s1: + clears the transcript to one New chat note", cards.length === 1, JSON.stringify(cards));
      check("s1: New chat note names the surviving mission", /Mission testing is still active/.test(cards[0] && cards[0].text), cards[0] && cards[0].text);
      r = await h.send("hi");
      check("s1: chat works after New chat", /msg turn/.test(r.cls) && !/has ended|Closed/.test(r.text), r.text);
      await h.shot("after-new-chat");
      const log = await gatewayLog(gw.port);
      check("s1: every gateway request carried the Bearer key from SecretStorage", log.length > 0 && log.every((q) => q.auth), JSON.stringify(log.filter((q) => !q.auth)));
    });

    // S2: the project 0.7.0 left behind: 'testing' ended by the idle clock.
    const ws2 = mkWorkspace("s2");
    console.log(seedProject(ws2, `loop.set_mission("testing", ["manual"])\nloop.state.record("budget_exhausted", {"reason": "max_seconds=3600"})`));
    await runScenario("s2-stuck-by-idle-clock", Object.assign({}, base, { workspace: ws2, inputs: [] }), async (h) => {
      await h.waitConnected();
      await h.page.waitForFunction(() => /Session resume/.test(document.getElementById("session-resume").innerText), null, { timeout: 15000 }).catch(() => {});
      const resume = await h.page.$eval("#session-resume", (e) => e.innerText);
      check("s2: session resume does not say the mission is terminal", !/terminal/i.test(resume), resume);
      const r = await h.send("yes new mission we are about to start");
      await h.shot("first-message");
      check("s2: a project stuck by the idle clock answers again", /msg turn/.test(r.cls) && !/has ended|Terminal state|Closed/.test(r.text), r.text);
    });

    // S3: a mission that really used its whole turn budget.
    const ws3 = mkWorkspace("s3");
    console.log(seedProject(ws3, `loop.set_mission("testing", ["manual"])\nfor i in range(50):\n    loop.state.record("turn_completed", {"turn_id": f"t{i}", "stalls": 0, "sig": None})\nloop.state.record("budget_exhausted", {"reason": "max_turns=50"})`));
    await runScenario("s3-mission-really-ended", Object.assign({}, base, { workspace: ws3, inputs: ["second mission", "manual"] }), async (h) => {
      await h.waitConnected();
      let r = await h.send("keep going");
      await h.shot("ended");
      check("s3: ended mission says so in plain words", /Mission "testing" has ended \(its 50-turn budget ran out\)/.test(r.text), r.text);
      const btn = await h.page.$("#messages > .msg:last-child .new-mission-action");
      check("s3: ended reply shows a Start a new mission button", !!btn, "no button");
      if (btn) await btn.click();
      await h.page.waitForTimeout(1500);
      check("s3: the button opens New Mission (objective, criteria)", h.vs.ui.inputPrompts.length >= 2, JSON.stringify(h.vs.ui.inputPrompts));
      r = await h.send("the export must be CSV");
      await h.shot("new-mission-works");
      check("s3: the new mission's first message gets a real reply", /What outcome/.test(r.text) && !/has ended/.test(r.text), r.text);
    });

    // S4: a per-turn pause (turn token budget) is NOT a mission ending.
    await runScenario("s4-turn-pause-is-not-an-ending", Object.assign({}, base, { workspace: mkWorkspace("s4"), inputs: ["testing", "manual"], settings: { "awino.turnTokenBudget": 1 } }), async (h) => {
      await h.waitConnected();
      await h.vs.commands["awino.newMission"]();
      const r = await h.send("please list the files in the workspace");
      await h.shot("paused");
      check("s4: tiny turn budget pauses the turn", /paused|budget/i.test(r.text), r.text);
      const btn = await h.page.$("#messages > .msg:last-child .new-mission-action");
      check("s4: a paused turn does not offer Start a new mission", !btn, r.text);
    });
  } finally {
    await browser.close();
    gw.proc.kill();
    stat.srv.close();
  }
  check("no unhandled promise rejections in the extension host", asyncErrors.length === 0, asyncErrors.join(" | "));
  const bad = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 2));
  console.log(`\n${results.length - bad.length}/${results.length} passed`);
  process.exit(bad.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
