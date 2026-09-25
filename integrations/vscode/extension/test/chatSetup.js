"use strict";
// test/chatSetup.js — headless tests for the 0.4.0 chat-webview additions:
// the provider-status pill (Kilo's "No providers" pattern) and the
// first-run onboarding wizard (choose provider -> key/get-key -> model/fetch
// -> Done). Feeds "state"/"wizardModels" messages through a minimal DOM shim
// and asserts pill text, wizard visibility, provider/docs/get-key behavior,
// model discovery wiring, inline validation, and the save/dismiss messages.
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
  this.listeners = {};
  this.parentNode = null;
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
StubEl.prototype.addEventListener = function (t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); };
StubEl.prototype.fire = function (t, e) { (this.listeners[t] || []).forEach(function (fn) { fn(e || {}); }); };
StubEl.prototype.setAttribute = function (k, v) { this[k] = v; };
StubEl.prototype.getAttribute = function (k) { return this[k]; };
StubEl.prototype.focus = function () {};
// every element gets a classList (banner/pill/theme code uses it)
const _stubElInit = StubEl;
StubEl = function (tag) { _stubElInit.call(this, tag); this.classList = new StubClassList(this); };
StubEl.prototype = _stubElInit.prototype;
// select with real semantics: a value with no matching option leaves it blank
function SelectStub() {
  StubEl.call(this, "select");
  const self = this;
  this._options = [];
  this._value = "";
  Object.defineProperty(this, "value", {
    get: function () { return self._value; },
    set: function (v) {
      self._value = self._options.some(function (o) { return o.value === v; }) ? v : "";
    }
  });
  Object.defineProperty(this, "firstChild", {
    get: function () { return self.children[0] || null; }
  });
}
SelectStub.prototype = Object.create(StubEl.prototype);
SelectStub.prototype.appendChild = function (o) {
  this._options.push(o); this.children.push(o); return o;
};
SelectStub.prototype.insertBefore = function (o) {
  this._options.unshift(o); this.children.unshift(o); return o;
};
// minimal classList synced into className
function StubClassList(el) { this._el = el; this._set = {}; }
StubClassList.prototype._sync = function () {
  const self = this;
  this._el.className = Object.keys(this._set).filter(function (k) { return self._set[k]; }).join(" ");
};
StubClassList.prototype.add = function (c) { this._set[c] = true; this._sync(); };
StubClassList.prototype.remove = function (c) { delete this._set[c]; this._sync(); };
StubClassList.prototype.contains = function (c) { return !!this._set[c]; };
StubClassList.prototype.toggle = function (c, force) {
  const on = force === undefined ? !this.contains(c) : !!force;
  if (on) this._set[c] = true; else delete this._set[c];
  this._sync();
  return on;
};

