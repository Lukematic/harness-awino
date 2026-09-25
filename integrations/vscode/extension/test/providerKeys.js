// providerKeys.js — unit tests for src/providerKeys.ts: which provider needs
// an API key from SecretStorage, and whether that key is missing.
const assert = require("assert");
const { keyMissingForProvider } = require("../out/providerKeys.js");

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

const NONE = { openai: false, anthropic: false, bedrock: false };
const OPENAI = { openai: true, anthropic: false, bedrock: false };
const ANTHROPIC = { openai: false, anthropic: true, bedrock: false };
const BEDROCK = { openai: false, anthropic: false, bedrock: true };
const ALL = { openai: true, anthropic: true, bedrock: true };

// echo / ollama never need a key
t("echo needs no key even when nothing is stored", () => {
  assert.strictEqual(keyMissingForProvider("echo", NONE), false);
});
t("ollama needs no key even when nothing is stored", () => {
  assert.strictEqual(keyMissingForProvider("ollama", NONE), false);
});
t("echo still needs no key when keys are stored", () => {
  assert.strictEqual(keyMissingForProvider("echo", ALL), false);
});

// scripted (TEST ONLY canned turns) needs no key — claiming otherwise would
// show a lying setup card / onboarding wizard in GUI-test sessions
t("scripted needs no key even when nothing is stored", () => {
  assert.strictEqual(keyMissingForProvider("scripted", NONE), false);
});
t("scripted still needs no key when keys are stored", () => {
  assert.strictEqual(keyMissingForProvider("scripted", ALL), false);
});

// bedrock needs the Bedrock key and nothing else satisfies it —
// UNLESS profile (SigV4) auth is selected, which needs no key at all
t("bedrock missing when no bedrock key stored", () => {
  assert.strictEqual(keyMissingForProvider("bedrock", NONE), true);
});
t("bedrock satisfied only by the bedrock key", () => {
  assert.strictEqual(keyMissingForProvider("bedrock", BEDROCK), false);
  assert.strictEqual(keyMissingForProvider("bedrock", OPENAI), true);
  assert.strictEqual(keyMissingForProvider("bedrock", ANTHROPIC), true);
});
t("bedrock profile auth never reports a missing key", () => {
  assert.strictEqual(keyMissingForProvider("bedrock", NONE, { bedrockProfileAuth: true }), false);
  assert.strictEqual(keyMissingForProvider("bedrock", ALL, { bedrockProfileAuth: true }), false);
});
t("profile-auth opt does not leak to other providers", () => {
  assert.strictEqual(keyMissingForProvider("openai", NONE, { bedrockProfileAuth: true }), true);
});

// anthropic needs the Anthropic key and nothing else satisfies it
t("anthropic missing when no anthropic key stored", () => {
  assert.strictEqual(keyMissingForProvider("anthropic", NONE), true);
});
t("anthropic satisfied only by the anthropic key", () => {
  assert.strictEqual(keyMissingForProvider("anthropic", ANTHROPIC), false);
  assert.strictEqual(keyMissingForProvider("anthropic", OPENAI), true);
  assert.strictEqual(keyMissingForProvider("anthropic", BEDROCK), true);
});

// openai rides AWINO_API_KEY
t("openai missing when no openai key stored", () => {
  assert.strictEqual(keyMissingForProvider("openai", NONE), true);
});
t("openai satisfied only by the openai key", () => {
  assert.strictEqual(keyMissingForProvider("openai", OPENAI), false);
  assert.strictEqual(keyMissingForProvider("openai", ANTHROPIC), true);
  assert.strictEqual(keyMissingForProvider("openai", BEDROCK), true);
});

// unknown / custom OpenAI-compatible providers fall back to AWINO_API_KEY
t("unknown provider falls back to the openai key", () => {
  assert.strictEqual(keyMissingForProvider("custom-endpoint", NONE), true);
  assert.strictEqual(keyMissingForProvider("custom-endpoint", OPENAI), false);
});
t("empty provider falls back to the openai key", () => {
  assert.strictEqual(keyMissingForProvider("", NONE), true);
  assert.strictEqual(keyMissingForProvider("", OPENAI), false);
});
t("provider match is case-insensitive", () => {
  assert.strictEqual(keyMissingForProvider("Echo", NONE), false);
  assert.strictEqual(keyMissingForProvider("BEDROCK", BEDROCK), false);
});

console.log(`providerKeys: ${n} tests`);
