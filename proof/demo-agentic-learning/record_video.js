// Replays timeline.json (real sidecar events from run_mission.py) into the
// real chat webview (webview/chat.html + chat.js) in Chromium and records it.
// Captions in gold mark the human's actions; everything else is the UI
// rendering sidecar events exactly as the extension would post them.
let chromium;
try { ({ chromium } = require("playwright")); }
catch (e) { ({ chromium } = require(require("child_process").execSync("npm root -g").toString().trim() + "/playwright")); }
const fs = require("fs");
const path = require("path");
const here = __dirname;
const ext = path.join(here, "../../integrations/vscode/extension/webview");
// Usage: node record_video.js [timeline.json] [outDir]
const tlPath = process.argv[2] ? path.resolve(process.argv[2]) : path.join(here, "timeline.json");
const out = process.argv[3] ? path.resolve(process.argv[3]) : path.join(here, "out");
fs.mkdirSync(out, { recursive: true });
const html = fs.readFileSync(path.join(ext, "chat.html"), "utf8")
  .replace("{{CSP_SOURCE}}", "file: 'unsafe-inline'")
  .replace("{{SETUP_SHARED_JS}}", "file://" + path.join(ext, "setup-shared.js"))
  .replace("{{CHAT_JS}}", "file://" + path.join(ext, "chat.js"));
fs.writeFileSync(path.join(out, "page.html"), html);
const tl = JSON.parse(fs.readFileSync(tlPath, "utf8"));
const meta = tl.find((x) => x.kind === "meta") || { live: false };
const badge = meta.live ? "LIVE MODEL: " + meta.provider + " · " + meta.model
                        : "SCRIPTED MODEL — harness and UI are real, the model's replies were written by hand";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const exe = process.env.PW_CHROMIUM || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
  const browser = await chromium.launch(fs.existsSync(exe) ? { executablePath: exe } : {});
  const ctx = await browser.newContext({ viewport: { width: 620, height: 1000 },
    recordVideo: { dir: out, size: { width: 620, height: 1000 } } });
  const page = await ctx.newPage();
  await page.addInitScript(() => { window.acquireVsCodeApi = () => ({ postMessage() {}, getState() {}, setState() {} }); });
  await page.goto("file://" + path.join(out, "page.html"));
  const post = (m) => page.evaluate((m) => window.postMessage(m, "*"), m);
  const caption = (t) => page.evaluate((t) => {
    let c = document.getElementById("demo-caption");
    if (!c) {
      c = document.createElement("div"); c.id = "demo-caption";
      c.style.cssText = "position:fixed;top:44px;left:50%;transform:translateX(-50%);z-index:9999;" +
        "background:#D4A937;color:#111A2E;font:600 13px system-ui;padding:6px 12px;border-radius:6px;" +
        "box-shadow:0 2px 10px rgba(0,0,0,.4);max-width:92%;text-align:center";
      document.body.appendChild(c);
    }
    c.textContent = t; c.style.display = t ? "block" : "none";
  }, t);
  await page.evaluate((b) => {
    const d = document.createElement("div");
    d.textContent = b;
    d.style.cssText = "position:fixed;bottom:0;left:0;right:0;z-index:10000;font:700 11px system-ui;" +
      "text-align:center;padding:3px;color:#111A2E;background:" + (b.startsWith("LIVE") ? "#4EC9B0" : "#F14C4C");
    document.body.appendChild(d);
  }, badge);
  const ready = tl.find((x) => x.kind === "event" && x.ev.event === "ready").ev;
  let status = { mission: null, phase: "IDLE" };
  await post({ type: "state", connected: true, ready, status, keyMissing: false, provider: "scripted",
    model: "scripted model", showWizard: false, modes: [], activeMode: "interview" });
  await caption("Mission: agentic learning — a 7-approach literature review" + (meta.live ? " (live)" : " (scripted model)"));
  await sleep(2000);
  let shot = 0;
  for (const x of tl) {
    if (x.kind === "user") {
      await caption("You type:");
      await page.fill("#input", "");
      await page.type("#input", x.text, { delay: 18 });
      await sleep(400);
      await page.click("#send");
      await caption("");
      await sleep(700);
    } else if (x.kind === "approve") {
      await caption("You click Approve");
      const btns = await page.$$('button[data-act="approve"]');
      if (btns.length) { await btns[btns.length - 1].scrollIntoViewIfNeeded(); await btns[btns.length - 1].click().catch(() => {}); }
      await sleep(1300);
      await caption("");
    } else if (x.kind === "command") {
      await caption("You: " + x.label);
      await sleep(1800);
      await caption("");
    } else if (x.kind === "stories") {
      await post({ type: "sessionResume", summary: { mission: "Literature review of 7 agentic learning approaches",
        phase: "SHIP", turns: 6, stories: x.ledger } });
      await caption("Story closed: on the brag board; the benchmark idea is parked for later");
      await page.evaluate(() => window.scrollTo(0, 0));
      await sleep(3500);
    } else if (x.kind === "status") {
      status = x.status;
      await post({ type: "state", connected: true, ready, status, keyMissing: false, provider: "scripted",
        model: "scripted model", showWizard: false, modes: [], activeMode: "interview" });
    } else if (x.kind === "event") {
      const ev = x.ev;
      if (ev.event === "ready") continue;
      if (ev.event === "approval_requested" && !(ev.approvals || []).length) continue;
      await post({ type: "event", payload: ev });
      if (ev.event === "said_delta" || ev.event === "thinking_delta") { await sleep(8); continue; }
      await sleep(ev.event === "turn_result" ? 2600 : 1200);
      if (ev.event === "turn_result") {
        shot++;
        await page.screenshot({ path: path.join(out, "step-" + String(shot).padStart(2, "0") + ".png") });
      }
    }
  }
  await sleep(1500);
  await page.screenshot({ path: path.join(out, "final.png") });
  await caption("");
  await page.click("#theme-toggle");
  await sleep(800);
  await page.screenshot({ path: path.join(out, "final-savanna.png") });
  const video = page.video();
  await ctx.close();
  const vp = await video.path();
  fs.renameSync(vp, path.join(out, "agentic-learning-run.webm"));
  await browser.close();
  console.log("video:", path.join(out, "agentic-learning-run.webm"));
})();
