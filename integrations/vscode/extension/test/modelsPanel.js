"use strict";
// test/modelsPanel.js — headless test for webview/models.js (Models & Providers
// panel): key labels + key-set indicators, model discovery fetch flow, and the
// save payload. Boots models.js against a minimal DOM shim.
function StubEl(tag) {
  this.tagName = tag;
  this.children = [];
  this._innerHTML = "";
  this._textContent = "";
  this.className = "";
  this.hidden = false;
  this.value = "";
  this.listeners = {};
}
StubEl.prototype.appendChild = function (c) { this.children.push(c); return c; };
Object.defineProperty(StubEl.prototype, "innerHTML", {
  get: function () { return this._innerHTML; },
  set: function (v) { this._innerHTML = String(v); this.children = []; }
});
Object.defineProperty(StubEl.prototype, "textContent", {
  get: function () { return this._textContent; },
  set: function (v) { this._textContent = String(v); }
});
StubEl.prototype.addEventListener = function (t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); };
StubEl.prototype.fire = function (t, e) { (this.listeners[t] || []).forEach(function (fn) { fn(e || {}); }); };
StubEl.prototype.focus = function () {};

let ids, messageListeners, posted;
function boot() {
  ids = {};
  ["provider", "endpoint", "bedrockRegion", "model", "modelSelect", "timeout",
   "openaiKey", "anthropicKey", "bedrockKey",
   "openaiKeyLabel", "anthropicKeyLabel", "bedrockKeyLabel",
   "openaiKeyState", "anthropicKeyState", "bedrockKeyState",
   "save", "clearKeys", "fetchModels", "fetchRow", "fetchNote",
   "status", "env", "switchEnv", "envResult"].forEach(function (id) {
    const e = new StubEl(id === "modelSelect" ? "select" : "div");
    e.id = id;
    if (id === "modelSelect" || id === "fetchRow") e.hidden = true; // as shipped in models.html
    ids[id] = e;
  });
  messageListeners = [];
  posted = [];
  global.window = {
    addEventListener: function (t, fn) { if (t === "message") messageListeners.push(fn); }
  };
  global.document = {
    getElementById: function (id) { return ids[id] || null; },
    createElement: function (tag) { return new StubEl(tag); }
  };
  global.acquireVsCodeApi = function () {
    return { postMessage: function (m) { posted.push(m); } };
  };
  delete require.cache[require.resolve("../webview/models.js")];
  require("../webview/models.js"); // boots the IIFE against the shim
}
function dispatch(m) { messageListeners.forEach(function (fn) { fn({ data: m }); }); }
function lastPosted(type) {
  for (let i = posted.length - 1; i >= 0; i--) if (posted[i].type === type) return posted[i];
  return null;
}

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log(`ok - ${name}`); }
  else { fail++; console.error(`FAIL - ${name}`); }
}

boot();
ok(posted.length === 1 && posted[0].type === "init", "panel posts init on load");

function stateMsg(over) {
  return Object.assign({
    type: "state",
    config: { provider: "openai", endpoint: "http://x:11434", model: "", timeout: 180, bedrockRegion: "" },
    binding: null,
    environments: [],
    openaiKeySet: true,
    anthropicKeySet: false,
    bedrockKeySet: true,
    keyLabels: { openai: "Work", bedrock: "Prod" },
    bedrockRegions: ["us-east-1"]
  }, over || {});
}

// 1. key labels render into the label inputs and the key-set indicators
dispatch(stateMsg());
ok(ids["openaiKeyLabel"].value === "Work", "openai label input filled from settings");
ok(ids["anthropicKeyLabel"].value === "", "anthropic label input empty when unset");
ok(ids["bedrockKeyLabel"].value === "Prod", "bedrock label input filled from settings");
ok(ids["openaiKeyState"].textContent === "key set (Work)", "indicator shows 'key set (Work)'");
ok(ids["openaiKeyState"].className === "key-stored", "indicator styled stored");
ok(ids["anthropicKeyState"].textContent === "not set", "indicator shows 'not set' without a key");
ok(ids["anthropicKeyState"].className === "key-unset", "indicator styled unset");
ok(ids["bedrockKeyState"].textContent === "key set (Prod)", "bedrock indicator shows its label");
// label is escaped: textContent assignment is inherently safe
dispatch(stateMsg({ keyLabels: { openai: "<img>" } }));
ok(ids["openaiKeyState"].textContent === "key set (<img>)", "label with markup rendered as text, not HTML");