let ids, messageListeners, posted, bodyEl;
function boot() {
  // shared setup knowledge first, while `window` is undefined, so it
  // attaches to globalThis and bare `AwinoSetup` resolves inside chat.js
  delete require.cache[require.resolve("../webview/setup-shared.js")];
  require("../webview/setup-shared.js");
  ids = {};
  ["messages", "input", "send", "stop", "statusline", "banner", "jump-latest",
   "theme-toggle", "theme-name", "models-btn", "setup-card", "setup-sub",
   "setup-btn", "provider-pill", "inputbar", "wizard",
   "mission-header", "session-resume",
   "conn-dot", "mode-select", "new-mission-btn", "echo-banner",
   "w-docs", "w-keystep", "w-key", "w-keylabel", "w-getkey",
   "w-model", "w-modelselect", "w-intel", "w-endpoint",
   "w-regionrow", "w-region", "w-fetchrow", "w-fetch", "w-fetchnote",
   "w-done", "w-skip", "w-error", "w-blurb",
   "w-bedrockauthrow", "w-bedrockprofilerow", "w-bedrockprofile",
   "w-keyrow", "w-getkeyrow",
   "w-provestep", "w-prove", "w-provenote"].forEach(function (id) {
    ids[id] = new StubEl("div");
    ids[id].id = id;
  });
  // bedrock auth select carries the two real options (as shipped in chat.html)
  ids["w-bedrockauth"] = new SelectStub();
  ids["w-bedrockauth"].id = "w-bedrockauth";
  ["api-key", "aws-profile"].forEach(function (v) {
    const o = new StubEl("option"); o.value = v; o.textContent = v;
    ids["w-bedrockauth"].appendChild(o);
  });
  ids["wizard"].hidden = true;
  ids["w-provider"] = new SelectStub();
  ids["w-provider"].id = "w-provider";
  ids["mode-select"] = new SelectStub();
  ids["mode-select"].id = "mode-select";
  messageListeners = [];
  posted = [];
  bodyEl = new StubEl("body");
  bodyEl.classList = new StubClassList(bodyEl);
  global.window = {
    addEventListener: function (t, fn) { if (t === "message") messageListeners.push(fn); }
  };
  global.document = {
    body: bodyEl,
    getElementById: function (id) { return ids[id] || null; },
    createElement: function (tag) {
      return tag === "select" ? new SelectStub() : new StubEl(tag);
    },
    createTextNode: function (t) { return { nodeType: 3, textContent: String(t), parentNode: null }; }
  };
  global.acquireVsCodeApi = function () {
    return {
      postMessage: function (m) { posted.push(m); },
      getState: function () { return {}; },
      setState: function () {}
    };
  };
  delete require.cache[require.resolve("../webview/chat.js")];
  require("../webview/chat.js");
}
boot();
function state(over) {
  const m = Object.assign({
    type: "state", connected: false, ready: null, status: null,
    keyMissing: false, provider: "echo", model: "",
    showWizard: false, bedrockRegions: ["us-east-1"]
  }, over || {});
  messageListeners.forEach(function (fn) { fn({ data: m }); });
}
function lastPosted(type) {
  for (let i = posted.length - 1; i >= 0; i--) {
    if (posted[i].type === type) return posted[i];
  }
  return null;
}

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

// 1. provider pill: "No provider" when the key is missing
state({ keyMissing: true, provider: "openai" });
ok(ids["provider-pill"].textContent === "No provider", "pill reads 'No provider' when the key is missing");
ok(ids["provider-pill"].classList.contains("none"), "pill styled as 'none' with no provider");
// 2. provider pill: "provider · model" when connected
state({
  keyMissing: false, connected: true, provider: "openai", model: "gpt-4o",
  ready: { binding: { provider: "openai", model: "gpt-4o" } }, status: { mission: null }
});
ok(ids["provider-pill"].textContent === "openai · gpt-4o", "pill reads 'provider · model' when connected");
ok(!ids["provider-pill"].classList.contains("none"), "pill not styled 'none' when connected");
// 3. pill click opens Models & Providers (same as the gear button)
posted = [];
ids["provider-pill"].fire("click", {});
ok(lastPosted("models") !== null, "pill click posts 'models' (opens Models & Providers)");

