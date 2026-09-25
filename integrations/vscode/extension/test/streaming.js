"use strict";
// test/streaming.js — headless state-machine test for the chat.js streaming
// renderers (spec §8 step 4). Feeds a scripted event sequence through a
// minimal DOM shim and asserts card structure, collapse behavior, caps.

function StubEl(tag) {
  this.tagName = tag;
  this.children = [];
  this._innerHTML = "";
  this._textContent = "";
  this.className = "";
  this.title = "";
  this.hidden = false;
  this.disabled = false;
  this.value = "";
  this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 100;
  this.listeners = {};
  this.parentNode = null;
  this.open = false;
}
StubEl.prototype.appendChild = function (c) { c.parentNode = this; this.children.push(c); return c; };
Object.defineProperty(StubEl.prototype, "innerHTML", {
  get: function () { return this._innerHTML; },
  set: function (v) { this._innerHTML = String(v); this.children = []; }
});
Object.defineProperty(StubEl.prototype, "textContent", {
  get: function () { return this._textContent; },
  set: function (v) { this._textContent = String(v); this.children = []; }
});
Object.defineProperty(StubEl.prototype, "firstChild", {
  get: function () { return this.children[0] || null; }
});
StubEl.prototype.addEventListener = function (t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); };
StubEl.prototype.fire = function (t, e) { (this.listeners[t] || []).forEach(function (fn) { fn(e || {}); }); };
StubEl.prototype.setAttribute = function (k, v) { this[k] = v; };
StubEl.prototype.getAttribute = function (k) { return this[k]; };
StubEl.prototype.querySelectorAll = function () { return []; };

// Minimal classList (add/remove/contains/toggle), synced into className.
function StubClassList(el) { this._el = el; this._set = {}; }
StubClassList.prototype._sync = function () {
  this._el.className = Object.keys(this._set).filter(function (k) { return this._set[k]; }, this).join(" ");
};
StubClassList.prototype.add = function () {
  for (let i = 0; i < arguments.length; i++) this._set[arguments[i]] = true;
  this._sync();
};
StubClassList.prototype.remove = function () {
  for (let i = 0; i < arguments.length; i++) delete this._set[arguments[i]];
  this._sync();
};
StubClassList.prototype.contains = function (c) { return !!this._set[c]; };
StubClassList.prototype.toggle = function (c) {
  if (this.contains(c)) this.remove(c); else this.add(c);
  return this.contains(c);
};
const _stubElInit = StubEl;
StubEl = function (tag) { _stubElInit.call(this, tag); this.classList = new StubClassList(this); };
StubEl.prototype = _stubElInit.prototype;

let ids, messageListeners, posted, messages, bodyEl, webviewState;
function boot(bodyClasses, initialState) {
  ids = {};
  ["messages", "input", "send", "stop", "statusline", "banner", "jump-latest",
   "theme-toggle", "theme-name", "models-btn", "setup-card", "setup-sub",
   "setup-btn"].forEach(function (id) {
    const e = new StubEl("div");
    e.id = id;
    if (id === "jump-latest") e.hidden = true; // chat.html ships it hidden
    if (id === "setup-card") e.hidden = true; // chat.html ships it hidden
    ids[id] = e;
  });
  messageListeners = [];
  posted = [];
  webviewState = Object.assign({}, initialState || {});
  bodyEl = new StubEl("body");
  (bodyClasses || []).forEach(function (c) { bodyEl.classList.add(c); });
  global.window = {
    addEventListener: function (t, fn) { if (t === "message") messageListeners.push(fn); }
  };
  global.document = {
    body: bodyEl,
    getElementById: function (id) { return ids[id] || null; },
    createElement: function (tag) { return new StubEl(tag); },
    createTextNode: function (t) { return { nodeType: 3, textContent: String(t), parentNode: null }; }
  };
  global.acquireVsCodeApi = function () {
    return {
      postMessage: function (m) { posted.push(m); },
      getState: function () { return webviewState; },
      // NB: VS Code copies state on setState; snapshot first so a caller
      // passing the live getState() object (as chat.js does) isn't wiped.
      setState: function (s) {
        const snap = Object.assign({}, s);
        Object.keys(webviewState).forEach(function (k) { delete webviewState[k]; });
        Object.assign(webviewState, snap);
      }
    };
  };
  delete require.cache[require.resolve("../webview/chat.js")];
  require("../webview/chat.js"); // boots the IIFE against the shim
  messages = ids["messages"];
}
boot([]);
function fire(payload) {
  messageListeners.forEach(function (fn) { fn({ data: { type: "event", payload: payload } }); });
}
function byClass(root, cls) {
  const out = [];
  (function walk(n) {
    if (n.className && (" " + n.className + " ").indexOf(" " + cls + " ") >= 0) out.push(n);
    (n.children || []).forEach(walk);
  })(root);
  return out;
}
function subtreeHtml(n) {
  let s = n.innerHTML || "";
  (n.children || []).forEach(function (c) { s += subtreeHtml(c); });
  return s;
}

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

