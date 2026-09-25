const { chromium } = require("/opt/node22/lib/node_modules/playwright");
const fs = require("fs");
const dir = "/tmp/claude-0/ui";
const SAID = [
  "[A.W.I.N.O. | phase: IDLE | mode: observe | stance: advisor | skills:  | loop: 6 | run: 9f171654f475 | knowledge: 0/0 | mission: none]",
  "STANCE -> advisor (default)",
  "[Certain] Listing the root directory to inspect project structure while asking the user for mission details.",
  "Questions: What specific objective or task would you like to set as the mission for this project?",
  "Assuming: [Likely] Project context and mission information can be discovered from the root directory.",
  "Floor: IDLE | Next action: answer: What specific objective or task would you like to set as the mission for this project? | Blocked on: user answers.",
].join("\n");
const ready = { event: "ready", project: "agentic-seedlings", provider: "openai", model: "gemini-3.8-flash-project",
  binding: { provider: "openai", model: "gemini-3.8-flash-project", environment: "(global)" }, mcp: [] };
async function shoot(variant) {
  let html = fs.readFileSync(`${dir}/chat-${variant}.html`, "utf8")
    .replace("{{CSP_SOURCE}}", "file: 'unsafe-inline'")
    .replace("{{SETUP_SHARED_JS}}", "setup-shared.js").replace("{{CHAT_JS}}", `chat-${variant}.js`);
  fs.writeFileSync(`${dir}/page-${variant}.html`, html);
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const page = await browser.newPage({ viewport: { width: 560, height: 1000 } });
  await page.addInitScript(() => { window.acquireVsCodeApi = () => ({ postMessage() {}, getState() {}, setState() {} }); });
  await page.goto(`file://${dir}/page-${variant}.html`);
  const send = (m) => page.evaluate((m) => window.postMessage(m, "*"), m);
  const state = { type: "state", connected: true, ready, status: { mission: null, phase: "IDLE" }, keyMissing: false,
    provider: "openai", model: "gemini-3.8-flash-project", showWizard: false, modes: [], activeMode: "interview" };
  for (let i = 0; i < 4; i++) await send(state);            // the extension re-sends state on every view resolve
  await send({ type: "sessionResume", summary: { mission: null, turns: 6, phase: "IDLE",
    stories: { stories: [
      { id: "a", title: "Health endpoint", status: "doing" },
      { id: "b", title: "Docs refresh", status: "open", ready_to_close: true },
      { id: "c", title: "Deep health checks", status: "parked", revisit_due: true }] } } });
  await send({ type: "event", payload: { event: "turn_result", turn_id: "t6", result: {
    status: "ok", phase: "IDLE", mode: "interview", stance: "advisor", said: SAID,
    results: [{ tool: "list_dir", result: { path: ".awino", entries: ["project.yaml", "projects", "registry", "seeds"] } }] } } });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${dir}/${variant}.png`, fullPage: false });
  await browser.close();
}
(async () => { await shoot("main"); await shoot("branch"); console.log("ok"); })();