// 4. wizard shows on first-run state, disables (never hides) the input bar, hides setup card
state({ showWizard: true, provider: "echo", keyMissing: false });
ok(ids["wizard"].hidden === false, "wizard visible when showWizard is true");
// Spec 1.3: the input bar is NEVER hidden — it stays in the DOM and is
// only disabled (with a state placeholder) while the wizard is active.
ok(ids["inputbar"].hidden === false, "input bar never hidden while the wizard is active (Spec 1.3)");
ok(ids["input"].disabled === true, "input disabled while the wizard is active (Spec 1.3)");
ok(ids["setup-card"].hidden === true, "setup card suppressed while the wizard owns setup");
ok(ids["w-provider"].children.length === 6, "wizard provider select populated from the catalogue (Spec 2.1: echo, ollama, openai, openai-compatible, anthropic, bedrock)");
// 5. wizard hides again; input bar re-enables (never hidden)
state({ showWizard: false, connected: true });
ok(ids["wizard"].hidden === true, "wizard hidden when showWizard is false");
ok(ids["inputbar"].hidden === false, "input bar stays in the DOM after the wizard (Spec 1.3)");
ok(ids["input"].disabled === false, "input enabled after the wizard (Spec 1.3)");
// 6. wizard never renders the provider select blank (0.4.0d in the wizard too)
state({ showWizard: true, provider: "scripted" });
ok(ids["w-provider"].value === "scripted", "wizard provider select shows the binding, never blank");
// 7. provider change drives docs link, key step, get-key button (openai)
state({ showWizard: true, provider: "echo" });
ids["w-provider"].value = "openai";
ids["w-provider"].fire("change", {});
ok(ids["w-docs"].hidden === false, "docs link visible for openai");
ids["w-docs"].fire("click", { preventDefault: function () {} });
ok(lastPosted("openExternal") && lastPosted("openExternal").url === "https://platform.openai.com/docs",
  "wizard docs link opens the openai docs");
ok(ids["w-keystep"].hidden === false, "key step shown for a keyed provider");
ok(ids["w-getkey"].hidden === false, "get-key button shown for a keyed provider");
ok(ids["w-getkey"].textContent.indexOf("OpenAI") >= 0, "get-key button names the provider");
posted = [];
ids["w-getkey"].fire("click", {});
ok(lastPosted("openExternal") && lastPosted("openExternal").url === "https://platform.openai.com/api-keys",
  "wizard get-key button opens the key-creation page");
// 8. echo hides the key step and docs link
ids["w-provider"].value = "echo";
ids["w-provider"].fire("change", {});
ok(ids["w-keystep"].hidden === true, "key step hidden for echo");
ok(ids["w-docs"].hidden === true, "docs link hidden for echo");
ok(ids["w-getkey"].hidden === true, "get-key button hidden for echo");
// 9. wizard model intel line (0.4.0e in the wizard too)
ids["w-model"].value = "gpt-4o";
ids["w-model"].fire("input", {});
ok(ids["w-intel"].textContent.indexOf("128K") >= 0, "wizard intel shows context for a known model");
ok(ids["w-intel"].textContent.indexOf("(est.)") >= 0, "wizard intel marked as an estimate");
// 10. model fetch posts wizardFetch; results populate the dropdown
ids["w-provider"].value = "ollama";
ids["w-provider"].fire("change", {});
posted = [];
ids["w-fetch"].fire("click", {});
const wf = lastPosted("wizardFetch");
ok(wf && wf.provider === "ollama", "fetch posts wizardFetch for the chosen provider");
messageListeners.forEach(function (fn) {
  fn({ data: { type: "wizardModels", ok: true, models: ["llama3.1:8b", "qwen2.5:7b"] } });
});
ok(ids["w-modelselect"].hidden === false, "fetch results open the model dropdown");
ok(ids["w-fetchnote"].textContent.indexOf("Found 2 models") >= 0, "fetch note reports the count");
// 11. fetch failure keeps manual entry with the plain-language reason
messageListeners.forEach(function (fn) {
  fn({ data: { type: "wizardModels", ok: false, error: "connection refused" } });
});
ok(ids["w-model"].hidden === false, "manual model input returns on fetch failure");
ok(ids["w-fetchnote"].textContent.indexOf("connection refused") >= 0, "failure note shows the reason");
// 12. Done validates inline: keyed provider with no key is refused, not silent
state({ showWizard: true, provider: "echo" });
ids["w-provider"].value = "openai";
ids["w-provider"].fire("change", {});
ids["w-key"].value = "";
posted = [];
ids["w-done"].fire("click", {});
ok(ids["w-error"].hidden === false, "inline error shown when the key is missing");
ok(ids["w-error"].textContent.indexOf("API key") >= 0, "error names the missing API key");
ok(lastPosted("wizardSave") === null, "no wizardSave posted without the required key");
// 13. Done with a key posts wizardSave (key travels to SecretStorage via host)
ids["w-key"].value = "sk-test";
ids["w-keylabel"].value = "work";
posted = [];
ids["w-done"].fire("click", {});
const ws = lastPosted("wizardSave");
ok(ws && ws.provider === "openai" && ws.key === "sk-test" && ws.keyLabel === "work",
  "wizardSave carries provider, key, and label");
