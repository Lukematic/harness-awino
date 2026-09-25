/**
 * Unit tests for src/bedrock.ts (AWS Bedrock provider logic).
 *
 * Pure logic — no vscode, no network. HTTP is exercised through an
 * injected mock fetch. Run after `npm run compile`:
 *   node ./test/bedrock.js
 */
"use strict";
const assert = require("assert");

const {
  BEDROCK_REGIONS,
  isValidRegion,
  bedrockEndpointForRegion,
  parseAwsProfileNames,
  parseBedrockModelRef,
  resolveBedrockConnection,
  validateBedrockSetup,
  bedrockChatRequestShape,
  probeBedrockModels,
} = require("../out/bedrock.js");

let n = 0;
function ok(cond, label) {
  n += 1;
  assert.ok(cond, label);
  console.log(`ok ${n} - ${label}`);
}

async function main() {
  // ---- regions -------------------------------------------------------
  ok(BEDROCK_REGIONS.includes("us-east-1"), "region list contains us-east-1");
  ok(BEDROCK_REGIONS.includes("eu-west-1"), "region list contains eu-west-1");
  ok(isValidRegion("us-east-1"), "us-east-1 is a valid region");
  ok(isValidRegion("us-gov-west-1"), "us-gov-west-1 is a valid region");
  ok(!isValidRegion("not-a-region-"), "'not-a-region-' rejected");
  ok(!isValidRegion(""), "empty region rejected");

  let r = bedrockEndpointForRegion("us-east-1");
  ok(r.ok && r.endpoint === "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
    "us-east-1 derives the runtime OpenAI-compatible endpoint");
  r = bedrockEndpointForRegion("eu-west-2");
  ok(r.ok && r.endpoint === "https://bedrock-runtime.eu-west-2.amazonaws.com/openai/v1",
    "eu-west-2 derives its endpoint");
  r = bedrockEndpointForRegion("bogus");
  ok(!r.ok && /doesn't look like an AWS region/.test(r.error) && /one next action|Pick a region/.test(r.error),
    "bad region yields a plain-language error with a next action");

  // ---- ARN parsing: the four valid kinds ------------------------------
  let p = parseBedrockModelRef(
    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0");
  ok(p.kind === "foundation-model" && p.region === "us-east-1" && p.accountId === undefined &&
    p.resourceId === "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "foundation-model ARN parses (empty account segment)");

  p = parseBedrockModelRef(
    "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-3-5-sonnet-20240620-v1:0");
  ok(p.kind === "inference-profile" && p.accountId === "123456789012" &&
    p.resourceId === "us.anthropic.claude-3-5-sonnet-20240620-v1:0" &&
    /system inference profile/.test(p.description),
    "system inference-profile ARN parses with description");

  p = parseBedrockModelRef(
    "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/my-profile-1");
  ok(p.kind === "application-inference-profile" && p.resourceId === "my-profile-1" &&
    /application inference profile/.test(p.description),
    "application inference-profile ARN parses");

  p = parseBedrockModelRef(
    "arn:aws:bedrock:eu-west-1:123456789012:provisioned-model/abc123def456");
  ok(p.kind === "provisioned-model" && p.region === "eu-west-1",
    "provisioned-model ARN parses");

  // ---- ARN parsing: bare ids ------------------------------------------
  p = parseBedrockModelRef("anthropic.claude-3-5-sonnet-20241022-v2:0");
  ok(p.kind === "model-id", "bare foundation model id accepted");
  p = parseBedrockModelRef("us.anthropic.claude-sonnet-4-5-20250929-v1:0");
  ok(p.kind === "model-id", "system inference-profile id accepted as model-id");
  p = parseBedrockModelRef("global.anthropic.claude-opus-4-6");
  ok(p.kind === "model-id", "global inference-profile id accepted as model-id");

  // ---- ARN parsing: malformed ------------------------------------------
  p = parseBedrockModelRef(
    "arn:aws:bedrock:us-east-1:123:application-inference-profile/x");
  ok(p.kind === "invalid" && /12-digit/.test(p.error) && /It means/.test(p.error),
    "short account id rejected with plain-language error");

  p = parseBedrockModelRef(
    "arn:aws:bedrock:us-east-1:123456789012:foundation-model/anthropic.claude-x");
  ok(p.kind === "invalid" && /never include an account id/.test(p.error),
    "foundation-model ARN with account id rejected");

  p = parseBedrockModelRef("arn:aws:s3:::my-bucket");
  ok(p.kind === "invalid" && /different AWS service/.test(p.error),
    "non-Bedrock ARN rejected");

  p = parseBedrockModelRef(
    "arn:aws:bedrock:us-east-1:123456789012:weird-thing/x");
  ok(p.kind === "invalid" && /doesn't match any Bedrock model ARN shape/.test(p.error),
    "unknown ARN resource kind rejected");

  p = parseBedrockModelRef(
    "arn:aws:bedrock:us-east-1:123456789012:provisioned-model/");
  ok(p.kind === "invalid" && /empty resource id/.test(p.error),
    "ARN with empty resource id rejected");

  p = parseBedrockModelRef("");
  ok(p.kind === "invalid" && /empty/.test(p.error), "empty model field rejected");

  p = parseBedrockModelRef("has spaces in it");
  ok(p.kind === "invalid" && /characters Bedrock doesn't allow/.test(p.error),
    "model id with spaces rejected");

  // every invalid error follows house style: what happened / what it means /
  // a next action — spot-check the shape on one of them
  p = parseBedrockModelRef("arn:aws:bedrock:us-east-1:123:application-inference-profile/x");
  ok(/It means/.test(p.error) && /Paste the ARN again/.test(p.error),
    "ARN error carries what-it-means and a next action");

  // ---- connection resolution --------------------------------------------
  let c = resolveBedrockConnection({ region: "us-west-2", apiKey: "KEY" });
  ok(c.ok && c.args.sidecarProvider === "openai" &&
    c.args.endpoint === "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1" &&
    c.args.keyEnvVar === "AWINO_API_KEY",
    "region resolves to sidecar=openai + derived endpoint + key env var");

  c = resolveBedrockConnection({ region: "us-west-2", endpoint: "https://custom.example/v1", apiKey: "KEY" });
  ok(c.ok && c.args.endpoint === "https://custom.example/v1",
    "explicit endpoint override wins over the region");

  c = resolveBedrockConnection({ apiKey: "KEY" });
  ok(!c.ok && /No Bedrock region/.test(c.error), "missing region errors plainly");

  c = resolveBedrockConnection({ region: "us-east-1" });
  ok(!c.ok && /No Bedrock API key/.test(c.error) && /Set API Key/.test(c.error),
    "missing key errors with the one next action");

  // ---- connection resolution: aws-profile (SigV4) mode -------------------
  c = resolveBedrockConnection({ region: "us-west-2", authMode: "aws-profile", awsProfile: "sso" });
  ok(c.ok && c.args.sidecarProvider === "bedrock" &&
    c.args.endpoint === "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1" &&
    c.args.awsProfile === "sso" && c.args.keyEnvVar === undefined,
    "profile mode: sidecar=bedrock + profile forwarded + NO key env var");

  c = resolveBedrockConnection({ region: "us-west-2", authMode: "aws-profile", awsProfile: "sso", apiKey: "LEFTOVER" });
  ok(c.ok && c.args.keyEnvVar === undefined,
    "profile mode never hands the sidecar a key, even when one is stored");

  c = resolveBedrockConnection({ region: "us-west-2", authMode: "aws-profile", awsProfile: "  " });
  ok(!c.ok && /no profile name is set/.test(c.error) && /bedrockAwsProfile/.test(c.error),
    "profile mode without a profile name errors with the one next action");

  c = resolveBedrockConnection({ region: "us-west-2", authMode: "aws-profile" });
  ok(!c.ok && /no profile name is set/.test(c.error),
    "profile mode without a profile at all errors plainly");

  // ---- setup validation ----------------------------------------------------
  let errs = validateBedrockSetup({
    region: "us-east-1",
    authMode: "api-key",
    modelRef: "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/p",
    keyPresent: true,
  });
  ok(errs.length === 0, "valid api-key setup has no errors");

  errs = validateBedrockSetup({
    region: "us-east-1",
    authMode: "aws-profile",
    modelRef: "us.anthropic.claude-x",
    keyPresent: false,
    awsProfile: "sso",
  });
  ok(errs.length === 0, "valid profile setup has no errors (no key needed)");

  errs = validateBedrockSetup({
    region: "us-east-1",
    authMode: "aws-profile",
    modelRef: "us.anthropic.claude-x",
    keyPresent: false,
    awsProfile: "",
  });
  ok(errs.length === 1 && /No AWS profile was chosen/.test(errs[0]),
    "profile setup without a profile reports exactly that problem");

  // ---- ~/.aws/config profile parsing ---------------------------------------
  let names = parseAwsProfileNames(
    "[default]\nregion = us-east-1\n\n[profile sso]\nsso_start_url = https://x\n\n[profile work]\nregion = eu-west-1\n");
  ok(JSON.stringify(names) === JSON.stringify(["default", "sso", "work"]),
    "profile names parsed from ~/.aws/config (default + [profile X])");

  names = parseAwsProfileNames("[profile sso]\n[profile sso]\n");
  ok(JSON.stringify(names) === JSON.stringify(["sso"]),
    "duplicate profile sections deduped");

  names = parseAwsProfileNames(
    "[sso-session my-sso]\nsso_start_url = https://x\n\n[profile sso]\nsso_session = my-sso\n");
  ok(JSON.stringify(names) === JSON.stringify(["sso"]),
    "sso-session blocks are not offered as profiles");

  names = parseAwsProfileNames("");
  ok(names.length === 0, "empty config yields no profiles");

  errs = validateBedrockSetup({ region: "nope", modelRef: "", keyPresent: false });
  ok(errs.length === 3, "invalid setup reports all three problems");

  // ---- wire contract (mocked, no live AWS) ----------------------------------
  const shape = bedrockChatRequestShape(
    "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
    "SECRET",
    "us.anthropic.claude-x");
  ok(shape.url === "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/chat/completions",
    "chat URL is endpoint + /chat/completions");
  ok(shape.headers.Authorization === "Bearer SECRET", "Bearer auth header shape");
  ok(shape.body.model === "us.anthropic.claude-x" && shape.body.stream === false,
    "body carries the model id, streaming off");

  // ---- probe with mocked fetch ----------------------------------------------
  const mockOk = async (url, init) => {
    assert.ok(init.headers.Authorization.startsWith("Bearer "), "probe sends Bearer auth");
    assert.ok(url.endsWith("/models"), "probe hits /models");
    return { ok: true, status: 200, json: async () => ({ data: [{ id: "a" }, { id: "b" }] }) };
  };
  let probe = await probeBedrockModels("https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1", "K", mockOk);
  ok(probe.ok && probe.models.length === 2, "probe success returns model ids");

  const mock401 = async () => ({ ok: false, status: 401, json: async () => ({}) });
  probe = await probeBedrockModels("https://x/openai/v1", "BADKEY", mock401);
  ok(!probe.ok && /rejected the API key/.test(probe.error) && !probe.error.includes("BADKEY"),
    "401 yields a key error that never echoes the key");

  const mock404 = async () => ({ ok: false, status: 404, json: async () => ({}) });
  probe = await probeBedrockModels("https://x/openai/v1", "K", mock404);
  ok(!probe.ok && /endpoint or region/.test(probe.error), "404 points at endpoint/region");

  const mockDown = async () => { throw new Error("socket hang up"); };
  probe = await probeBedrockModels("https://x/openai/v1", "K", mockDown);
  ok(!probe.ok && /Couldn't reach/.test(probe.error), "network failure yields a plain-language error");

  console.log(`\nALL BEDROCK UNIT TESTS PASSED (${n} checks)`);
}

main().catch((e) => {
  console.error("BEDROCK TESTS FAILED:", e);
  process.exit(1);
});
