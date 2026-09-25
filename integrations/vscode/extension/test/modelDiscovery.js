"use strict";
// test/modelDiscovery.js — unit + live-localhost tests for src/modelDiscovery.ts
// (spec: Models & Providers "Fetch models" for 0.4.0).
const http = require("http");
const {
  modelsListUrl,
  parseOpenAiModels,
  parseOllamaTags,
  discoverModels,
} = require("../out/modelDiscovery.js");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log(`ok - ${name}`); }
  else { fail++; console.error(`FAIL - ${name}`); }
}
function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

// ---------- modelsListUrl ----------
ok(modelsListUrl("openai", "https://api.openai.com/v1") === "https://api.openai.com/v1/models",
  "openai: /v1 base -> /v1/models");
ok(modelsListUrl("openai", "https://api.openai.com/v1/") === "https://api.openai.com/v1/models",
  "openai: trailing slash stripped");
ok(modelsListUrl("openai", "http://localhost:11434") === "http://localhost:11434/v1/models",
  "openai: bare base gains /v1/models (sidecar semantics)");
ok(modelsListUrl("openai", "") === "http://localhost:11434/v1/models",
  "openai: empty endpoint falls back to local default");
ok(modelsListUrl("openai", undefined) === "http://localhost:11434/v1/models",
  "openai: undefined endpoint falls back to local default");
ok(modelsListUrl("OpenAI", "https://x/v1") === "https://x/v1/models",
  "openai: provider match is case-insensitive");
ok(modelsListUrl("ollama", "") === "http://localhost:11434/api/tags",
  "ollama: default -> /api/tags");
ok(modelsListUrl("ollama", "http://pi:11434/") === "http://pi:11434/api/tags",
  "ollama: custom host -> /api/tags");
ok(modelsListUrl("bedrock", "https://x") === null, "bedrock: no listable endpoint");
ok(modelsListUrl("anthropic", "https://x") === null, "anthropic: no listable endpoint");
ok(modelsListUrl("echo", "") === null, "echo: no listable endpoint");
ok(modelsListUrl("openai", "not-a-url") === null, "openai: non-http base rejected");
ok(modelsListUrl("openai", "ftp://x") === null, "openai: non-http(s) scheme rejected");

// ---------- parseOpenAiModels ----------
ok(eq(parseOpenAiModels({ data: [{ id: "a" }, { id: "b" }, { id: "a" }, { id: " " }, { id: 5 }, {}] }), ["a", "b"]),
  "openai parse: ids extracted, deduped, blanks/non-strings dropped");
ok(eq(parseOpenAiModels({}), []), "openai parse: missing data -> []");
ok(eq(parseOpenAiModels(null), []), "openai parse: null -> []");
ok(eq(parseOpenAiModels({ data: "nope" }), []), "openai parse: non-array data -> []");
const many = [];
for (let i = 0; i < 250; i++) many.push({ id: "m" + i });
ok(parseOpenAiModels({ data: many }).length === 200, "openai parse: capped at 200 ids");

// ---------- parseOllamaTags ----------
ok(eq(parseOllamaTags({ models: [{ name: "qwen2.5:1.5b" }, { name: "llama3" }] }), ["qwen2.5:1.5b", "llama3"]),
  "ollama parse: names extracted");
ok(eq(parseOllamaTags({}), []), "ollama parse: missing models -> []");
ok(eq(parseOllamaTags({ models: [] }), []), "ollama parse: empty list -> []");

// ---------- live localhost server ----------
function startServer(handler) {
  return new Promise((resolve) => {
    const seen = {};
    const srv = http.createServer((req, res) => handler(req, res, seen));
    srv.listen(0, "127.0.0.1", () => resolve({ srv, port: srv.address().port, seen }));
  });
}
function json(res, status, obj) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(obj));
}
function stop(srv) { return new Promise((r) => srv.close(r)); }

async function live() {
  // 1. openai discovery sends the bearer token and parses ids
  {
    const { srv, port, seen } = await startServer((req, res, seen) => {
      seen.url = req.url;
      seen.auth = req.headers.authorization;
      json(res, 200, { data: [{ id: "gpt-4" }, { id: "gpt-3.5-turbo" }] });
    });
    const r = await discoverModels("openai", `http://127.0.0.1:${port}`, "test-key");
    ok(r.ok && eq(r.models, ["gpt-4", "gpt-3.5-turbo"]), "openai discovery: models parsed");
    ok(seen.url === "/v1/models", "openai discovery: hit /v1/models");
    ok(seen.auth === "Bearer test-key", "openai discovery: key sent as bearer token");
    await stop(srv);
  }
  // 2. ollama discovery hits /api/tags without a key
  {
    const { srv, port, seen } = await startServer((req, res, seen) => {
      seen.url = req.url;
      seen.auth = req.headers.authorization;
      json(res, 200, { models: [{ name: "qwen2.5:1.5b" }] });
    });
    const r = await discoverModels("ollama", `http://127.0.0.1:${port}`);
    ok(r.ok && eq(r.models, ["qwen2.5:1.5b"]), "ollama discovery: names parsed");
    ok(seen.url === "/api/tags", "ollama discovery: hit /api/tags");
    ok(!seen.auth, "ollama discovery: no auth header without a key");
    await stop(srv);
  }
  // 3. 401 -> plain-language key hint
  {
    const { srv, port } = await startServer((req, res) => json(res, 401, { error: "bad key" }));
    const r = await discoverModels("openai", `http://127.0.0.1:${port}/v1`, "wrong");
    ok(!r.ok && r.error.indexOf("API key") >= 0, "401: plain-language key hint, ok:false");
    await stop(srv);
  }
  // 4. non-JSON body
  {
    const { srv, port } = await startServer((req, res) => {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end("<html>nope</html>");
    });
    const r = await discoverModels("openai", `http://127.0.0.1:${port}/v1`);
    ok(!r.ok && r.error.indexOf("JSON") >= 0, "non-JSON: plain-language error, ok:false");
    await stop(srv);
  }
  // 5. empty list -> ok:false, manual input stays
  {
    const { srv, port } = await startServer((req, res) => json(res, 200, { data: [] }));
    const r = await discoverModels("openai", `http://127.0.0.1:${port}/v1`);
    ok(!r.ok && r.error.indexOf("no models") >= 0, "empty list: ok:false with no-models note");
    await stop(srv);
  }
  // 6. connection refused -> plain-language reachability note
  {
    const r = await discoverModels("openai", "http://127.0.0.1:1/v1");
    ok(!r.ok && r.error.indexOf("couldn't reach") >= 0, "refused: plain-language reachability note");
  }
  // 7. bedrock never touches the network
  {
    const r = await discoverModels("bedrock", "http://127.0.0.1:9");
    ok(!r.ok && r.error.indexOf("isn't available") >= 0, "bedrock: no fetch attempted");
  }
}

live().then(() => {
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}).catch((e) => {
  console.error("harness error:", e);
  process.exit(1);
});
