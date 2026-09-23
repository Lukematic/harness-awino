/**
 * bedrock.ts — AWS Bedrock provider support for the Awino extension.
 *
 * Pure logic: no `vscode` import, so it is unit-testable with plain node.
 * The extension host (extension.ts) wires these helpers to SecretStorage,
 * quick-picks, and the sidecar spawn.
 *
 * Wire protocol note (honest, read before "fixing"): Bedrock exposes an
 * OpenAI-compatible endpoint
 *   https://bedrock-runtime.{region}.amazonaws.com/openai/v1
 * which speaks the OpenAI chat-completions protocol and accepts a Bedrock
 * API key as `Authorization: Bearer <key>`. The Python sidecar's
 * OpenAICompatibleBackend already implements exactly that, so the extension
 * maps the user-facing provider "bedrock" to the sidecar provider "openai"
 * with the derived endpoint and the Bedrock key. The UI keeps the "bedrock"
 * label (see session.displayProvider in extension.ts).
 *
 * What is NOT here: AWS SSO / shared-config profiles. Those need SigV4
 * request signing inside the sidecar (prototype/), which this change
 * deliberately does not touch. SSO is documented as future work, never
 * half-wired.
 */

// ------------------------------------------------------------------ regions

/** Curated Bedrock regions. Users can also type any other valid region. */
export const BEDROCK_REGIONS: string[] = [
  "us-east-1",
  "us-east-2",
  "us-west-2",
  "eu-west-1",
  "eu-west-2",
  "eu-west-3",
  "eu-central-1",
  "eu-central-2",
  "eu-north-1",
  "eu-south-1",
  "eu-south-2",
  "ap-south-1",
  "ap-south-2",
  "ap-southeast-1",
  "ap-southeast-2",
  "ap-southeast-3",
  "ap-northeast-1",
  "ap-northeast-2",
  "ap-northeast-3",
  "ca-central-1",
  "sa-east-1",
  "me-south-1",
  "me-central-1",
  "af-south-1",
  "il-central-1",
  "us-gov-west-1",
  "us-gov-east-1",
];

const REGION_RE = /^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$/;

export function isValidRegion(region: string): boolean {
  return REGION_RE.test(region.trim());
}

/**
 * Derive the OpenAI-compatible endpoint for a region.
 * Returns { ok, endpoint } or { ok:false, error } in house style
 * (what happened / what it means / the one next action).
 */
export function bedrockEndpointForRegion(
  region: string
): { ok: true; endpoint: string } | { ok: false; error: string } {
  const r = region.trim();
  if (!isValidRegion(r)) {
    return {
      ok: false,
      error:
        `That region ("${r || "(empty)"}") doesn't look like an AWS region name. ` +
        `It means the endpoint URL can't be built from it. ` +
        `Pick a region from the list (e.g. us-east-1) or type one in the same shape.`,
    };
  }
  return { ok: true, endpoint: `https://bedrock-runtime.${r}.amazonaws.com/openai/v1` };
}

// --------------------------------------------------------------- model refs

export type BedrockModelKind =
  | "foundation-model"
  | "inference-profile"
  | "application-inference-profile"
  | "provisioned-model"
  | "model-id" // bare model or inference-profile id, not an ARN
  | "invalid";

export interface ParsedModelRef {
  kind: BedrockModelKind;
  /** The original string, trimmed. */
  ref: string;
  partition?: string;
  region?: string;
  accountId?: string;
  /** Resource id after the final slash (profile name, provisioned id, model id). */
  resourceId?: string;
  /** Plain-language error (house style) when kind === "invalid". */
  error?: string;
  /** Short human description when kind !== "invalid". */
  description?: string;
}

const ARN_RE =
  /^arn:(aws(?:-[a-z]+)?):bedrock:([a-z0-9-]+):(\d*):(foundation-model|inference-profile|application-inference-profile|provisioned-model)\/(.*)$/;

const KIND_LABEL: Record<string, string> = {
  "foundation-model": "foundation model",
  "inference-profile": "system inference profile",
  "application-inference-profile": "application inference profile",
  "provisioned-model": "provisioned throughput model",
};

const MODEL_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:=-]{0,2047}$/;

function arnError(what: string, example: string): string {
  return (
    `${what} ` +
    `It means Bedrock won't accept that string as a model identifier. ` +
    `Paste the ARN again from the Bedrock console, e.g. ${example}`
  );
}

/**
 * Parse a model field value: a full Bedrock ARN (any of the four kinds),
 * or a bare model / inference-profile id.
 */