// 13b. Done shows a busy state while the host stores the key (no dead click)
ok(ids["w-done"].disabled === true, "Save & Connect disabled while the key is being stored");
ok(ids["w-done"].textContent === "Saving…", "Save & Connect shows Saving… while storing");
// 13c. wizardSaveFailed surfaces the storage error inline and re-enables Save
messageListeners.forEach(function (fn) { fn({ data: { type: "wizardSaveFailed", error: "keyring boom" } }); });
ok(ids["w-done"].disabled === false, "Save & Connect re-enabled after the save fails");
ok(ids["w-done"].textContent === "Save & Connect", "button label restored after the save fails");
ok(ids["w-error"].hidden === false, "inline error shown when the key could not be stored");
ok(ids["w-error"].textContent.indexOf("keyring boom") >= 0, "error names the storage failure");
// 13d. wizardProveReady resets the button for the prove-it step
ids["w-done"].fire("click", {});
ok(ids["w-done"].disabled === true, "busy again on re-save");
messageListeners.forEach(function (fn) { fn({ data: { type: "wizardProveReady" } }); });
ok(ids["w-done"].disabled === false, "Save & Connect re-enabled when the prove-it step appears");
ok(ids["w-provestep"].hidden === false, "prove-it step shown on wizardProveReady");
// 14. keyless path needs no key: echo Done posts wizardSave immediately
ids["w-provider"].value = "echo";
ids["w-provider"].fire("change", {});
posted = [];
ids["w-done"].fire("click", {});
ok(lastPosted("wizardSave") && lastPosted("wizardSave").provider === "echo",
  "echo Done posts wizardSave with no key required");
// 15. Skip posts wizardDismiss
posted = [];
ids["w-skip"].fire("click", {});
ok(lastPosted("wizardDismiss") !== null, "skip posts wizardDismiss");
// 16. mission header renders mission, phase, verified x/y, revision
state({
  connected: true,
  status: {
    mission: "Ship the tasks panel",
    phase: "build",
    mission_revision: 3,
    criteria: [
      { ok: true, label: "panel renders" },
      { ok: true, label: "tests green" },
      { ok: false, label: "docs updated" }
    ]
  }
});
ok(ids["mission-header"].hidden === false, "mission header visible when a mission is active");
ok(ids["mission-header"].innerHTML.indexOf("BUILD") >= 0, "mission header shows the phase");
ok(ids["mission-header"].innerHTML.indexOf("Ship the tasks panel") >= 0, "mission header shows the mission");
ok(ids["mission-header"].innerHTML.indexOf("2/3 done") >= 0, "mission header shows verified done-criteria x/y");
ok(ids["mission-header"].innerHTML.indexOf("rev 3") >= 0, "mission header shows the mission revision");
// 17. mission header hides when there is no mission
state({ connected: true, status: { mission: null } });
ok(ids["mission-header"].hidden === true, "mission header hidden with no mission");
// 18. sessionResume renders the read-only session-focus summary
messageListeners.forEach(function (fn) {
  fn({ data: {
    type: "sessionResume",
    summary: {
      mission: "Ship the tasks panel", phase: "build", mission_revision: 3,
      criteria_total: 3, criteria_verified: 2,
      verified_labels: ["panel renders", "tests green"],
      last_progress: ["tasks_list query wired"],
      next_action: "write the docs",
      last_stop_point: "after wiring tasks_list",
      recent_milestones: [{ kind: "learn", text: "suite needs TMPDIR" }],
      turns: 12
    }
  } });
});
ok(ids["session-resume"].hidden === false, "session resume visible for an active mission");
ok(ids["session-resume"].innerHTML.indexOf("Ship the tasks panel") >= 0, "resume shows the mission");
ok(ids["session-resume"].innerHTML.indexOf("2/3 done criteria") >= 0, "resume shows verified criteria x/y");
ok(ids["session-resume"].innerHTML.indexOf("write the docs") >= 0, "resume shows the next expected action");
ok(ids["session-resume"].innerHTML.indexOf("Stopped at") >= 0, "resume shows the last stop point");
// 19. sessionResume stays hidden with no mission and no turns
messageListeners.forEach(function (fn) {
  fn({ data: { type: "sessionResume", summary: { mission: null, turns: 0 } } });
});
ok(ids["session-resume"].hidden === true, "session resume hidden with no mission and no turns");

