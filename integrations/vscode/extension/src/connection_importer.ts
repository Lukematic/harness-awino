/**
 * connection_importer.ts — permission-first import of model connection details
 * from other tools (Claude Code, Kilo CLI, project .env files).
 *
 * Pure logic: no `vscode` import, so it is unit-testable with plain node.
 * The extension host (extension.ts) owns consent, quick-picks, and config
 * writes; this module only parses text and proposes non-secret config.
 *
 * IRON RULE (enforced here, tested in test/connection_importer.js):
 * secret values are NEVER returned, logged, or displayed. When a
 * secret-looking key or value is detected we emit a credentialRef finding
 * that names the file only — "found a credential reference in <file> —
 * enter it yourself in the providers panel" — and move on.
 *
 * Kilo Code honesty note (read before "fixing"):
 * - Kilo CLI (the newer TUI, an OpenCode fork) keeps NON-SECRET settings in
 *   plain files: ~/.config/kilo/kilo.jsonc (global), ./.kilo/kilo.jsonc or
 *   ./kilo.jsonc (project), and ~/.config/kilo/opencode.json. We parse those.
 * - Kilo CLI keeps credentials in ~/.local/share/kilo/auth.json (mode 0600).
 *   We deliberately NEVER read auth.json — it is a credential store by design.
 * - Kilo Code the VS Code extension stores provider profiles in VS Code
 *   globalState + SecretStorage, namespaced to its own extension id. There is
 *   no file to read, and reaching into another extension's storage would be
 *   hacking around its trust boundary. We do not do that.
 */

import { parseBedrockModelRef } from "./bedrock";

export type ProviderKind = "bedrock" | "openai" | "anthropic" | "unknown";

export type FindingKind = "region" | "endpoint" | "model" | "awsProfile" | "credentialRef";

export interface ImportFinding {
  /** Human label of the file, e.g. "~/.claude/settings.json". */
  source: string;
  provider: ProviderKind;
  kind: FindingKind;
  /** Non-secret connection fact. NEVER a secret (see IRON RULE above). */
  value: string;
  /** Plain-language context for the user. */
  note: string;
}

export interface ScanCandidate {
  /** Absolute path on disk. */
  absPath: string;
  /** Display label, e.g. "~/.claude/settings.json". */
  label: string;
  /** Which parser handles this file. */
  kind: "claude" | "dotenv" | "kilo";
}

export interface ScanResult {
  scanned: string[];
  missing: string[];
  findings: ImportFinding[];
}

/** A concrete awino.* config write proposed by the importer. */
export interface ConfigWrite {
  key: "provider" | "endpoint" | "model" | "bedrockRegion";
  value: string;
}

// ------------------------------------------------------------ secret guard

const SECRET_KEY_RE = /key|token|secret|password|passwd|credential|session|auth/i;

/** True when the KEY NAME alone marks the value as a secret. */
export function isSecretKeyName(name: string): boolean {
  return SECRET_KEY_RE.test(name);
}

/**
 * Credential-shaped prefixes: strong signals regardless of length
 * (AWS access key ids, sk- secret keys, chat/GitHub tokens...).
 * These never occur in legitimate model ids, regions, or endpoints.
 */
const CREDENTIAL_PREFIX_RE =
  /^(AKIA|ASIA|ABIA|ACCA|sk-|xox[bap]-|ghp_|gho_|ghu_|ghs_|ghr_|glpat-|Bearer\s)/i;

/**
 * Heuristic for a secret VALUE under an innocent key name: known credential
 * prefixes match at any length; otherwise long, whitespace-free,
 * token-shaped strings that are not ARNs / URLs / model ids.
 * Documented as a heuristic — the key-name rule is the primary defense.
 */