// 2. fetch row visibility follows the provider
dispatch(stateMsg());
ok(ids["fetchRow"].hidden === false, "fetch row shown for openai");
ids["provider"].value = "bedrock";
ids["provider"].fire("change", {});
ok(ids["fetchRow"].hidden === true, "fetch row hidden for bedrock");
ok(ids["model"].hidden === false, "manual input restored when leaving fetchable provider");
ids["provider"].value = "ollama";
ids["provider"].fire("change", {});
ok(ids["fetchRow"].hidden === false, "fetch row shown for ollama");
ids["provider"].value = "openai";
ids["provider"].fire("change", {});

// 3. fetch click posts fetchModels with the typed endpoint + typed key
ids["endpoint"].value = "https://api.openai.com/v1";
ids["openaiKey"].value = "sk-typed";
const n0 = posted.length;
ids["fetchModels"].fire("click", {});
const fm = lastPosted("fetchModels");
ok(posted.length === n0 + 1 && fm && fm.provider === "openai" &&
   fm.endpoint === "https://api.openai.com/v1" && fm.key === "sk-typed",
  "fetch click posts provider/endpoint/typed key");

// 4. openai with no endpoint and no key: no network call, plain note
ids["endpoint"].value = "";
ids["openaiKey"].value = "";
const n1 = posted.length;
ids["fetchModels"].fire("click", {});
ok(posted.length === n1, "no fetchModels posted without endpoint or key");
ok(ids["fetchNote"].textContent.indexOf("Enter an endpoint and API key first") >= 0,
  "plain-language note when nothing to fetch with");

// 5. successful fetch populates the model dropdown
ids["model"].value = "";
dispatch({ type: "modelsFetched", ok: true, models: ["gpt-4", "gpt-3.5-turbo"], error: null });
ok(ids["modelSelect"].hidden === false, "dropdown shown after successful fetch");
ok(ids["model"].hidden === true, "text input hidden after successful fetch");
const optVals = ids["modelSelect"].children.map(function (o) { return o.value; });
ok(optVals.indexOf("gpt-4") >= 0 && optVals.indexOf("gpt-3.5-turbo") >= 0,
  "dropdown holds the fetched model ids");
ok(optVals[optVals.length - 1] === "__awino_manual__", "dropdown ends with a manual-entry option");
ok(ids["fetchNote"].textContent.indexOf("Found 2 models") >= 0, "fetch note reports the count");

// 6. current value is preserved when it is not in the fetched list
dispatch(stateMsg());
ids["model"].value = "my-custom";
dispatch({ type: "modelsFetched", ok: true, models: ["gpt-4"], error: null });
ok(ids["modelSelect"].value === "my-custom", "current value kept selectable after fetch");

// 7. picking the manual option restores the text input with the selection
ids["modelSelect"].value = "gpt-4";
ids["modelSelect"].fire("change", {});
ids["modelSelect"].value = "__awino_manual__";
ids["modelSelect"].fire("change", {});
ok(ids["model"].hidden === false && ids["modelSelect"].hidden === true,
  "manual option swaps back to the text input");
ok(ids["model"].value === "gpt-4", "text input keeps the last dropdown selection");

// 8. failed fetch keeps the manual input with a plain-language note (never blocks saving)
dispatch(stateMsg());
ids["model"].value = "typed-model";
dispatch({ type: "modelsFetched", ok: false, models: [], error: "couldn't reach x — is the server running?" });
ok(ids["model"].hidden === false && ids["modelSelect"].hidden === true,
  "manual input stays visible on fetch failure");
ok(ids["model"].value === "typed-model", "typed value untouched by the failure");
ok(ids["fetchNote"].textContent.indexOf("Couldn't fetch models: couldn't reach x") >= 0,
  "failure note shows the plain-language reason");
ok(ids["fetchNote"].className.indexOf("warn") >= 0, "failure note styled as a warning");

// 9. save sends labels and the model from the dropdown
dispatch(stateMsg());
ids["openaiKeyLabel"].value = "Work";
ids["anthropicKeyLabel"].value = "";
ids["bedrockKeyLabel"].value = "Prod";
dispatch({ type: "modelsFetched", ok: true, models: ["gpt-4", "gpt-3.5"], error: null });
ids["modelSelect"].value = "gpt-3.5";
ids["modelSelect"].fire("change", {});
ids["save"].fire("click", {});
const sv = lastPosted("save");
ok(sv && sv.openaiKeyLabel === "Work" && sv.anthropicKeyLabel === "" && sv.bedrockKeyLabel === "Prod",
  "save posts the key labels");
ok(sv && sv.model === "gpt-3.5", "save posts the dropdown model");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