export function parseBedrockModelRef(raw: string): ParsedModelRef {
  const ref = (raw ?? "").trim();
  if (!ref) {
    return {
      kind: "invalid",
      ref,
      error:
        `The model field is empty. ` +
        `It means the harness doesn't know which Bedrock model to call. ` +
        `Enter a model id (e.g. us.anthropic.claude-sonnet-4-5-20250929-v1:0) or paste a full ARN.`,
    };
  }

  if (!ref.startsWith("arn:")) {
    if (/\s/.test(ref) || !MODEL_ID_RE.test(ref)) {
      return {
        kind: "invalid",
        ref,
        error:
          `That model id ("${ref.slice(0, 60)}") contains characters Bedrock doesn't allow. ` +
          `It means the request would be rejected before it reaches a model. ` +
          `Use the plain model or inference-profile id, e.g. us.anthropic.claude-sonnet-4-5-20250929-v1:0`,
      };
    }
    return {
      kind: "model-id",
      ref,
      resourceId: ref,
      description: `model id "${ref}" (passed through to Bedrock as-is)`,
    };
  }

  const m = ARN_RE.exec(ref);
  if (!m) {
    // Figure out the most helpful single next action.
    if (!ref.startsWith("arn:aws")) {
      return {
        kind: "invalid",
        ref,
        error: arnError(
          `That ARN doesn't start with "arn:aws" — it's not an AWS ARN at all.`,
          "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0"
        ),
      };
    }
    if (!ref.includes(":bedrock:")) {
      return {
        kind: "invalid",
        ref,
        error: arnError(
          `That ARN is for a different AWS service, not Bedrock.`,
          "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/my-profile"
        ),
      };
    }
    return {
      kind: "invalid",
      ref,
      error: arnError(
        `That ARN doesn't match any Bedrock model ARN shape (foundation-model, inference-profile, application-inference-profile, provisioned-model).`,
        "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/my-profile"
      ),
    };
  }

  const [, partition, region, account, kind, resourceId] = m;
  if (!resourceId || !resourceId.trim() || /\s/.test(resourceId)) {
    return {
      kind: "invalid",
      ref,
      error: arnError(
        `The ARN ends with an empty resource id (nothing after the final "/").`,
        "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/my-profile"
      ),
    };
  }
  if (!isValidRegion(region)) {
    return {
      kind: "invalid",
      ref,
      error: arnError(
        `The region segment ("${region}") isn't a valid AWS region name.`,
        "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/my-profile"
      ),
    };
  }
  if (kind === "foundation-model") {
    // Foundation-model ARNs have an EMPTY account segment:
    // arn:aws:bedrock:us-east-1::foundation-model/<model-id>
    if (account !== "") {
      return {
        kind: "invalid",
        ref,
        error: arnError(
          `Foundation-model ARNs never include an account id — remove the digits ("${account}") between the colons.`,
          "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0"
        ),
      };
    }
  } else if (!/^\d{12}$/.test(account)) {
    return {
      kind: "invalid",
      ref,
      error: arnError(
        `That ARN kind needs a 12-digit AWS account id, but the account segment is "${account || "(empty)"}".`,
        "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/my-profile"
      ),
    };
  }

  const label = KIND_LABEL[kind];
  const where = kind === "foundation-model" ? `in ${region}` : `in ${region} (account ${account})`;
  return {
    kind: kind as ParsedModelRef["kind"],
    ref,
    partition,
    region,
    accountId: account || undefined,
    resourceId,
    description: `${label} "${resourceId}" ${where}`,
  };
}

// ---------------------------------------------------------- connection args

export interface BedrockConnectInput {
  /** User override from awino.endpoint; empty = derive from region. */
  endpoint?: string;
  /** From awino.bedrockRegion. */
  region?: string;
  /** Bedrock API key from SecretStorage (never logged). */
  apiKey?: string;
}

export interface BedrockConnectArgs {
  /** What the sidecar is told (it only speaks openai/anthropic/ollama/echo). */
  sidecarProvider: "openai";
  endpoint: string;
  /** Env var the key is passed under; the OpenAI-compatible backend reads it. */
  keyEnvVar: "AWINO_API_KEY";
}

/**
 * Resolve what to hand the sidecar for provider "bedrock".
 * Pure — the extension supplies secrets; nothing here touches vscode.
 */
export function resolveBedrockConnection(
  input: BedrockConnectInput
): { ok: true; args: BedrockConnectArgs } | { ok: false; error: string } {
  const override = (input.endpoint ?? "").trim();
  let endpoint = override;
  if (!endpoint) {
    const region = (input.region ?? "").trim();
    if (!region) {
      return {
        ok: false,
        error:
          `No Bedrock region is set. ` +
          `It means the endpoint URL can't be derived. ` +
          `Run "Awino: Set Up AWS Bedrock" and pick a region, or set awino.bedrockRegion.`,
      };
    }
    const derived = bedrockEndpointForRegion(region);
    if (!derived.ok) {
      return derived;
    }
    endpoint = derived.endpoint;
  }
  if (!input.apiKey) {
    return {
      ok: false,
      error:
        `No Bedrock API key is stored. ` +
        `It means the harness can't authenticate to Bedrock. ` +
        `Run "Awino: Set API Key", choose AWS Bedrock, and paste a key from the Bedrock console (API keys → Generate API key).`,
    };
  }
  return { ok: true, args: { sidecarProvider: "openai", endpoint, keyEnvVar: "AWINO_API_KEY" } };
}