// 20. Spec 1.4: connection dot reflects connected state
state({ connected: true, showWizard: false });
ok(ids["conn-dot"].classList.contains("on"), "conn dot on when connected (Spec 1.4)");
ok(ids["input"].disabled === false, "input enabled when connected (Spec 1.3)");
state({ connected: false, showWizard: false });
ok(!ids["conn-dot"].classList.contains("on"), "conn dot off when disconnected (Spec 1.4)");
ok(ids["input"].disabled === true, "input disabled when disconnected (Spec 1.3)");
ok(ids["input"].placeholder.indexOf("not connected") >= 0, "input placeholder names the disconnected state (Spec 1.3)");

// 21. Spec 1.4: mode selector populates from the live mode list
state({ connected: true, showWizard: false,
  modes: [{ id: "architect", label: "Architect" }, { id: "debug", label: "Debug" }],
  activeMode: "debug" });
ok(ids["mode-select"].hidden === false, "mode selector visible with modes (Spec 1.4)");
ok(ids["mode-select"].children.length === 2, "mode selector has one option per mode (Spec 1.4)");
ok(ids["mode-select"].value === "debug" || (function () {
  // SelectStub may track selection differently; accept any debug selection
  var found = false;
  ids["mode-select"].children.forEach(function (o) { if (o.value === "debug" && o.selected) found = true; });
  return found;
})(), "mode selector marks the active mode (Spec 1.4)");

// 22. Spec 2.3 + 4.4: echo pill reads "Echo (demo)", model truncated, echo banner shows
state({ connected: true, showWizard: false,
  ready: { binding: { provider: "echo", model: "echo-1" } } });
ok(ids["provider-pill"].textContent.indexOf("Echo (demo)") === 0, "pill reads 'Echo (demo)', never bare 'Echo' (Spec 2.3)");
ok(ids["echo-banner"].classList.contains("show"), "echo banner shown for the echo provider (Spec 2.3)");
ok(ids["echo-banner"].innerHTML.indexOf("local demo") >= 0, "echo banner names the no-op demo truthfully (Spec 2.3)");
state({ connected: true, showWizard: false,
  ready: { binding: { provider: "openai", model: "gpt-4-turbo-preview-0125-extra-long" } } });
ok(ids["provider-pill"].textContent.indexOf("…") >= 0, "pill truncates long model names to 24 chars (Spec 4.4)");
ok(!ids["echo-banner"].classList.contains("show"), "echo banner hidden for real providers (Spec 2.3)");

// 23. Spec 4.3: bindingChanged re-renders the pill from the authoritative binding
state({ connected: true, showWizard: false,
  ready: { binding: { provider: "openai", model: "gpt-4o" } } });
messageListeners.forEach(function (fn) {
  fn({ data: { type: "bindingChanged",
    binding: { provider: "anthropic", model: "claude-4" }, settingsDirty: true } });
});
ok(ids["provider-pill"].textContent.indexOf("anthropic") >= 0, "bindingChanged updates the pill (Spec 4.3)");
ok(ids["provider-pill"].textContent.indexOf("(stale)") >= 0, "bindingChanged marks the pill stale when dirty (Spec 4.4)");