// 1. turn_start builds the streaming card shell
// 0. ready handshake: the webview announces itself once its message
// listener is live, so the host can (re)deliver transcript + state.
ok(posted.length === 1 && posted[0].type === "chatReady", "boot posts one chatReady handshake");
fire({ event: "turn_start", turn_id: "t7", phase: "BUILD", mode: { id: "build", source: "stage" }, persona: null });
ok(messages.children.length === 1, "turn_start creates one card");
const card = messages.children[0];
ok(card.className === "msg turn", "card has msg turn class");
const thinking = byClass(card, "thinking")[0];
ok(thinking && thinking.open === true, "thinking section open while streaming");
ok(byClass(card, "thinking-body")[0].textContent === "", "thinking body starts empty");
ok(byClass(card, "said").length === 1, "body element exists");
ok(byClass(card, "tools-live").length === 1, "tools container exists");
ok(byClass(card, "checks").length === 1, "checks container exists");
ok(subtreeHtml(card).indexOf("BUILD") >= 0, "phase chip rendered from turn_start");

// 2. thinking deltas accumulate with the 8000-char cap
fire({ event: "thinking_delta", turn_id: "t7", text: "first thought. " });
fire({ event: "thinking_delta", turn_id: "t7", text: "second thought." });
let tb = byClass(card, "thinking-body")[0];
ok(tb.textContent === "first thought. second thought.", "thinking accumulates across deltas");
fire({ event: "thinking_delta", turn_id: "t7", text: new Array(9000).join("x") });
tb = byClass(card, "thinking-body")[0];
ok(tb.textContent.length <= 8000 + 20, "thinking capped at 8000 chars");
ok(tb.textContent.indexOf("[truncated]") >= 0, "truncation marker present");

// 3. said deltas re-render markdown incrementally
fire({ event: "said_delta", turn_id: "t7", text: "Hello **world**" });
const body = byClass(card, "said")[0];
ok(body.innerHTML.indexOf("<strong>world</strong>") >= 0, "markdown rendered in stream");
ok(body.innerHTML.indexOf("streaming-caret") >= 0, "streaming caret shown while streaming");
fire({ event: "said_delta", turn_id: "t7", text: " and `code`." });
ok(body.innerHTML.indexOf("<code>code</code>") >= 0, "body grows incrementally");

// 4. tool progress rows upsert start -> done
fire({ event: "tool_progress", turn_id: "t7", tool: "write_file", phase: "start", summary: "notes.txt" });
let rows = byClass(card, "tool-row");
ok(rows.length === 1, "tool row created on start");
ok(rows[0].children[0].className === "bead working", "bead pulsing while running");
fire({ event: "tool_progress", turn_id: "t7", tool: "write_file", phase: "done", summary: "notes.txt (42 bytes)", ms: 12 });
rows = byClass(card, "tool-row");
ok(rows.length === 1, "tool row upserted, not duplicated");
ok(rows[0].children[0].className === "bead done", "bead done after finish");
ok(rows[0].children[3].textContent === "12 ms", "elapsed ms shown");

// 5. harness checks stream into their feed
fire({ event: "harness_check", turn_id: "t7", check: "contract", verdict: "pass", detail: "phase BUILD" });
fire({ event: "harness_check", turn_id: "t7", check: "judge:architect", verdict: "warn", detail: "no test for empty input" });
const crows = byClass(card, "check-row");
ok(crows.length === 2, "two check rows appended");
ok(crows[0].innerHTML.indexOf("pass") >= 0 && crows[1].innerHTML.indexOf("warn") >= 0, "verdict pills rendered");

