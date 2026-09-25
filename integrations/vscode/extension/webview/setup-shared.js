/* setup-shared.js — shared provider-setup knowledge for the Models &
 * Providers panel (models.js) and the chat onboarding wizard (chat.js).
 *
 * Plain script, no build step: defines window.AwinoSetup in a webview, or
 * exports it under node for the headless tests. It carries NO secrets —
 * only public documentation/key-creation URLs and estimated model pricing.
 *
 * Loaded via the {{SETUP_SHARED_JS}} placeholder before models.js / chat.js.
 * Both consumers access it lazily (setupMeta()) so a failed load degrades
 * instead of killing the whole webview.
 */
(function (root) {
  "use strict";

  // Provider catalogue. `keyUrl` is the key-creation page (Roo/Cline's
  // "Get {Provider} API Key" pattern); `docsUrl` feeds the "Provider Docs"
  // link. echo/ollama need no key and echo needs no docs.
  var PROVIDERS = [
    { id: "echo", label: "Echo (demo — local, no AI)", needsKey: false,
      docsUrl: null, keyUrl: null, keyName: null,
      blurb: "A local demo with canned replies. No AI is involved, nothing leaves your machine." },
    { id: "ollama", label: "Ollama (local, no key)", needsKey: false,
      docsUrl: "https://ollama.com/docs", keyUrl: null, keyName: null,
      blurb: "Runs models on your own machine via Ollama. Private, no API key needed." },
    { id: "openai", label: "OpenAI (api.openai.com)", needsKey: true,
      docsUrl: "https://platform.openai.com/docs",
      keyUrl: "https://platform.openai.com/api-keys", keyName: "OpenAI",
      defaultEndpoint: "https://api.openai.com/v1",
      blurb: "OpenAI's hosted API. Needs an API key." },
    { id: "openai-compatible", label: "OpenAI-compatible (custom endpoint)", needsKey: true,
      docsUrl: "https://platform.openai.com/docs",
      keyUrl: null, keyName: "API key",
      blurb: "Any endpoint that speaks the OpenAI chat API (LM Studio, vLLM, Together, etc.)." },
    { id: "anthropic", label: "Anthropic (console.anthropic.com)", needsKey: true,
      docsUrl: "https://docs.anthropic.com",
      keyUrl: "https://console.anthropic.com", keyName: "Anthropic",
      blurb: "Anthropic's hosted API. Needs an API key." },
    { id: "bedrock", label: "Bedrock (AWS Bedrock)", needsKey: true,
      docsUrl: "https://docs.aws.amazon.com/bedrock/",
      keyUrl: "https://console.aws.amazon.com/bedrock/", keyName: "Bedrock",
      blurb: "AWS Bedrock: Bedrock API key, or an AWS profile / SSO (SigV4 signing, no key)." },
  ];

  function providerById(id) {
    var k = String(id == null ? "" : id).toLowerCase();
    for (var i = 0; i < PROVIDERS.length; i++) {
      if (PROVIDERS[i].id === k) return PROVIDERS[i];
    }
    return null;
  }

  function needsKey(providerId) {
    var p = providerById(providerId);
    return p ? p.needsKey : false;
  }

  // Model discovery ("Fetch models") is offered for openai-compatible
  // (GET {endpoint}/models) and ollama (GET {endpoint}/api/tags).
  function fetchableProvider(providerId) {
    var k = String(providerId == null ? "" : providerId).toLowerCase();
    return k === "openai" || k === "ollama";
  }

  // Model intelligence (Cline pattern): context window + $/M in/out.
  // ALL values are estimates — pricing changes; the UI always labels them
  // "(est.)" and unknown models say so honestly instead of guessing.
  // `local: true` marks models that run on-device (Ollama): no per-token cost.
  var MODEL_INTEL = {
    "gpt-5":               { ctx: "400K", input: 1.25,  output: 10.00 },
    "gpt-5-mini":          { ctx: "400K", input: 0.25,  output: 2.00 },
    "gpt-4.1":             { ctx: "1M",   input: 2.00,  output: 8.00 },
    "gpt-4.1-mini":        { ctx: "1M",   input: 0.40,  output: 1.60 },
    "gpt-4.1-nano":        { ctx: "1M",   input: 0.10,  output: 0.40 },
    "gpt-4o":              { ctx: "128K", input: 2.50,  output: 10.00 },
    "gpt-4o-mini":         { ctx: "128K", input: 0.15,  output: 0.60 },
    "gpt-4-turbo":         { ctx: "128K", input: 10.00, output: 30.00 },
    "gpt-4":               { ctx: "8K",   input: 30.00, output: 60.00 },
    "gpt-3.5":             { ctx: "16K",  input: 0.50,  output: 1.50 },
    "o1":                  { ctx: "200K", input: 15.00, output: 60.00 },
    "o1-mini":             { ctx: "128K", input: 1.10,  output: 4.40 },
    "o3-mini":             { ctx: "200K", input: 1.10,  output: 4.40 },
    "claude-opus-4-1":     { ctx: "200K", input: 15.00, output: 75.00 },
    "claude-sonnet-4":     { ctx: "200K", input: 3.00,  output: 15.00 },
    "claude-3-5-sonnet":   { ctx: "200K", input: 3.00,  output: 15.00 },
    "claude-3-5-haiku":    { ctx: "200K", input: 0.80,  output: 4.00 },
    "claude-3-haiku":      { ctx: "200K", input: 0.25,  output: 1.25 },
    "llama3.1":            { ctx: "128K", local: true },
    "llama3":              { ctx: "8K",   local: true },
    "qwen2.5":             { ctx: "32K",  local: true },
    "mistral":             { ctx: "32K",  local: true },
    "codellama":           { ctx: "16K",  local: true },
  };

  // Case-insensitive match; also tries the id before any ":" tag so
  // "llama3.1:8b" still matches the "llama3.1" row.
  function modelIntel(modelId) {
    var raw = String(modelId == null ? "" : modelId).trim();
    if (!raw) return null;
    var k = raw.toLowerCase();
    if (MODEL_INTEL[k]) return MODEL_INTEL[k];
    var base = k.split(":")[0];
    if (base !== k && MODEL_INTEL[base]) return MODEL_INTEL[base];
    return null;
  }

  // Display line for under the model picker, or null when unknown (callers
  // render the honest "unknown" message themselves).
  function modelIntelLine(modelId) {
    var m = modelIntel(modelId);
    if (!m) return null;
    if (m.local) {
      return "Runs locally \u2014 no per-token cost \u00B7 context " + m.ctx + " (est.)";
    }
    return "Context " + m.ctx +
      " \u00B7 $" + m.input.toFixed(2) + "/M in \u00B7 $" + m.output.toFixed(2) + "/M out (est.)";
  }

  // MUST-FIX (0.4.0d): a <select> whose value matches no <option> renders
  // blank. The stored provider can be a legacy id (0.3.0 left "scripted"
  // behind) — never render blank; surface the real binding as a labeled
  // option instead.
  function ensureSelectedOption(select, value, label) {
    var v = value == null ? "" : String(value);
    select.value = v;
    if (select.value !== v && typeof document !== "undefined") {
      var o = document.createElement("option");
      o.value = v;
      o.textContent = label || (v + " (current)");
      if (select.firstChild) select.insertBefore(o, select.firstChild);
      else select.appendChild(o);
      select.value = v;
    }
    return select.value;
  }

  var api = {
    PROVIDERS: PROVIDERS,
    MODEL_INTEL: MODEL_INTEL,
    providerById: providerById,
    needsKey: needsKey,
    fetchableProvider: fetchableProvider,
    modelIntel: modelIntel,
    modelIntelLine: modelIntelLine,
    ensureSelectedOption: ensureSelectedOption,
  };
  root.AwinoSetup = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