/** Validate the whole guided-setup result before writing config. */
export function validateBedrockSetup(input: {
  region: string;
  modelRef: string;
  keyPresent: boolean;
}): string[] {
  const errors: string[] = [];
  const ep = bedrockEndpointForRegion(input.region);
  if (!ep.ok) {
    errors.push(ep.error);
  }
  const parsed = parseBedrockModelRef(input.modelRef);
  if (parsed.kind === "invalid") {
    errors.push(parsed.error as string);
  }
  if (!input.keyPresent) {
    errors.push(
      `No Bedrock API key was provided. ` +
        `It means the harness can't authenticate to Bedrock. ` +
        `Create one in the Bedrock console (API keys → Generate API key) and paste it when asked.`
    );
  }
  return errors;
}

// ------------------------------------------------------- wire contract test

export interface ChatRequestShape {
  url: string;
  headers: Record<string, string>;
  body: Record<string, unknown>;
}

/**
 * The exact HTTP shape the sidecar's OpenAI-compatible backend sends for
 * Bedrock (chat-completions + Bearer key). Unit tests pin this contract
 * with a mocked fetch; the Python side is the implementation.
 */
export function bedrockChatRequestShape(
  endpoint: string,
  apiKey: string,
  model: string
): ChatRequestShape {
  const base = endpoint.replace(/\/+$/, "");
  const url = base.endsWith("/chat/completions") ? base : `${base}/chat/completions`;
  return {
    url,
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + apiKey },
    body: {
      model,
      messages: [{ role: "system", content: "" }, { role: "user", content: "" }],
      stream: false,
      temperature: 0.2,
    },
  };
}

export interface ProbeResult {
  ok: boolean;
  /** Model ids when ok. */
  models?: string[];
  /** House-style error when !ok. Never contains key material. */
  error?: string;
}

type FetchFn = (url: string, init?: Record<string, unknown>) => Promise<{
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}>;

/**
 * Test a Bedrock connection by listing models (GET {endpoint}/models).
 * fetchFn is injectable for tests; the wizard passes the real fetch.
 * Key material never appears in errors.
 */
export async function probeBedrockModels(
  endpoint: string,
  apiKey: string,
  fetchFn?: FetchFn
): Promise<ProbeResult> {
  const f: FetchFn =
    fetchFn ??
    ((globalThis as unknown as { fetch: FetchFn }).fetch?.bind(globalThis) as FetchFn);
  if (!f) {
    return {
      ok: false,
      error:
        `No HTTP client is available in this environment. ` +
        `It means the connection can't be tested from here. ` +
        `Skip the test — the key and region are still saved.`,
    };
  }
  const url = endpoint.replace(/\/+$/, "") + "/models";
  let resp: { ok: boolean; status: number; json(): Promise<unknown> };
  try {
    resp = await f(url, { headers: { Authorization: "Bearer " + apiKey } });
  } catch (e) {
    return {
      ok: false,
      error:
        `Couldn't reach ${url} (${e instanceof Error ? e.message : String(e)}). ` +
        `It usually means no network path to AWS or a wrong region. ` +
        `Check the region and your network, then test again.`,
    };
  }
  if (resp.status === 401 || resp.status === 403) {
    return {
      ok: false,
      error:
        `Bedrock rejected the API key (HTTP ${resp.status}). ` +
        `It means the key is wrong, expired, or lacks Bedrock access. ` +
        `Generate a fresh key in the Bedrock console (API keys → Generate API key) and try again.`,
    };
  }
  if (!resp.ok) {
    return {
      ok: false,
      error:
        `Bedrock answered HTTP ${resp.status} to the models probe. ` +
        `It means the endpoint or region may be wrong. ` +
        `Verify the region in the Bedrock console and try again.`,
    };
  }
  try {
    const payload = (await resp.json()) as { data?: Array<{ id?: string }> };
    const models = Array.isArray(payload.data)
      ? payload.data.map((m) => String(m.id ?? "")).filter(Boolean)
      : [];
    return { ok: true, models };
  } catch {
    return {
      ok: false,
      error:
        `Bedrock answered, but the response wasn't the expected model list. ` +
        `It means something non-Bedrock may be answering at that URL. ` +
        `Double-check the endpoint, then test again.`,
    };
  }
}
