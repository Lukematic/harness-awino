"use strict";
// test/setupShared.js — unit tests for webview/setup-shared.js: the provider
// catalogue (docs/key URLs), needsKey/fetchableProvider, the model-intel
// table + display line, and ensureSelectedOption (the blank-dropdown fix).
const assert = require("assert");
const S = require("../webview/setup-shared.js");

let n = 0;
function t(name, fn) {
  n += 1;
  try {
    fn();
    console.log(`  ok ${n} - ${name}`);
  } catch (e) {
    console.error(`  FAIL ${n} - ${name}: ${e.message}`);
    process.exitCode = 1;
  }
}

// ---- provider catalogue ----
t("five providers, echo first", () => {
  assert.strictEqual(S.PROVIDERS.length, 5);
  assert.deepStrictEqual(S.PROVIDERS.map((p) => p.id),
    ["echo", "ollama", "openai", "anthropic", "bedrock"]);
});
t("key-creation URLs match the teardown's pages", () => {
  assert.strictEqual(S.providerById("openai").keyUrl, "https://platform.openai.com/api-keys");
  assert.strictEqual(S.providerById("anthropic").keyUrl, "https://console.anthropic.com");
  assert.strictEqual(S.providerById("bedrock").keyUrl, "https://console.aws.amazon.com/bedrock/");
  assert.strictEqual(S.providerById("ollama").keyUrl, null);
  assert.strictEqual(S.providerById("echo").keyUrl, null);
});
t("docs URLs present except echo", () => {
  assert.strictEqual(S.providerById("openai").docsUrl, "https://platform.openai.com/docs");
  assert.strictEqual(S.providerById("anthropic").docsUrl, "https://docs.anthropic.com");
  assert.ok(S.providerById("bedrock").docsUrl.indexOf("docs.aws.amazon.com/bedrock") >= 0);
  assert.ok(S.providerById("ollama").docsUrl.indexOf("ollama.com/docs") >= 0);
  assert.strictEqual(S.providerById("echo").docsUrl, null);
});
t("key-button labels derive from keyName", () => {
  assert.strictEqual(S.providerById("openai").keyName, "OpenAI");
  assert.strictEqual(S.providerById("anthropic").keyName, "Anthropic");
  assert.strictEqual(S.providerById("bedrock").keyName, "Bedrock");
});
t("providerById is case-insensitive, unknown -> null", () => {
  assert.strictEqual(S.providerById("OpenAI").id, "openai");
  assert.strictEqual(S.providerById("nope"), null);
});

// ---- needsKey / fetchableProvider ----
t("needsKey: echo/ollama/scripted false; openai/anthropic/bedrock true", () => {
  assert.strictEqual(S.needsKey("echo"), false);
  assert.strictEqual(S.needsKey("ollama"), false);
  assert.strictEqual(S.needsKey("openai"), true);
  assert.strictEqual(S.needsKey("anthropic"), true);
  assert.strictEqual(S.needsKey("bedrock"), true);
});
t("fetchableProvider: openai + ollama only", () => {
  assert.strictEqual(S.fetchableProvider("openai"), true);
  assert.strictEqual(S.fetchableProvider("ollama"), true);
  assert.strictEqual(S.fetchableProvider("bedrock"), false);
  assert.strictEqual(S.fetchableProvider("anthropic"), false);
  assert.strictEqual(S.fetchableProvider("echo"), false);
});

// ---- model intel ----
t("known model line shows context + $/M in/out marked as estimate", () => {
  const line = S.modelIntelLine("gpt-4o");
  assert.ok(line.indexOf("128K") >= 0, "context window");
  assert.ok(line.indexOf("$2.50/M") >= 0, "input price");
  assert.ok(line.indexOf("$10.00/M") >= 0, "output price");
  assert.ok(line.indexOf("(est.)") >= 0, "marked estimate");
});
t("lookup is case-insensitive", () => {
  assert.ok(S.modelIntelLine("GPT-4o").indexOf("128K") >= 0);
});
t("ollama tag suffix still matches (llama3.1:8b)", () => {
  const line = S.modelIntelLine("llama3.1:8b");
  assert.ok(line.indexOf("no per-token cost") >= 0, "local model");
  assert.ok(line.indexOf("128K") >= 0, "context");
  assert.ok(line.indexOf("(est.)") >= 0, "marked estimate");
});
t("unknown model -> null (caller renders the honest unknown message)", () => {
  assert.strictEqual(S.modelIntelLine("mystery-model-9000"), null);
  assert.strictEqual(S.modelIntelLine(""), null);
  assert.strictEqual(S.modelIntelLine(null), null);
});

// ---- ensureSelectedOption: the blank-dropdown fix (0.4.0d) ----
// Minimal select double with REAL select semantics: assigning a value with
// no matching option leaves the select blank (value "").
function FakeSelect(optionValues) {
  this._options = optionValues.map((v) => ({ value: v, textContent: v }));
  this._value = "";
  this.children = this._options.slice();
  const self = this;
  Object.defineProperty(this, "value", {
    get: function () { return self._value; },
    set: function (v) {
      self._value = self._options.some((o) => o.value === v) ? v : "";
    },
  });
  Object.defineProperty(this, "firstChild", {
    get: function () { return self.children[0] || null; },
  });
}
FakeSelect.prototype.appendChild = function (o) {
  this._options.push(o); this.children.push(o); return o;
};
FakeSelect.prototype.insertBefore = function (o) {
  this._options.unshift(o); this.children.unshift(o); return o;
};
// document stub for option creation
global.document = {
  createElement: function (tag) { return { tagName: tag, value: "", textContent: "" }; },
};

t("matching value selects without adding options", () => {
  const s = new FakeSelect(["echo", "openai"]);
  S.ensureSelectedOption(s, "openai");
  assert.strictEqual(s.value, "openai");
  assert.strictEqual(s.children.length, 2);
});
t("legacy value (scripted) becomes a labeled option instead of blank", () => {
  const s = new FakeSelect(["echo", "ollama", "openai", "anthropic", "bedrock"]);
  S.ensureSelectedOption(s, "scripted");
  assert.strictEqual(s.value, "scripted");
  assert.strictEqual(s.children.length, 6);
  assert.strictEqual(s.children[0].value, "scripted");
  assert.ok(s.children[0].textContent.indexOf("scripted") >= 0);
  assert.ok(s.children[0].textContent.indexOf("current") >= 0);
});
t("custom label is honored", () => {
  const s = new FakeSelect(["echo"]);
  S.ensureSelectedOption(s, "x", "x (legacy binding)");
  assert.strictEqual(s.children[0].textContent, "x (legacy binding)");
});
delete global.document;

console.log(`setupShared: ${n} tests`);
