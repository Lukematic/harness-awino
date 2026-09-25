// modelDiscovery.ts — vscode-free model listing for the Models & Providers
// panel. "Fetch models" queries the provider's list endpoint and returns the
// model ids so the panel can offer them as a dropdown instead of a blind
// text field.
//
// openai-compatible: GET {base}/models (OpenAI list API), key as bearer.
//   {base} is the endpoint with /v1[/chat/completions] semantics mirrored
//   from the sidecar: a base ending in /v1 lists at /v1/models, anything
//   else at /v1/models under the base. Default base http://localhost:11434
//   (vLLM / llama.cpp / Ollama's OpenAI-compat endpoint).
// ollama: GET {base}/api/tags, no key needed. Default base
//   http://localhost:11434.
// bedrock: no listable equivalent — the panel keeps manual entry.

import * as http from "http";
import * as https from "https";

export const OLLAMA_DEFAULT_BASE = "http://localhost:11434";
export const OPENAI_COMPAT_DEFAULT_BASE = "http://localhost:11434";

const FETCH_TIMEOUT_MS = 15000;
const MAX_BODY_BYTES = 2 * 1024 * 1024;
const MAX_MODELS = 200;

function normalizeBase(endpoint: string | undefined, fallback: string): string | null {
  const base = (endpoint ?? "").trim().replace(/\/+$/, "") || fallback;
  if (!/^https?:\/\//i.test(base)) {
    return null;
  }
  return base;
}

/**
 * The list-models URL for a provider, or null when the provider has no
 * listable endpoint (bedrock, anthropic, echo) or the base is not http(s).
 */
export function modelsListUrl(provider: string, endpoint?: string): string | null {
  const p = (provider ?? "").toLowerCase();
  if (p === "ollama") {
    const base = normalizeBase(endpoint, OLLAMA_DEFAULT_BASE);
    return base ? base + "/api/tags" : null;
  }
  if (p === "openai") {
    const base = normalizeBase(endpoint, OPENAI_COMPAT_DEFAULT_BASE);
    if (!base) {
      return null;
    }
    return base.endsWith("/v1") ? base + "/models" : base + "/v1/models";
  }
  return null;
}

function cleanIds(ids: unknown[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of ids) {
    if (typeof id !== "string") {
      continue;
    }
    const t = id.trim();
    if (!t || seen.has(t)) {
      continue;
    }
    seen.add(t);
    out.push(t);
    if (out.length >= MAX_MODELS) {
      break;
    }
  }
  return out;
}

/** OpenAI list API: { "data": [ { "id": "..." } ] } */
export function parseOpenAiModels(payload: unknown): string[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const data = (payload as { data?: unknown }).data;
  if (!Array.isArray(data)) {
    return [];
  }
  return cleanIds(data.map((m) => (m && typeof m === "object" ? (m as { id?: unknown }).id : undefined)));
}

/** Ollama tags API: { "models": [ { "name": "..." } ] } */
export function parseOllamaTags(payload: unknown): string[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const models = (payload as { models?: unknown }).models;
  if (!Array.isArray(models)) {
    return [];
  }
  return cleanIds(models.map((m) => (m && typeof m === "object" ? (m as { name?: unknown }).name : undefined)));
}

export interface FetchJsonOptions {
  bearer?: string;
  timeoutMs?: number;
}

/** GET a URL and parse the body as JSON. Rejects with a plain-language Error. */
export function fetchJson(url: string, opts: FetchJsonOptions = {}): Promise<unknown> {
  const timeoutMs = opts.timeoutMs ?? FETCH_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      reject(new Error(`not a valid URL: ${url}`));
      return;
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      reject(new Error(`only http(s) endpoints can be queried`));
      return;
    }
    const lib = parsed.protocol === "https:" ? https : http;
    const headers: Record<string, string> = { Accept: "application/json" };
    if (opts.bearer) {
      headers["Authorization"] = `Bearer ${opts.bearer}`;
    }
    const req = lib.request(
      url,
      { method: "GET", headers, timeout: timeoutMs },
      (res) => {
        const status = res.statusCode ?? 0;
        if (status === 401 || status === 403) {
          res.resume();
          reject(new Error(`the endpoint rejected the request (HTTP ${status}) — check the API key`));
          return;
        }
        if (status < 200 || status >= 300) {
          res.resume();
          reject(new Error(`the endpoint returned HTTP ${status}`));
          return;
        }
        let bytes = 0;
        const chunks: Buffer[] = [];
        res.on("data", (c: Buffer) => {
          bytes += c.length;
          if (bytes <= MAX_BODY_BYTES) {
            chunks.push(c);
          }
        });
        res.on("end", () => {
          if (bytes > MAX_BODY_BYTES) {
            reject(new Error(`the endpoint returned an unexpectedly large response`));
            return;
          }
          try {
            resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
          } catch {
            reject(new Error(`the endpoint didn't return JSON`));
          }
        });
        res.on("error", (e) => reject(toPlainError(url, e)));
      }
    );
    req.on("timeout", () => {
      req.destroy(new Error(`timed out after ${Math.round(timeoutMs / 1000)}s — is the endpoint reachable?`));
    });
    req.on("error", (e) => reject(toPlainError(url, e)));
    req.end();
  });
}

function toPlainError(url: string, e: Error & { code?: string }): Error {
  const code = (e as { code?: string }).code;
  if (code === "ECONNREFUSED") {
    let host = url;
    try {
      host = new URL(url).host;
    } catch {
      /* keep url */
    }
    return new Error(`couldn't reach ${host} — is the server running?`);
  }
  if (code === "ENOTFOUND" || code === "EAI_AGAIN") {
    return new Error(`couldn't resolve the endpoint hostname — check the URL`);
  }
  return e instanceof Error ? e : new Error(String(e));
}

export interface DiscoverResult {
  ok: boolean;
  models: string[];
  /** Plain-language reason when ok is false. Never blocks saving. */
  error?: string;
}

/**
 * List the models a provider endpoint offers. Never throws — failures come
 * back as { ok: false, error } so the panel keeps its manual text input.
 */
export async function discoverModels(
  provider: string,
  endpoint?: string,
  key?: string
): Promise<DiscoverResult> {
  const url = modelsListUrl(provider, endpoint);
  if (!url) {
    return { ok: false, models: [], error: `model listing isn't available for provider "${provider}"` };
  }
  let payload: unknown;
  try {
    payload = await fetchJson(url, key ? { bearer: key } : {});
  } catch (e) {
    return { ok: false, models: [], error: e instanceof Error ? e.message : String(e) };
  }
  const p = (provider ?? "").toLowerCase();
  const models = p === "ollama" ? parseOllamaTags(payload) : parseOpenAiModels(payload);
  if (!models.length) {
    return { ok: false, models: [], error: `the endpoint returned no models` };
  }
  return { ok: true, models };
}
