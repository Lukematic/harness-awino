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

const ids = {};
["messages", "input", "send", "stop", "statusline", "banner", "jump-latest"].forEach(function (id) {
  const e = new StubEl("div");
  e.id = id;
  if (id === "jump-latest") e.hidden = true; // chat.html ships it hidden
  ids[id] = e;
});
const messageListeners = [];
global.window = {
  addEventListener: function (t, fn) { if (t === "message") messageListeners.push(fn); }
};
global.document = {
  getElementById: function (id) { return ids[id] || null; },
  createElement: function (tag) { return new StubEl(tag); },
  createTextNode: function (t) { return { nodeType: 3, textContent: String(t), parentNode: null }; }
};
const posted = [];
global.acquireVsCodeApi = function () {
  return { postMessage: function (m) { posted.push(m); } };
};

require("../webview/chat.js"); // boots the IIFE against the shim

const messages = ids["messages"];
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
ids["stop"].fire("click", {});
ok(posted.length === 1 && posted[0].type === "stop", "stop button posts {type:stop}");

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