// 6. turn_result finalizes the stream in place
fire({
  event: "turn_result", turn_id: "t7",
  result: {
    said: "Hello **world** and `code`.", thinking: null,
    checks: [
      { check: "contract", verdict: "pass", detail: "phase BUILD" },
      { check: "judge:architect", verdict: "warn", detail: "no test for empty input" }
    ],
    phase: "BUILD", status: "ok"
  }
});
ok(messages.children.length === 1, "turn_result finalizes in place (no new card)");
ok(byClass(card, "thinking")[0].open === false, "thinking collapsed after turn_result");
ok(byClass(card, "thinking-status")[0].textContent.indexOf("chars)") >= 0, "thinking summary shows char count");
ok(byClass(card, "checks")[0].open === true, "checks stay open when a warn exists");
ok(body.innerHTML.indexOf("<strong>world</strong>") >= 0, "final markdown render kept");
ok(body.innerHTML.indexOf("streaming-caret") < 0, "streaming caret removed on finalize");

// 7. honest-null thinking (provider exposes no thinking)
fire({ event: "turn_start", turn_id: "t8", phase: "PLAN", mode: { id: "plan", source: "stage" }, persona: null });
fire({ event: "said_delta", turn_id: "t8", text: "plan text" });
fire({ event: "turn_result", turn_id: "t8", result: { said: "plan text", thinking: null, checks: [], phase: "PLAN", status: "ok" } });
const card8 = messages.children[1];
ok(byClass(card8, "thinking-status")[0].textContent.indexOf("not exposed by this provider") >= 0,
  "honest null thinking label rendered");
ok(byClass(card8, "checks")[0].open === false, "checks collapse when all pass");

// 8. deltas for an unknown turn_id create a card without throwing
let threw = false;
try { fire({ event: "said_delta", turn_id: "tX", text: "orphan" }); } catch (e) { threw = true; }
ok(!threw && messages.children.length === 3, "unknown turn_id creates card, no throw");

// 9. jump-to-latest pill on unpinned scroll
fire({ event: "turn_start", turn_id: "t10", phase: "BUILD", mode: { id: "build", source: "stage" }, persona: null });
const jump = ids["jump-latest"];
messages.scrollHeight = 2000; messages.scrollTop = 100; messages.clientHeight = 200;
messages.fire("scroll", {});
fire({ event: "said_delta", turn_id: "t10", text: "streaming while unpinned" });
ok(jump.hidden === false, "jump pill appears when unpinned and content arrives");
jump.fire("click", {});
ok(jump.hidden === true, "jump pill hides after click (re-pinned)");
fire({ event: "turn_result", turn_id: "t10", result: { said: "done", thinking: null, checks: [], status: "ok" } });

// 10. stop mid-stream freezes the card with the cancelled line
fire({ event: "turn_start", turn_id: "t9", phase: "BUILD", mode: { id: "build", source: "stage" }, persona: null });
const card9 = messages.children[messages.children.length - 1]; // capture before cancel_ack appends its warn card
fire({ event: "said_delta", turn_id: "t9", text: "partial" });
fire({ event: "cancel_ack", note: "user stopped" });
ok(byClass(card9, "cancelled-note").length === 1, "cancelled line appended to in-flight card");
ok(byClass(card9, "thinking")[0].open === false, "thinking collapsed on cancel");

// 11. legacy turn_result (no turn_id, no open streams) renders a fresh card
const before = messages.children.length;
fire({
  event: "turn_result",
  result: { said: "legacy **body**", status: "ok", results: [{ tool: "run_command", result: "ok" }] }
});
ok(messages.children.length === before + 1, "legacy turn_result renders a new card");
const legacy = messages.children[messages.children.length - 1];
ok(subtreeHtml(legacy).indexOf("<strong>body</strong>") >= 0, "legacy body rendered as markdown");
ok(subtreeHtml(legacy).indexOf("<table") >= 0, "legacy tool table fallback present");

// 12. stop posts the stop verb
const postedBeforeStop = posted.length;
ids["stop"].fire("click", {});
ok(posted.length === postedBeforeStop + 1 && posted[posted.length - 1].type === "stop", "stop button posts {type:stop}");

// 13. theme variants (refinement 2026-09-23): vibranium default, toggle, savanna, persistence
ok(bodyEl.classList.contains("awino-vibranium"), "default theme is vibranium when no vscode-light class");
ok(!bodyEl.classList.contains("awino-savanna"), "savanna not applied by default in dark");
ok(ids["theme-name"].textContent === "Vibranium", "toggle label shows the current variant");
ids["theme-toggle"].fire("click");
ok(bodyEl.classList.contains("awino-savanna"), "toggle switches body to savanna");
ok(!bodyEl.classList.contains("awino-vibranium"), "vibranium removed after toggle to savanna");
ok(ids["theme-name"].textContent === "Savanna", "toggle label updates to Savanna");
ok(webviewState.awinoTheme === "savanna", "manual choice persisted in webview state");
ids["theme-toggle"].fire("click");
ok(bodyEl.classList.contains("awino-vibranium"), "toggle switches back to vibranium");
ok(webviewState.awinoTheme === "vibranium", "state updated on second toggle");

