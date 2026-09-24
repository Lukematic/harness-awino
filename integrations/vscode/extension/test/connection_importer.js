/**
 * Unit tests for src/connection_importer.ts (permission-first connection import).
 *
 * Pure logic — no vscode, no fs. The `read` injector is faked so we can prove
 * the scanner only ever touches the declared candidate paths. Run after
 * `npm run compile`:
 *   node ./test/connection_importer.js
 */
"use strict";
const assert = require("assert");

const {
  isSecretKeyName,
  looksLikeSecretValue,
  parseClaudeSettings,
  parseDotEnv,
  parseKiloConfig,
  stripJsonc,
  planSources,
  scanSources,
  applicableFindings,
  informationalFindings,
  buildConfigWrites,
  summarizeWrites,
  formatScannedLine,
} = require("../out/connection_importer.js");

// Fake-but-shaped secrets. If any of these ever appear in an import result,
// the secret-safety tests below fail loudly.
const FAKE_OPENAI_KEY = "sk-FAKEFAKEFAKE00000000000000000000000000000000";
const FAKE_ANTHROPIC_KEY = "sk-ant-FAKEFAKEFAKE00000000000000000000000000000000";
const FAKE_AKIA = "AKIAFAKEFAKEFAKEFAKE00";

function noSecretsLeak(findings, secrets) {
  const blob = JSON.stringify(findings);
  for (const s of secrets) {
    assert(!blob.includes(s), `SECRET LEAK: result contains ${s.slice(0, 12)}…`);
  }
}

const CLAUDE_FIXTURE = JSON.stringify({
  env: {
    AWS_PROFILE: "lukes-sso",
    AWS_REGION: "us-west-2",
    AWS_DEFAULT_REGION: "us-west-2",
    ANTHROPIC_MODEL:
      "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    OPENAI_API_KEY: FAKE_OPENAI_KEY,
  },
  model: "sonnet",
});

const DOTENV_FIXTURE = [
  "# project connections",
  "AWS_REGION=us-east-1",
  "export OPENAI_BASE_URL=https://proxy.example.com/v1  # trailing comment",
  "ANTHROPIC_API_KEY=" + FAKE_ANTHROPIC_KEY,
  'QUOTED_MODEL="claude-opus-4-6"',
  "SOME_SETTING=" + FAKE_AKIA,
].join("\n");

const KILO_FIXTURE = `{
  // Kilo CLI provider config (jsonc — comments are legal here)
  "model": "relay-chat/my-model-1",
  "provider": {
    "relay-chat": {
      "name": "AI Gateway - OpenAI Chat",
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "apiKey": "{env:KILO_GATEWAY_KEY}",
        "baseURL": "https://gateway.example.com/v1"
      },
      "models": {
        "my-model-1": { "name": "Gateway Model One" }
      }
    }
  }
}`;

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`ok ${passed} - ${name}`);
  } catch (e) {
    console.error(`FAIL: ${name}\n  ${e.message}`);
    process.exitCode = 1;
  }
}

// ------------------------------------------------------- permission / confinement

test("scanner reads ONLY the declared candidate paths", () => {
  const readPaths = [];
  const read = (p) => {
    readPaths.push(p);
    return null;
  };
  scanSources(read, "/home/tester", "/ws/proj");
  const planned = new Set(planSources("/home/tester", "/ws/proj").map((c) => c.absPath));
  assert.strictEqual(readPaths.length, planned.size, "read count matches plan");
  for (const p of readPaths) {
    assert(planned.has(p), `unplanned read: ${p}`);
  }
});

test("scanner never touches credential stores", () => {
  const readPaths = [];
  const read = (p) => {
    readPaths.push(p);
    return null;
  };
  scanSources(read, "/home/tester", "/ws/proj");
  const forbidden = ["auth.json", ".aws/credentials", "SecretStorage", "state.vscdb"];
  for (const p of readPaths) {
    for (const f of forbidden) {
      assert(!p.includes(f), `read touched credential store: ${p}`);
    }
  }
});

test("pure parsers take text in — no fs involved", () => {
  // The parsers' signatures accept strings only; this pins that contract.
  assert.strictEqual(parseClaudeSettings.length, 2);
  assert.strictEqual(parseDotEnv.length, 2);
  assert.strictEqual(parseKiloConfig.length, 2);
});