// 24. Spec 1.4: new-mission button posts newMission
posted = [];
ids["new-mission-btn"].fire("click", {});
ok(lastPosted("newMission") !== null, "new-mission button posts 'newMission' (Spec 1.4)");

// 25. Spec 1.4: mode selector change posts invokeModeSelect
posted = [];
ids["mode-select"].fire("change", {});
var modePost = lastPosted("invokeModeSelect");
ok(modePost !== null, "mode selector change posts 'invokeModeSelect' (Spec 1.4)");

// 26. Spec 2.1 Step 3: prove-it step appears on wizardProveReady
state({ showWizard: true, provider: "openai", keyMissing: false });
messageListeners.forEach(function (fn) {
  fn({ data: { type: "wizardProveReady" } });
});
ok(ids["w-provestep"].hidden === false, "prove-it step shown after save & connect (Spec 2.1)");

// 27. Spec 2.1: prove button posts wizardProve
posted = [];
ids["w-prove"].fire("click", {});
ok(lastPosted("wizardProve") !== null, "prove button posts 'wizardProve' (Spec 2.1)");
ok(ids["w-prove"].disabled === true, "prove button disabled while sending (Spec 2.1)");

// 28. Spec 2.1: wizardProved confirms completion
messageListeners.forEach(function (fn) {
  fn({ data: { type: "wizardProved" } });
});
ok(ids["w-provenote"].textContent.indexOf("complete") >= 0, "wizardProved confirms setup complete (Spec 2.1)");

// 29. Spec 2.1: wizardProveFailed shows the error and re-enables retry
messageListeners.forEach(function (fn) {
  fn({ data: { type: "wizardProveFailed", error: "bad key" } });
});
ok(ids["w-prove"].disabled === false, "prove button re-enabled after failure (Spec 2.1)");
ok(ids["w-error"].hidden === false, "prove failure shows the error inline (Spec 2.1)");

// 30. Spec 2.1: provider blurb is truthful (echo = demo)
state({ showWizard: false });
state({ showWizard: true, provider: "echo", keyMissing: false });
ok(ids["w-blurb"].textContent.indexOf("demo") >= 0, "echo blurb names the demo truthfully (Spec 2.1/2.3)");

// 31. Bedrock wizard: AWS profile / SSO is a real keyless auth path
state({ showWizard: true, provider: "echo" });
ids["w-provider"].value = "bedrock";
ids["w-provider"].fire("change", {});
ok(ids["w-bedrockauthrow"].hidden === false, "bedrock auth select shown for bedrock");
ok(ids["w-bedrockprofilerow"].hidden === true, "profile row hidden in api-key mode");
ids["w-bedrockauth"].value = "aws-profile";
ids["w-bedrockauth"].fire("change", {});
ok(ids["w-bedrockprofilerow"].hidden === false, "profile row shown in aws-profile mode");
ok(ids["w-keyrow"].hidden === true, "key inputs hidden in aws-profile mode");
// empty profile name is refused inline
ids["w-bedrockprofile"].value = "";
posted = [];
ids["w-done"].fire("click", {});
ok(ids["w-error"].hidden === false, "inline error when the profile name is empty");
ok(lastPosted("wizardSave") === null, "no wizardSave posted without a profile name");
// profile name + no key posts wizardSave with aws-profile mode
ids["w-bedrockprofile"].value = "sso";
ids["w-key"].value = "";
posted = [];
ids["w-done"].fire("click", {});
const pws = lastPosted("wizardSave");
ok(pws && pws.provider === "bedrock" && pws.bedrockAuthMode === "aws-profile" &&
  pws.bedrockAwsProfile === "sso" && pws.key === "",
  "wizardSave carries aws-profile mode + profile name, no key required");
// bedrock blurb no longer claims profiles are unsupported
ok(ids["w-blurb"].textContent.indexOf("SSO") >= 0, "bedrock blurb mentions profile/SSO support");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