// 14. light VS Code theme defaults to savanna; saved override wins
boot(["vscode-light"]);
ok(bodyEl.classList.contains("awino-savanna"), "vscode-light defaults to savanna");
ok(!bodyEl.classList.contains("awino-vibranium"), "vibranium not applied for light default");
ok(ids["theme-name"].textContent === "Savanna", "label follows savanna default");
boot(["vscode-light"], { awinoTheme: "vibranium" });
ok(bodyEl.classList.contains("awino-vibranium"), "saved manual override wins over vscode-light default");
ok(ids["theme-name"].textContent === "Vibranium", "label follows saved override");

// 15. state with connectError: failure detail in statusline + banner (spawn fix)
function fireState(m) {
  messageListeners.forEach(function (fn) { fn({ data: m }); });
}
function slText() { return byClass(ids["statusline"], "sl-text")[0]; }
fireState({ type: "state", connected: false, connectError: 'spawn "py" ENOENT — tried: py <oops>' });
ok(slText().textContent === "not connected — sidecar failed to start", "statusline shows failure, not bare not-connected");
ok(byClass(ids["statusline"], "alert").length === 1, "link bead goes alert on connect failure");
ok(ids["banner"].classList.contains("show"), "banner shown for connect failure");
ok(ids["banner"].innerHTML.indexOf("Sidecar failed to start.") >= 0, "banner titles the failure");
ok(ids["banner"].innerHTML.indexOf("spawn \"py\" ENOENT") >= 0, "banner shows the OS error + interpreter tried");
ok(ids["banner"].innerHTML.indexOf("&lt;oops&gt;") >= 0, "connect error HTML-escaped in banner");

// 16. plain disconnect (no error) keeps old behavior; later states clear the banner
fireState({ type: "state", connected: false });
ok(slText().textContent === "not connected", "plain disconnect keeps bare not-connected text");
ok(byClass(ids["statusline"], "alert").length === 0, "no alert bead on plain disconnect");
ok(!ids["banner"].classList.contains("show"), "banner hidden again on plain disconnect");
fireState({ type: "state", connected: true, status: {} });
ok(ids["banner"].classList.contains("show") && ids["banner"].innerHTML.indexOf("No active mission.") >= 0, "no-mission banner on connect");
fireState({ type: "state", connected: true, status: { mission: {} } });
ok(!ids["banner"].classList.contains("show"), "banner cleared once a mission exists");

// 17. setup card: keyMissing shows "No model connected" + provider copy
fireState({ type: "state", connected: true, keyMissing: true, provider: "openai", status: { mission: {} } });
ok(ids["setup-card"].hidden === false, "setup card shown when provider key is missing");
ok(ids["setup-sub"].textContent.indexOf('"openai"') >= 0, "setup copy names the provider");
ok(ids["setup-sub"].textContent.indexOf("SecretStorage") >= 0, "setup copy says keys are not in settings JSON");

// 18. setup card hides once the key exists
fireState({ type: "state", connected: true, keyMissing: false, provider: "openai", status: { mission: {} } });
ok(ids["setup-card"].hidden === true, "setup card hidden when key is present");

// 19. header gear posts {type:"models"}
const postedBefore = posted.length;
ids["models-btn"].fire("click", {});
ok(posted.length === postedBefore + 1 && posted[posted.length - 1].type === "models", "gear button posts {type:models}");

// 20. setup-card button posts {type:"models"}
fireState({ type: "state", connected: true, keyMissing: true, provider: "bedrock", status: { mission: {} } });
const postedBefore2 = posted.length;
ids["setup-btn"].fire("click", {});
ok(posted.length === postedBefore2 + 1 && posted[postedBefore2].type === "models", "setup button posts {type:models}");

// 21. setup card shows on a never-connected keyless state (bedrock early-return path)
fireState({ type: "state", connected: false, keyMissing: true, provider: "bedrock" });
ok(ids["setup-card"].hidden === false, "setup card shown even when never connected");
ok(ids["setup-sub"].textContent.indexOf('"bedrock"') >= 0, "setup copy names bedrock");

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