export function looksLikeSecretValue(value: string): boolean {
  const v = value.trim();
  if (CREDENTIAL_PREFIX_RE.test(v)) return true;
  if (v.length < 32) return false;
  if (/\s/.test(v)) return false;
  if (v.startsWith("arn:")) return false;
  if (/^https?:\/\//i.test(v)) return false;
  return /^[A-Za-z0-9_\-+/=]+$/.test(v);
}

// ------------------------------------------------------------------ parse

function pushFinding(
  out: ImportFinding[],
  source: string,
  provider: ProviderKind,
  kind: FindingKind,
  value: string,
  note: string
): void {
  const v = value.trim();
  if (!v) return;
  if (looksLikeSecretValue(v)) return; // defense in depth: never emit secret-shaped values
  out.push({ source, provider, kind, value: v, note });
}

function credentialRef(out: ImportFinding[], source: string, what: string): void {
  out.push({
    source,
    provider: "unknown",
    kind: "credentialRef",
    value: "",
    note: `Found a credential reference (${what}) in ${source} — enter it yourself in the providers panel. Nothing was copied.`,
  });
}

const REGION_RE = /^[a-z]{2}-[a-z-]+-\d+[a-z]?$/;

function classifyEndpoint(url: string): ProviderKind {
  const u = url.toLowerCase();
  if (u.includes("anthropic")) return "anthropic";
  if (u.includes("localhost") || u.includes("127.0.0.1") || u.includes("ollama")) return "openai";
  return "openai"; // OpenAI-compatible is the default assumption for custom base URLs
}

function handleEnvPair(
  out: ImportFinding[],
  source: string,
  rawKey: string,
  rawValue: string
): void {
  const key = rawKey.trim().toUpperCase();
  let value = rawValue.trim();
  // strip matching quotes
  if (
    value.length >= 2 &&
    ((value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'")))
  ) {
    value = value.slice(1, -1);
  }
  if (!value) return;

  // Secrets first: never let them reach a finding.
  if (isSecretKeyName(key) || looksLikeSecretValue(value)) {
    credentialRef(out, source, key.toLowerCase());
    return;
  }
  // Kilo-style references ({env:VAR}, {file:path}) are credential pointers.
  if (/^\{(env|file):/i.test(value)) {
    credentialRef(out, source, key.toLowerCase());
    return;
  }

  switch (key) {
    case "AWS_REGION":
    case "AWS_DEFAULT_REGION":
      if (REGION_RE.test(value)) {
        pushFinding(out, source, "bedrock", "region", value, "AWS region — used for the Bedrock provider.");
      }
      return;
    case "AWS_PROFILE":
    case "AWS_DEFAULT_PROFILE":
      pushFinding(
        out, source, "bedrock", "awsProfile", value,
        "AWS profile found. Note: A.W.I.N.O.'s Bedrock provider uses an API key, not SSO profiles — enter the key yourself in the providers panel."
      );
      return;
    case "ANTHROPIC_MODEL":
    case "CLAUDE_MODEL":
    case "MODEL": {
      if (value.startsWith("arn:")) {
        const parsed = parseBedrockModelRef(value);
        if (parsed.kind === "invalid") return; // malformed ARN: never import it
        pushFinding(out, source, "bedrock", "model", parsed.ref, parsed.description || "Bedrock model ARN.");
      } else if (/^anthropic|claude/i.test(value) || key === "ANTHROPIC_MODEL") {
        pushFinding(out, source, "anthropic", "model", value, "Model id from another tool's config.");
      } else {
        pushFinding(out, source, "unknown", "model", value, "Model id from another tool's config.");
      }
      return;
    }
    case "ANTHROPIC_BASE_URL":
      pushFinding(out, source, "anthropic", "endpoint", value, "Custom Anthropic endpoint.");
      return;
    case "OPENAI_BASE_URL":
    case "OPENAI_API_BASE":
      pushFinding(out, source, classifyEndpoint(value), "endpoint", value, "OpenAI-compatible endpoint.");
      return;
    case "OLLAMA_HOST": {
      const url = /^https?:\/\//i.test(value) ? value : `http://${value}`;
      pushFinding(out, source, "openai", "endpoint", url, "Ollama host — OpenAI-compatible.");
      return;
    }
    default:
      // MODEL-ish keys, e.g. OPENAI_MODEL, BEDROCK_MODEL_ID
      if (/MODEL/.test(key) && value.length < 200) {
        if (value.startsWith("arn:")) {
          const parsed = parseBedrockModelRef(value);
          if (parsed.kind === "invalid") return; // malformed ARN: never import it
          pushFinding(out, source, "bedrock", "model", parsed.ref, parsed.description || "Bedrock model ARN.");
        } else {
          pushFinding(out, source, "unknown", "model", value, `Model id (${key.toLowerCase()}).`);
        }
      }
      return;
  }
}

/** Parse ~/.claude/settings.json and settings.local.json (also ./.claude/). */
export function parseClaudeSettings(text: string, label: string): ImportFinding[] {
  const out: ImportFinding[] = [];
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    return out;
  }
  if (typeof doc !== "object" || doc === null) return out;
  const rec = doc as Record<string, unknown>;
  const env = rec["env"];
  if (typeof env === "object" && env !== null) {
    for (const [k, v] of Object.entries(env as Record<string, unknown>)) {
      if (typeof v === "string") handleEnvPair(out, label, k, v);
      else if (typeof v === "number" || typeof v === "boolean") {
        // non-string env values carry no connection facts we use
      } else if (v !== null && v !== undefined) {
        credentialRef(out, label, k.toLowerCase());
      }
    }
  }
  const topModel = rec["model"];
  if (typeof topModel === "string" && topModel.trim()) {
    handleEnvPair(out, label, "MODEL", topModel);
  }
  return out;
}

/** Parse a .env / .env.local file. */
export function parseDotEnv(text: string, label: string): ImportFinding[] {
  const out: ImportFinding[] = [];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const body = line.startsWith("export ") ? line.slice(7).trim() : line;
    const eq = body.indexOf("=");
    if (eq <= 0) continue;
    let value = body.slice(eq + 1).trim();
    // strip trailing inline comments for unquoted values
    if (!value.startsWith('"') && !value.startsWith("'")) {
      const hash = value.indexOf(" #");
      if (hash >= 0) value = value.slice(0, hash).trim();
    }
    handleEnvPair(out, label, body.slice(0, eq), value);
  }
  return out;
}

/** Strip // and block comments from JSONC (string-aware). */
export function stripJsonc(text: string): string {
  let out = "";
  let i = 0;
  let inStr: string | null = null;
  while (i < text.length) {
    const c = text[i];
    if (inStr) {
      out += c;
      if (c === "\\" && i + 1 < text.length) {
        out += text[i + 1];
        i += 2;
        continue;
      }
      if (c === inStr) inStr = null;
      i++;
      continue;
    }
    if (c === '"' || c === "'") {
      inStr = c;
      out += c;
      i++;
      continue;
    }
    if (c === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") i++;
      continue;
    }
    if (c === "/" && text[i + 1] === "*") {
      i += 2;
      while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) i++;
      i += 2;
      continue;
    }
    out += c;
    i++;
  }
  return out;
}

/** Parse Kilo CLI kilo.jsonc / opencode.json provider configs. */
export function parseKiloConfig(text: string, label: string): ImportFinding[] {
  const out: ImportFinding[] = [];
  let doc: unknown;
  try {
    doc = JSON.parse(stripJsonc(text));
  } catch {
    return out;
  }
  if (typeof doc !== "object" || doc === null) return out;
  const rec = doc as Record<string, unknown>;

  const providers = rec["provider"];
  if (typeof providers === "object" && providers !== null) {
    for (const [pid, pdef] of Object.entries(providers as Record<string, unknown>)) {
      if (typeof pdef !== "object" || pdef === null) continue;
      const opts = (pdef as Record<string, unknown>)["options"];
      const pname = String((pdef as Record<string, unknown>)["name"] ?? pid);
      if (typeof opts === "object" && opts !== null) {
        const orec = opts as Record<string, unknown>;
        const baseURL = orec["baseURL"];
        if (typeof baseURL === "string" && /^https?:\/\//i.test(baseURL.trim())) {
          pushFinding(
            out, label, classifyEndpoint(baseURL), "endpoint", baseURL.trim(),
            `Kilo provider "${pname}" base URL.`
          );
        }
        if (orec["apiKey"] !== undefined) {
          credentialRef(out, label, `provider "${pname}" apiKey`);
        }
      }
      const models = (pdef as Record<string, unknown>)["models"];
      if (typeof models === "object" && models !== null) {
        for (const mid of Object.keys(models as Record<string, unknown>)) {
          if (mid && !isSecretKeyName(mid)) {
            pushFinding(out, label, "unknown", "model", mid, `Kilo provider "${pname}" model.`);
          }
        }
      }
    }
  }

  const topModel = rec["model"];
  if (typeof topModel === "string") {
    // Kilo CLI format: "provider/model-id"
    const slash = topModel.indexOf("/");
    const mid = slash >= 0 ? topModel.slice(slash + 1) : topModel;
    if (mid.trim()) {
      handleEnvPair(out, label, "MODEL", mid.trim());
    }
  }
  return out;
}

// ------------------------------------------------------------------ scan

/**
 * Candidate source files, in display order. NOTE: Kilo CLI's auth.json and
 * the Kilo VS Code extension's globalState/SecretStorage are deliberately
 * absent — see the module header.
 */
export function planSources(homeDir: string, workspaceDir: string | null): ScanCandidate[] {
  const c: ScanCandidate[] = [
    { absPath: `${homeDir}/.claude/settings.json`, label: "~/.claude/settings.json", kind: "claude" },
    { absPath: `${homeDir}/.claude/settings.local.json`, label: "~/.claude/settings.local.json", kind: "claude" },
    { absPath: `${homeDir}/.config/kilo/kilo.jsonc`, label: "~/.config/kilo/kilo.jsonc", kind: "kilo" },
    { absPath: `${homeDir}/.config/kilo/opencode.json`, label: "~/.config/kilo/opencode.json", kind: "kilo" },
  ];
  if (workspaceDir) {
    c.push(
      { absPath: `${workspaceDir}/.claude/settings.json`, label: "<project>/.claude/settings.json", kind: "claude" },
      { absPath: `${workspaceDir}/.claude/settings.local.json`, label: "<project>/.claude/settings.local.json", kind: "claude" },
      { absPath: `${workspaceDir}/.kilo/kilo.jsonc`, label: "<project>/.kilo/kilo.jsonc", kind: "kilo" },
      { absPath: `${workspaceDir}/kilo.jsonc`, label: "<project>/kilo.jsonc", kind: "kilo" },
      { absPath: `${workspaceDir}/.env`, label: "<project>/.env", kind: "dotenv" },
      { absPath: `${workspaceDir}/.env.local`, label: "<project>/.env.local", kind: "dotenv" }
    );
  }
  return c;
}

/**
 * Scan candidate files. `read` is injected (the extension passes a real
 * fs reader ONLY after the user consents) so tests can use a fake.
 * Nothing outside the planned candidate paths is ever read.
 */
export function scanSources(
  read: (absPath: string) => string | null,
  homeDir: string,
  workspaceDir: string | null
): ScanResult {
  const scanned: string[] = [];
  const missing: string[] = [];
  const findings: ImportFinding[] = [];
  for (const cand of planSources(homeDir, workspaceDir)) {
    const text = read(cand.absPath);
    if (text === null) {
      missing.push(cand.label);
      continue;
    }
    scanned.push(cand.label);
    const parsed =
      cand.kind === "claude"
        ? parseClaudeSettings(text, cand.label)
        : cand.kind === "dotenv"
          ? parseDotEnv(text, cand.label)
          : parseKiloConfig(text, cand.label);
    findings.push(...parsed);
  }
  return { scanned, missing, findings: dedupeFindings(findings) };
}

function dedupeFindings(findings: ImportFinding[]): ImportFinding[] {
  const seen = new Set<string>();
  const out: ImportFinding[] = [];
  for (const f of findings) {
    const k = `${f.kind}|${f.provider}|${f.value}`;
    if (seen.has(k)) {
      const prev = out.find((x) => `${x.kind}|${x.provider}|${x.value}` === k);
      if (prev && !prev.source.includes(f.source)) prev.source += `, ${f.source}`;
      continue;
    }
    seen.add(k);
    out.push({ ...f });
  }
  return out;
}

// ----------------------------------------------------------------- apply

/** Findings the user can actually apply (informational kinds excluded). */
export function applicableFindings(findings: ImportFinding[]): ImportFinding[] {
  return findings.filter((f) => f.kind === "region" || f.kind === "endpoint" || f.kind === "model");
}

/** Findings that are information-only (shown, never written). */
export function informationalFindings(findings: ImportFinding[]): ImportFinding[] {
  return findings.filter((f) => f.kind === "awsProfile" || f.kind === "credentialRef");
}

/** Turn selected findings into concrete awino.* config writes. Pure. */
export function buildConfigWrites(selected: ImportFinding[]): ConfigWrite[] {
  const writes: ConfigWrite[] = [];
  const seen = new Set<string>();
  const add = (key: ConfigWrite["key"], value: string) => {
    const k = `${key}=${value}`;
    if (seen.has(k)) return;
    seen.add(k);
    writes.push({ key, value });
  };
  for (const f of selected) {
    if (f.kind === "region") {
      add("bedrockRegion", f.value);
      if (f.provider === "bedrock") add("provider", "bedrock");
    } else if (f.kind === "endpoint") {
      add("provider", f.provider === "unknown" ? "openai" : f.provider);
      add("endpoint", f.value);
    } else if (f.kind === "model") {
      add("provider", f.provider === "unknown" ? "openai" : f.provider);
      add("model", f.value);
    }
  }
  return writes;
}

/** One-line-per-write summary for the confirm dialog. */
export function summarizeWrites(writes: ConfigWrite[]): string {
  return writes.map((w) => `${w.key} = ${w.value}`).join("\n");
}