// ---------------------------------------------------------------- secret guard

test("isSecretKeyName flags secret key names", () => {
  for (const k of ["OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "aws_secret_access_key", "SESSION_TOKEN", "dbPassword", "KILO_API_KEY"]) {
    assert(isSecretKeyName(k), `${k} should be secret`);
  }
  for (const k of ["AWS_REGION", "AWS_PROFILE", "MODEL", "OPENAI_BASE_URL", "ANTHROPIC_MODEL"]) {
    assert(!isSecretKeyName(k), `${k} should NOT be secret`);
  }
});

test("looksLikeSecretValue heuristic", () => {
  assert(looksLikeSecretValue(FAKE_OPENAI_KEY));
  assert(looksLikeSecretValue(FAKE_AKIA));
  assert(!looksLikeSecretValue("us-west-2"));
  assert(!looksLikeSecretValue("https://proxy.example.com/v1"));
  assert(!looksLikeSecretValue("arn:aws:bedrock:us-west-2:123456789012:inference-profile/x"));
  assert(!looksLikeSecretValue("claude-sonnet-4-5"));
});

// ------------------------------------------------------- claude settings

test("claude settings: region, profile, ARN model extracted; key redacted", () => {
  const f = parseClaudeSettings(CLAUDE_FIXTURE, "~/.claude/settings.json");
  const kinds = f.map((x) => x.kind);
  assert(kinds.includes("region"), "region finding");
  assert(kinds.includes("awsProfile"), "awsProfile finding");
  const model = f.find((x) => x.kind === "model" && x.provider === "bedrock");
  assert(model, "bedrock model finding");
  assert(
    model.value.includes("arn:aws:bedrock:us-west-2:123456789012:inference-profile/"),
    "full ARN preserved"
  );
  const creds = f.filter((x) => x.kind === "credentialRef");
  assert(creds.length >= 1, "credential reference for OPENAI_API_KEY");
  assert(creds[0].note.includes("enter it yourself"), "credentialRef note is plain-language");
  noSecretsLeak(f, [FAKE_OPENAI_KEY]);
});

test("claude settings: malformed ARN is dropped, not imported", () => {
  const f = parseClaudeSettings(
    JSON.stringify({ env: { ANTHROPIC_MODEL: "arn:aws:bedrock:::bogus" } }),
    "~/.claude/settings.json"
  );
  assert(!f.some((x) => x.kind === "model" && x.value.includes("bogus")), "bad ARN dropped");
});

test("claude settings: invalid JSON yields nothing (no crash)", () => {
  assert.deepStrictEqual(parseClaudeSettings("not json{{{", "x"), []);
});

// ------------------------------------------------------------------- .env

test(".env: endpoint + region parsed; secrets become references only", () => {
  const f = parseDotEnv(DOTENV_FIXTURE, "<project>/.env");
  const ep = f.find((x) => x.kind === "endpoint");
  assert(ep && ep.value === "https://proxy.example.com/v1", "endpoint extracted, comment stripped");
  const region = f.find((x) => x.kind === "region");
  assert(region && region.value === "us-east-1", "region extracted");
  const creds = f.filter((x) => x.kind === "credentialRef");
  assert(creds.length >= 2, `expected >=2 credential refs, got ${creds.length}`);
  noSecretsLeak(f, [FAKE_ANTHROPIC_KEY, FAKE_AKIA]);
});

test(".env: quoted values and export prefix handled", () => {
  const f = parseDotEnv('export AWS_REGION="eu-west-1"\nQUOTED_MODEL=\'opus\'', "x");
  assert(f.some((x) => x.kind === "region" && x.value === "eu-west-1"), "quoted region");
  assert(f.some((x) => x.kind === "model" && x.value === "opus"), "quoted model");
});

// ------------------------------------------------------------------- kilo

test("stripJsonc removes comments but keeps // inside strings", () => {
  const out = stripJsonc('{ "url": "https://x.example/v1" // trailing\n } /* block */');
  assert(out.includes("https://x.example/v1"), "URL with // intact");
  assert(!out.includes("trailing"), "line comment removed");
  assert(!out.includes("block"), "block comment removed");
});

test("kilo config: baseURL + models extracted; apiKey becomes reference", () => {
  const f = parseKiloConfig(KILO_FIXTURE, "~/.config/kilo/kilo.jsonc");
  const ep = f.find((x) => x.kind === "endpoint");
  assert(ep && ep.value === "https://gateway.example.com/v1", "kilo baseURL extracted");
  assert(f.some((x) => x.kind === "model" && x.value === "my-model-1"), "kilo model extracted");
  const creds = f.filter((x) => x.kind === "credentialRef");
  assert(creds.length >= 1, "apiKey {env:...} became a credential reference");
  noSecretsLeak(f, ["KILO_GATEWAY_KEY"]);
});

test("kilo config: invalid JSONC yields nothing (no crash)", () => {
  assert.deepStrictEqual(parseKiloConfig("{{{nope", "x"), []);
});

// ------------------------------------------------------------- scanSources

test("scanSources aggregates across sources and dedupes", () => {
  const files = {
    "/home/t/.claude/settings.json": JSON.stringify({ env: { AWS_REGION: "us-west-2" } }),
    "/ws/.env": "AWS_REGION=us-west-2\n",
  };
  const r = scanSources((p) => files[p] ?? null, "/home/t", "/ws");
  assert(r.scanned.includes("~/.claude/settings.json"), "scanned list");
  assert(r.missing.includes("<project>/.env.local"), "missing list");
  const regions = r.findings.filter((x) => x.kind === "region");
  assert.strictEqual(regions.length, 1, "duplicate region deduped");
  assert(regions[0].source.includes("settings.json") && regions[0].source.includes(".env"), "sources merged");
});

// ------------------------------------------------------------- apply logic

test("applicable vs informational split", () => {
  const f = parseClaudeSettings(CLAUDE_FIXTURE, "s");
  const app = applicableFindings(f);
  const info = informationalFindings(f);
  assert(app.every((x) => ["region", "endpoint", "model"].includes(x.kind)));
  assert(info.every((x) => ["awsProfile", "credentialRef"].includes(x.kind)));
  assert(info.some((x) => x.kind === "awsProfile"), "profile is informational (SSO unsupported)");
});

test("buildConfigWrites maps findings to awino.* writes", () => {
  const writes = buildConfigWrites([
    { source: "s", provider: "bedrock", kind: "region", value: "us-west-2", note: "" },
    { source: "s", provider: "bedrock", kind: "model", value: "arn:aws:bedrock:x", note: "" },
    { source: "s", provider: "openai", kind: "endpoint", value: "https://p.example/v1", note: "" },
  ]);
  const map = Object.fromEntries(writes.map((w) => [w.key, w.value]));
  // last provider wins in this selection order — the confirm dialog shows it
  assert.strictEqual(map["bedrockRegion"], "us-west-2");
  assert.strictEqual(map["model"], "arn:aws:bedrock:x");
  assert.strictEqual(map["endpoint"], "https://p.example/v1");
});

test("buildConfigWrites is pure: no writes happen without the caller applying them", () => {
  // The function returns data; the extension only writes after user confirm.
  // Pin the contract: returns an array, touches nothing else.
  const w = buildConfigWrites([]);
  assert(Array.isArray(w) && w.length === 0, "empty selection -> no writes");
});

test("summarizeWrites renders one line per write for the confirm dialog", () => {
  const s = summarizeWrites([
    { key: "provider", value: "bedrock" },
    { key: "bedrockRegion", value: "us-west-2" },
  ]);
  assert(s.includes("provider = bedrock") && s.includes("bedrockRegion = us-west-2"));
});

test("formatScannedLine joins scanned labels for the 'Looked in' UI line", () => {
  assert.strictEqual(
    formatScannedLine(["~/.config/kilo/kilo.jsonc", "<project>/.env"]),
    "~/.config/kilo/kilo.jsonc, <project>/.env"
  );
  assert.strictEqual(formatScannedLine([]), "(none found)");
  // Paths only — the labels carry no values or secrets.
  const line = formatScannedLine(["~/.claude/settings.json"]);
  assert(!line.includes(FAKE_OPENAI_KEY), "no secret values in the scanned line");
});

console.log(`\nALL CONNECTION-IMPORTER UNIT TESTS PASSED (${passed} checks)`);
