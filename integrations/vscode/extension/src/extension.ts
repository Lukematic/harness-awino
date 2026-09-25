/**
 * extension.ts — Awino VS Code extension host.
 *
 * The extension is a surface. The Python sidecar owns the turn loop:
 * every model-proposed action flows through Loop.run_user_turn in the
 * sidecar process. This file spawns the sidecar, routes its §4 events
 * into the chat webview / tree views / status bar, and forwards only
 * the §4 command verbs back. It never constructs turns or calls tools.
 */

import * as vscode from "vscode";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawn } from "child_process";
import { SidecarClient, SidecarEvent, defaultSidecarPath } from "./sidecar";
import { resolvePythonInterpreter, describeSpawnFailure, isInterpreterNotFound, ResolvedInterpreter } from "./python";
import { bundledRuntimePath, prepareBundledRuntime } from "./bundledPython";
import { offerPythonRecovery, pickPythonPathWriteLevel } from "./pythonRecovery";
import { ConnectGuard } from "./connectGuard";
import { keyMissingForProvider } from "./providerKeys";
import { discoverModels } from "./modelDiscovery";
import { ChatHistory } from "./chatHistory";
import {
  BEDROCK_REGIONS,
  BedrockAuthMode,
  bedrockEndpointForRegion,
  isValidRegion,
  parseAwsProfileNames,
  parseBedrockModelRef,
  resolveBedrockConnection,
  probeBedrockModels,
  validateBedrockSetup,
} from "./bedrock";
import {
  scanSources,
  applicableFindings,
  informationalFindings,
  buildConfigWrites,
  summarizeWrites,
  formatScannedLine,
  ImportFinding,
} from "./connection_importer";
import {
  QueryFn,
  ContractView,
  JournalView,
  LearningsView,
  SkillsView,
  ContextView,
  ModesView,
  TasksView,
} from "./views";

const EXT_ID = "awino-loop-owner";
const KEY_OPENAI = "awino.apiKey.openai"; // -> AWINO_API_KEY
const KEY_ANTHROPIC = "awino.apiKey.anthropic"; // -> ANTHROPIC_API_KEY
const KEY_BEDROCK = "awino.apiKey.bedrock"; // Bedrock API key -> AWINO_API_KEY (Bearer)

// ------------------------------------------------------------------ secrets
// SecretStorage can hang indefinitely on machines without a working OS
// keyring (observed: store() never resolves under headless Xvfb). A hanging
// store with no timeout and no error surface = a dead "Save & Connect"
// button. Every secret write/delete goes through these helpers: they race
// the operation against a timeout and throw a human-readable error the
// caller must surface in the UI (wizard error line, notification, panel).
const SECRET_OP_TIMEOUT_MS = 15000;

function secretTimeoutError(op: string): Error {
  return new Error(
    `Timed out ${op} the API key in secret storage after ${SECRET_OP_TIMEOUT_MS / 1000}s. ` +
      `Your system keyring may be unavailable — set the key via the ` +
      `AWINO_API_KEY / ANTHROPIC_API_KEY environment variable instead, or fix the keyring and retry.`
  );
}

async function storeSecret(
  secrets: vscode.SecretStorage,
  key: string,
  value: string
): Promise<void> {
  await Promise.race([
    secrets.store(key, value),
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(secretTimeoutError("storing")), SECRET_OP_TIMEOUT_MS)
    ),
  ]);
}

async function deleteSecret(secrets: vscode.SecretStorage, key: string): Promise<void> {
  await Promise.race([
    secrets.delete(key),
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(secretTimeoutError("deleting")), SECRET_OP_TIMEOUT_MS)
    ),
  ]);
}

// ------------------------------------------------------------------ config

interface AwinoConfig {
  provider: string;
  endpoint: string;
  model: string;
  /** AWS region for provider "bedrock"; endpoint is derived from it. */
  bedrockRegion: string;
  /** Auth mode for provider "bedrock": "api-key" or "aws-profile" (SigV4). */
  bedrockAuthMode: BedrockAuthMode;
  /** AWS profile name for provider "bedrock" in "aws-profile" mode. */
  bedrockAwsProfile: string;
  timeout: number;
  pythonPath: string;
  mcpServers: Array<{ name: string; command: string; args?: string[]; env?: Record<string, string> }>;
  /**
   * Friendly, non-secret labels for API keys (e.g. { openai: "Work" }).
   * Labels live in settings; the keys themselves stay in SecretStorage.
   */
  keyLabels: Record<string, string>;
  /** TEST ONLY: scripted turns for the scripted provider. */
  script?: unknown[];
}

function readKeyLabels(): Record<string, string> {
  const raw = vscode.workspace.getConfiguration("awino").get<unknown>("keyLabels", {});
  const out: Record<string, string> = {};
  if (raw && typeof raw === "object") {
    for (const k of ["openai", "anthropic", "bedrock"]) {
      const v = (raw as Record<string, unknown>)[k];
      if (typeof v === "string" && v.trim()) {
        out[k] = v.trim().slice(0, 40);
      }
    }
  }
  return out;
}

function readBedrockAuthMode(
  c: vscode.WorkspaceConfiguration
): BedrockAuthMode {
  const raw = c.get<string>("bedrockAuthMode", "api-key");
  return raw === "aws-profile" ? "aws-profile" : "api-key";
}

function readConfig(): AwinoConfig {
  const c = vscode.workspace.getConfiguration("awino");
  return {
    provider: c.get<string>("provider", "echo"),
    endpoint: c.get<string>("endpoint", ""),
    model: c.get<string>("model", ""),
    bedrockRegion: c.get<string>("bedrockRegion", ""),
    bedrockAuthMode: readBedrockAuthMode(c),
    bedrockAwsProfile: c.get<string>("bedrockAwsProfile", ""),
    timeout: c.get<number>("timeout", 180),
    pythonPath: c.get<string>("pythonPath", "python3"),
    mcpServers: c.get<Array<{ name: string; command: string; args?: string[]; env?: Record<string, string> }>>(
      "mcpServers",
      []
    ),
    script: c.get<unknown[]>("script") ?? undefined,
    keyLabels: readKeyLabels(),
  };
}

// ------------------------------------------- providers.yaml (strict subset)

interface EnvBinding {
  provider?: string;
  model?: string;
  endpoint?: string;
  key_id?: string;
  [k: string]: string | undefined;
}

/** Minimal parser mirroring the sidecar's strict YAML subset — enough to
 *  list environment names for the switcher. Returns null on parse failure. */
function parseProvidersYaml(text: string): { environments: Record<string, EnvBinding>; default_environment?: string } | null {
  const root: Record<string, unknown> = {};
  const stack: Array<{ indent: number; obj: Record<string, unknown> }> = [{ indent: -1, obj: root }];
  for (const raw of text.split("\n")) {
    const line = raw.replace(/ #.*$/, "");
    if (!line.trim() || line.trim().startsWith("#") || line.includes("\t")) {
      continue;
    }
    const indent = line.length - line.trimStart().length;
    const stripped = line.trim();
    const ci = stripped.indexOf(":");
    if (ci < 0) {
      return null;
    }
    const key = stripped.slice(0, ci).trim();
    let value = stripped.slice(ci + 1).trim();
    if (!key || key.includes(" ")) {
      return null;
    }
    while (stack.length && indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }
    const parent = stack[stack.length - 1].obj;
    if (value === "") {
      const child: Record<string, unknown> = {};
      parent[key] = child;
      stack.push({ indent, obj: child });
    } else {
      if (value.length >= 2 && value[0] === value[value.length - 1] && "\"'".includes(value[0])) {
        value = value.slice(1, -1);
      }
      parent[key] = value;
    }
  }
  const envs = root["environments"];
  if (!envs || typeof envs !== "object") {
    return { environments: {} };
  }
  return {
    environments: envs as Record<string, EnvBinding>,
    default_environment: typeof root["default_environment"] === "string" ? (root["default_environment"] as string) : undefined,
  };
}

function listEnvironments(workspaceFolder: string): string[] {
  try {
    const p = path.join(workspaceFolder, ".awino", "providers.yaml");
    const text = fs.readFileSync(p, "utf8");
    const parsed = parseProvidersYaml(text);
    return parsed ? Object.keys(parsed.environments) : [];
  } catch {
    return [];
  }
}

// ------------------------------------------------------------------ session

interface Session {
  client: SidecarClient;
  alwaysAllow: Set<string>;
  ready: SidecarEvent | null;
  lastStatus: Record<string, unknown> | null;
  /** User-facing provider label ("bedrock") when it differs from what the
   *  sidecar was told (the sidecar only speaks openai/anthropic/ollama/echo,
   *  so Bedrock rides its OpenAI-compatible backend — see bedrock.ts). */
  displayProvider?: string;
}

// Webview handles live outside the session so a reconnect (sidecar restart)
// doesn't orphan the already-resolved chat/models views.
let chatPanel: vscode.WebviewView | null = null;
let modelsPanel: vscode.WebviewPanel | null = null;

// Transcript persistence: the chat webview keeps its transcript in DOM/JS
// memory only, and VS Code may dispose a hidden sidebar WebviewView (e.g.
// the user switches to the Explorer tab). Every transcript message posted to
// the chat is mirrored here so the "chatReady" handshake can replay it when
// the view (re)loads. Chrome messages (state, binding, session-resume) skip
// persistence — the view re-sends them fresh on every handshake.
const chatHistory = new ChatHistory();

// Module-level extension context for event handlers (e.g. the prove-it
// flow in onSidecarEvent) that don't receive it as a parameter.
let extContext: vscode.ExtensionContext | null = null;

let session: Session | null = null;
let statusBar: vscode.StatusBarItem;
// Rigor coach surface (Honda): a separate small status item for the last
// mission's RigorScore — never touches the primary provider/connection
// status bar above. Hidden while disconnected (truthful: no score to show).
let rigorBar: vscode.StatusBarItem;
let rigorChannel: vscode.OutputChannel | null = null;
let output: vscode.OutputChannel;

// Spec 1.4: cached mode list for the chat header mode selector. Refreshed
// on connect and after mode_invoke; cleared on disconnect. Included in
// postChatState so the dropdown renders without an extra round-trip.
let cachedModes: Array<{ id: string; label: string }> = [];
let cachedActiveMode = "";

async function refreshModesCache(): Promise<void> {
  if (!session?.ready) {
    cachedModes = [];
    cachedActiveMode = "";
    return;
  }
  try {
    const r = (await query("mode_list")) as {
      modes?: Array<{ id: string; label: string }>;
      active?: { id?: string };
    };
    cachedModes = (r.modes ?? []).map((m) => ({ id: m.id, label: m.label || m.id }));
    cachedActiveMode = r.active?.id ?? "";
  } catch {
    // Best effort — the dropdown just stays hidden.
    cachedModes = [];
    cachedActiveMode = "";
  }
  postToChat({ type: "modesList", modes: cachedModes, activeMode: cachedActiveMode });
}

function log(s: string): void {
  output.appendLine(`[awino] ${s}`);
}

// ------------------------------------------------- sidecar query plumbing

type Waiter = { name: string; id: string; resolve: (r: unknown) => void; reject: (e: Error) => void; timer: NodeJS.Timeout };
let waiters: Waiter[] = [];
let querySeq = 0;

/** Send a `command` verb and resolve with the matching command_result. */
function query(name: string, args: Record<string, unknown> = {}): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (!session) {
      reject(new Error("not connected"));
      return;
    }
    // Unique request id per query: the sidecar echoes it in command_result
    // so concurrent same-name queries resolve the right waiter instead of
    // matching on the command name alone (which could misroute).
    const id = `q${++querySeq}`;
    const timer = setTimeout(() => {
      waiters = waiters.filter((w) => w !== waiter);
      reject(new Error(`timed out waiting for command_result:${name} (id ${id})`));
    }, 120_000);
    const waiter: Waiter = { name, id, resolve, reject, timer };
    waiters.push(waiter);
    try {
      session.client.command(name, args, id);
    } catch (e) {
      clearTimeout(timer);
      waiters = waiters.filter((w) => w !== waiter);
      reject(e instanceof Error ? e : new Error(String(e)));
    }
  });
}

function routeCommandResult(ev: SidecarEvent): void {
  const evId = ev["id"];
  // Prefer the echoed request id; fall back to name matching for results
  // that carry none (older sidecars, or error paths that never saw one).
  const idx = waiters.findIndex((w) =>
    evId !== undefined && evId !== null ? w.id === String(evId) : w.name === String(ev.name)
  );
  if (idx >= 0) {
    const [w] = waiters.splice(idx, 1);
    clearTimeout(w.timer);
    w.resolve(ev.result);
  } else {
    postToChat({ type: "event", payload: ev });
  }
}

// ------------------------------------------------------------------ views

let contractView: ContractView;
let journalView: JournalView;
let learningsView: LearningsView;
let skillsView: SkillsView;
let contextView: ContextView;
let modesView: ModesView;
let tasksView: TasksView;

function refreshViews(): void {
  contractView?.refresh();
  journalView?.refresh();
  learningsView?.refresh();
  skillsView?.refresh();
  contextView?.refresh();
  modesView?.refresh();
  tasksView?.refresh();
}

function updateStatusBar(): void {
  // Spec 1.2: tree views (modes/tasks/contract/...) are gated on
  // `awino:connected` — only the chat view shows before the sidecar is live.
  void vscode.commands.executeCommand(
    "setContext",
    "awino:connected",
    !!session?.ready
  );
  if (!session?.ready) {
    statusBar.text = "$(circle-slash) Awino: not connected";
    statusBar.backgroundColor = undefined;
    statusBar.command = "awino.openModels";
    statusBar.tooltip = lastConnectError
      ? `Awino sidecar is not running\n\n${lastConnectError}`
      : "Awino sidecar is not running";
    // Rigor bar stays truthful: no connection, no score.
    rigorBar?.hide();
    return;
  }
  // Spec 3.2/3.4: settings changed since connect — persistent warning
  // surface, one click reconnects. Shows the OLD active binding (the values
  // the sidecar is actually running) with a stale marker — never the new
  // settings values, which aren't live yet (Spec 4.1).
  if (settingsDirty) {
    const binding = (session.ready["binding"] ?? {}) as Record<string, unknown>;
    const provider = String(session.displayProvider ?? binding["provider"] ?? "?");
    const model = String(binding["model"] ?? "?");
    statusBar.text = `$(sync) Awino: ${provider} · ${model} (stale — reconnect to apply)`;
    statusBar.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
    statusBar.command = "awino.reconnect";
    statusBar.tooltip = ["Settings changed since last connect — the sidecar still runs:", `provider: ${provider}`, `model: ${model}`, "", "Changed:", ...settingsDiffLines()].join("\n");
    return;
  }
  statusBar.backgroundColor = undefined;
  statusBar.command = "awino.openModels";
  const binding = (session.ready["binding"] ?? {}) as Record<string, unknown>;
  const mode = (session.lastStatus?.["active_mode"] ?? session.ready["active_mode"] ?? {}) as Record<string, unknown>;
  const provider = String(session.displayProvider ?? binding["provider"] ?? session.ready["provider"] ?? "?");
  const env = String(binding["environment"] ?? "(global)");
  const modeId = String(mode["id"] ?? "?");
  const persona = session.lastStatus?.["persona"] as Record<string, unknown> | null;
  // Binding-source trust: when the active binding did NOT come from the
  // global VS Code settings, say so in the visible text — a project-file or
  // named-environment override must be visible at a glance, not discoverable
  // only in the tooltip.
  const bindingSource = String(binding["source"] ?? "global-settings");
  const sourceLabel =
    bindingSource === "project-file" ? " (project file)"
    : bindingSource === "named-environment" ? " (named env)"
    : "";
  statusBar.text = `$(hubot) ${provider} · ${env}${sourceLabel} · ${modeId}${persona ? ` · ${persona["skill"]}` : ""}`;
  statusBar.tooltip = [
    `provider: ${provider}`,
    `model: ${String(binding["model"] ?? session.ready["model"] ?? "?")}`,
    `environment: ${env} (binding source: ${String(binding["source"] ?? "?")})`,
    `mode: ${modeId} (source: ${String(mode["source"] ?? "?")})`,
    persona ? `persona: ${persona["skill"]} (sha ${String(persona["sha256"]).slice(0, 12)})` : null,
    `key: ${String(binding["key"] ?? "not-required")} — key material lives only in secret storage`,
  ]
    .filter(Boolean)
    .join("\n");
}

// Post a message to the Models & Providers panel if it is open.
function postToModelsPanel(m: Record<string, unknown>): void {
  if (modelsPanel) {
    void modelsPanel.webview.postMessage(m);
  }
}

// Spec 4.1: the single binding publish path. session.binding (live, from
// the sidecar) is authoritative — no surface may derive the active
// provider/model from VS Code settings. Called on connect, reconnect,
// disconnect, binding events, and dirty-state transitions.
function publishBinding(): void {
  const binding = (session?.ready?.["binding"] ?? null) as Record<string, unknown> | null;
  const stale = settingsDirty && !!session?.ready;
  const display = binding
    ? { ...binding, provider: session?.displayProvider ?? binding["provider"] }
    : null;
  updateStatusBar(); // reads session.ready.binding + settingsDirty directly
  postToChat({ type: "bindingChanged", binding: display, settingsDirty: stale }, false);
  postToModelsPanel({ type: "bindingChanged", binding: display, settingsDirty: stale });
  refreshViews(); // tree views re-render from the live session
}

// ------------------------------------------------------------------ chat webview

function postToChat(msg: unknown, persist = true): void {
  if (persist) {
    chatHistory.push(msg);
  }
  chatPanel?.webview.postMessage(msg);
}

// Destinations the webviews may open via the "openExternal" message ("Get
// {Provider} API Key" buttons, "Provider Docs" links). Fixed allowlist —
// the webview never gets a free-form browser.
const EXTERNAL_ALLOWLIST = new Set([
  "platform.openai.com",
  "console.anthropic.com",
  "docs.anthropic.com",
  "console.aws.amazon.com",
  "docs.aws.amazon.com",
  "ollama.com",
]);

function openExternalAllowed(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "https:" && EXTERNAL_ALLOWLIST.has(u.hostname);
  } catch {
    return false;
  }
}

async function openExternal(url: unknown): Promise<void> {
  if (typeof url !== "string" || !openExternalAllowed(url)) {
    log(`openExternal refused: ${String(url).slice(0, 120)}`);
    return;
  }
  await vscode.env.openExternal(vscode.Uri.parse(url));
}

// Single shape for every "state" push to the chat webview: connection,
// binding (provider pill), key-missing (setup card), wizard flag, and the
// bedrock region list for the wizard. `extra` carries per-site fields such
// as connectError.
function postChatState(extra: Record<string, unknown> = {}): void {
  const binding = (session?.ready?.["binding"] ?? {}) as Record<string, unknown>;
  // Chrome, not transcript: the view re-sends fresh state on every resolve.
  postToChat({
    type: "state",
    connected: !!session?.ready,
    ready: session?.ready ?? null,
    status: session?.lastStatus ?? null,
    keyMissing: lastKeyMissing?.missing ?? false,
    provider: lastKeyMissing?.provider ?? "echo",
    model: String(binding["model"] ?? (session?.ready as Record<string, unknown> | null)?.["model"] ?? ""),
    showWizard: lastShowWizard,
    bedrockRegions: BEDROCK_REGIONS,
    modes: cachedModes,
    activeMode: cachedActiveMode,
    settingsDirty: settingsDirty,
    ...extra,
  }, false);
}

// Model discovery shared by the Models & Providers panel ("fetchModels")
// and the onboarding wizard ("wizardFetch"). Failures never throw — the
// caller renders the plain-language reason and keeps manual entry.
async function runModelDiscovery(
  context: vscode.ExtensionContext,
  provider: string,
  endpoint: string,
  key: string | undefined
): Promise<{ ok: boolean; models: string[]; error?: string }> {
  let k = key;
  if (!k && provider === "openai") {
    k = (await context.secrets.get(KEY_OPENAI)) ?? undefined;
  }
  try {
    return await discoverModels(provider, endpoint || undefined, k);
  } catch (e) {
    return { ok: false, models: [], error: e instanceof Error ? e.message : String(e) };
  }
}

class ChatViewProvider implements vscode.WebviewViewProvider {
  constructor(private ctx: vscode.ExtensionContext) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    chatPanel = view;
    const webviewDir = vscode.Uri.file(path.join(this.ctx.extensionPath, "webview"));
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [webviewDir],
    };
    view.webview.html = loadWebviewHtml(this.ctx, "chat.html")
      .replace(/\{\{CSP_SOURCE\}\}/g, view.webview.cspSource)
      .replace(
        "{{SETUP_SHARED_JS}}",
        String(view.webview.asWebviewUri(vscode.Uri.joinPath(webviewDir, "setup-shared.js")))
      )
      .replace(
        "{{CHAT_JS}}",
        String(view.webview.asWebviewUri(vscode.Uri.joinPath(webviewDir, "chat.js")))
      );
    view.webview.onDidReceiveMessage((m) => void handleChatMessage(this.ctx, m));
    view.onDidDispose(() => {
      chatPanel = null;
    });
    // Transcript/state delivery happens on the webview's "chatReady"
    // handshake (see handleChatMessage): the view's scripts must be loaded
    // before postMessage can be received, so nothing is pushed here.
  }
}

function loadWebviewHtml(ctx: vscode.ExtensionContext, file: string): string {
  const p = path.join(ctx.extensionPath, "webview", file);
  return fs.readFileSync(p, "utf8");
}

async function handleChatMessage(
  context: vscode.ExtensionContext,
  m: { type: string; [k: string]: unknown }
): Promise<void> {
  // "chatReady": the webview's scripts are loaded and its message listener
  // is live. (Re)deliver everything the view needs: the retained transcript
  // first (tab switches must not wipe the session view), then fresh chrome
  // (header pill, resume block) rendered from current host state.
  if (m.type === "chatReady") {
    chatPanel?.webview.postMessage({ type: "beginReplay" });
    chatHistory.replay((msg) => chatPanel?.webview.postMessage(msg));
    chatPanel?.webview.postMessage({ type: "endReplay" });
    postChatState();
    if (session?.ready) {
      await postSessionResume();
    }
    return;
  }
  // "models" opens the Models & Providers panel and needs no session — it is
  // the escape hatch when there is no model connected (e.g. missing API key).
  if (m.type === "models") {
    await vscode.commands.executeCommand("awino.openModels");
    return;
  }
  // "openExternal" opens allowlisted provider pages (key creation, docs) and
  // needs no session either — the wizard runs before any connection.
  if (m.type === "openExternal") {
    await openExternal(m.url);
    return;
  }
  // Wizard dismissal via "Skip for now": Spec 2.2 — selects Echo (the safe
  // local demo) and completes onboarding. The provider is written explicitly
  // so a skipped onboarding can never leave a half-configured provider.
  if (m.type === "wizardDismiss") {
    const cfg = vscode.workspace.getConfiguration("awino");
    await cfg.update("provider", "echo", vscode.ConfigurationTarget.Workspace);
    await context.globalState.update("awino.onboarded", true);
    wizardAwaitingProve = false;
    lastShowWizard = computeShowWizard(context);
    postChatState();
    return;
  }
  // Model discovery from the wizard (same backend as the panel's fetch).
  if (m.type === "wizardFetch") {
    const r = await runModelDiscovery(
      context,
      String(m.provider ?? "openai"),
      String(m.endpoint ?? "").trim(),
      typeof m.key === "string" && m.key ? m.key : undefined
    );
    postToChat({ type: "wizardModels", ok: r.ok, models: r.models, error: r.error ?? null });
    return;
  }
  // Wizard completion: store the key (SecretStorage) + label + provider
  // settings, mark onboarding done, reconnect.
  if (m.type === "wizardSave") {
    await saveWizardSettings(context, m);
    return;
  }
  // Spec 2.1 Step 3 ("Prove it"): send the fixed test message through the
  // live sidecar. The next turn_result/error settles awaitingProveIt.
  if (m.type === "wizardProve") {
    if (!session) {
      postToChat({ type: "wizardProveFailed", error: "not connected" });
      return;
    }
    awaitingProveIt = true;
    session.client.userMessage("Hello — reply in one short sentence.");
    return;
  }
  if (!session) {
    return;
  }
  switch (m.type) {
    case "send":
      session.client.userMessage(String(m.text ?? ""));
      break;
    case "stop":
      session.client.cancel();
      break;
    // Spec 1.4: chat header buttons.
    case "newMission":
      void vscode.commands.executeCommand("awino.newMission");
      break;
    case "invokeModeSelect":
      void invokeModeFlow(typeof m.mode === "string" ? m.mode : undefined);
      break;
    case "approve":
      log(`webview approve: id=${String(m.id)} decision=${m.decision}`);
      session.client.approve(String(m.id), m.decision === "deny" ? "deny" : "approve");
      break;
    default:
      log(`unknown chat message type: ${m.type}`);
  }
}

// Onboarding wizard completion: the key goes to SecretStorage (never
// settings JSON); the label is non-secret and lives in settings next to the
// other awino.* values. Then onboarding is marked done and the sidecar
// reconnects with the new binding.
async function saveWizardSettings(
  context: vscode.ExtensionContext,
  m: { type: string; [k: string]: unknown }
): Promise<void> {
  const cfg = vscode.workspace.getConfiguration("awino");
  let provider = String(m.provider ?? "echo");
  // Spec 2.1: "OpenAI-compatible (custom endpoint)" is a wizard-level
  // distinction — the sidecar speaks the OpenAI chat API for both, so it
  // maps to the "openai" backend with the user-supplied endpoint.
  if (provider === "openai-compatible") {
    provider = "openai";
  }
  const key = String(m.key ?? "");
  const keyed = provider === "openai" || provider === "anthropic" || provider === "bedrock";
  if (keyed && key) {
    const target =
      provider === "anthropic" ? KEY_ANTHROPIC : provider === "bedrock" ? KEY_BEDROCK : KEY_OPENAI;
    // Fail visibly, not silently: a hanging/failing secret store must not
    // leave the wizard on a dead "Save & Connect" click. The webview shows
    // the error inline and re-enables the button.
    try {
      await storeSecret(context.secrets, target, key);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`wizardSave: secret store failed: ${msg}`);
      postToChat({ type: "wizardSaveFailed", error: msg });
      return;
    }
    const label = String(m.keyLabel ?? "").trim().slice(0, 40);
    if (label) {
      const labels = readKeyLabels();
      labels[provider] = label;
      await cfg.update("keyLabels", labels, vscode.ConfigurationTarget.Workspace);
    }
  }
  await cfg.update("provider", provider, vscode.ConfigurationTarget.Workspace);
  // Spec 2.1: OpenAI gets its default endpoint unless the wizard supplied one.
  let endpoint = String(m.endpoint ?? "");
  if (provider === "openai" && !endpoint && String(m.provider ?? "") === "openai") {
    endpoint = "https://api.openai.com/v1";
  }
  await cfg.update("endpoint", endpoint, vscode.ConfigurationTarget.Workspace);
  await cfg.update("model", String(m.model ?? ""), vscode.ConfigurationTarget.Workspace);
  if (provider === "bedrock" && typeof m.bedrockRegion === "string" && m.bedrockRegion) {
    await cfg.update("bedrockRegion", m.bedrockRegion, vscode.ConfigurationTarget.Workspace);
  }
  if (provider === "bedrock") {
    await cfg.update(
      "bedrockAuthMode",
      m.bedrockAuthMode === "aws-profile" ? "aws-profile" : "api-key",
      vscode.ConfigurationTarget.Workspace
    );
    await cfg.update("bedrockAwsProfile", String(m.bedrockAwsProfile ?? ""), vscode.ConfigurationTarget.Workspace);
  }
  // Spec 2.1 Step 3 ("Prove it"): do NOT mark onboarded yet. The wizard
  // advances to the prove-it step; onboarding completes only after a
  // successful test-message reply (see wizardProve/awaitingProveIt).
  // Echo is the exception: it has no backend to prove, so it completes now.
  if (provider === "echo") {
    await context.globalState.update("awino.onboarded", true);
  }
  vscode.window.showInformationMessage("Awino: provider saved — reconnecting sidecar…");
  // Non-echo providers advance to the prove-it step: keep the wizard open
  // across the reconnect (computeShowWizard honors this flag).
  wizardAwaitingProve = provider !== "echo";
  await connect(context);
  // connect() re-pushes chat state on every path; this covers its no-folder
  // early return so the wizard always hides after Done.
  lastShowWizard = computeShowWizard(context);
  postChatState();
  if (provider !== "echo" && session?.ready) {
    postToChat({ type: "wizardProveReady" });
  }
}

// ------------------------------------------------------- event routing

async function onSidecarEvent(ev: SidecarEvent): Promise<void> {
  switch (ev.event) {
    case "ready":
      if (session) {
        session.ready = ev;
      }
      // Spec 3: snapshot sidecar-affecting settings at connect. A later
      // divergence sets the dirty flag ("reconnect to apply").
      lastConnectSettings = settingsSnapshot();
      lastConnectSettingsHash = JSON.stringify(lastConnectSettings);
      settingsDirty = false;
      settingsDirtyNotified = false;
      void vscode.commands.executeCommand("setContext", "awino:settingsDirty", false);
      updateStatusBar();
      publishBinding(); // Spec 4.2: all surfaces from the new live binding
      postToChat({ type: "event", payload: ev });
      await refreshStatus();
      postChatState(); // after refreshStatus: the header needs fresh status
      await postSessionResume(); // session-focus summary on (re)connect
      refreshViews(); // populate tree views on connect, not just after the first turn
      void refreshModesCache(); // Spec 1.4: header mode selector options
      break;
    case "turn_result": {
      const result = (ev["result"] ?? {}) as Record<string, unknown>;
      // Spec 2.1 Step 3: a turn result (without an error) proves the
      // provider works — complete onboarding.
      if (awaitingProveIt) {
        awaitingProveIt = false;
        if (!result["error"]) {
          if (extContext) {
            await extContext.globalState.update("awino.onboarded", true);
          }
          wizardAwaitingProve = false; // prove-it done — the wizard may hide now
          lastShowWizard = false; // onboarded → wizard never shows again
          postToChat({ type: "wizardProved" });
          postChatState();
        } else {
          postToChat({ type: "wizardProveFailed", error: String(result["error"]) });
        }
      }
      if (session) {
        // Merge into lastStatus, never replace: a partial turn_result must
        // not wipe fields (mission, phase) that only refreshStatus()
        // repopulates — replacing blanks the header transiently between the
        // two. Only defined values are merged so a missing key can't
        // clobber a known one with undefined.
        const merged: Record<string, unknown> = { ...(session.lastStatus ?? {}) };
        for (const k of ["active_mode", "persona", "provider", "phase"] as const) {
          const v = result[k];
          if (v !== undefined) {
            merged[k] = v;
          }
        }
        session.lastStatus = merged;
      }
      updateStatusBar();
      postToChat({ type: "event", payload: ev });
      await refreshStatus();
      postChatState(); // header must reflect the post-turn status
      refreshViews();
      break;
    }
    case "approval_requested":
      await handleApprovalRequested(ev);
      break;
    case "compaction_proposed":
      await handleCompactionProposed(ev);
      break;
    case "command_result":
      routeCommandResult(ev);
      break;
    case "cancel_ack":
      postToChat({ type: "event", payload: ev });
      break;
    case "warning":
      vscode.window.showWarningMessage(`Awino: ${String(ev.message)}`);
      postToChat({ type: "event", payload: ev });
      break;
    case "error":
      log(`sidecar error: ${String(ev.message)}`);
      // Spec 2.1 Step 3: a turn error fails the prove-it step.
      if (awaitingProveIt) {
        awaitingProveIt = false;
        postToChat({ type: "wizardProveFailed", error: String(ev.message ?? "unknown error") });
      }
      if (ev.fatal === true) {
        // Fatal: the sidecar process is dead (spawn failure or unexpected
        // exit). It must not leave the UI falsely "connected": reject the
        // pending queries (same as disconnect()), clear the session, and
        // push the disconnected state to the status bar, chat, and views.
        waiters.forEach((w) => {
          clearTimeout(w.timer);
          w.reject(new Error("disconnected"));
        });
        waiters = [];
        session = null;
        publishBinding(); // Spec 4.2: disconnected binding to all surfaces
        postChatState();
        postToChat({ type: "event", payload: ev });
        void vscode.window
          .showErrorMessage(`Awino: sidecar error — ${String(ev.message)}.`, "Reconnect")
          .then((choice) => {
            if (choice === "Reconnect") {
              void vscode.commands.executeCommand("awino.reconnect");
            }
          });
      } else {
        // Non-fatal: protocol or stream errors — the process is alive, only
        // the command failed. Surface it in the chat, keep the session.
        postToChat({ type: "event", payload: ev });
      }
      break;
    case "bye":
      break;
    default:
      postToChat({ type: "event", payload: ev });
  }
}

async function refreshStatus(): Promise<void> {
  if (!session) {
    return;
  }
  try {
    const r = (await query("status")) as Record<string, unknown>;
    session.lastStatus = { ...(session.lastStatus ?? {}), ...(r["status"] as Record<string, unknown>) };
    updateStatusBar();
    await refreshRigorBar();
  } catch (e) {
    log(`status refresh failed: ${e}`);
  }
}

// Rigor coach: show the last mission's RigorScore in the dedicated bar.
// Reads the latest journaled rigor_report event — no recompute, no journal
// write. Truthful when disconnected: the bar hides.
async function refreshRigorBar(): Promise<void> {
  if (!rigorBar) {
    return;
  }
  if (!session?.ready) {
    rigorBar.hide();
    return;
  }
  try {
    const r = (await query("events")) as { events?: Array<{ seq: number; type: string; data: Record<string, unknown> }> };
    const evs = r.events ?? [];
    const last = [...evs].reverse().find((e) => e.type === "rigor_report");
    if (!last) {
      rigorBar.hide();
      return;
    }
    setRigorBar(last.data);
  } catch (e) {
    log(`rigor bar refresh failed: ${e}`);
  }
}

// Exported for node tests (test/rigor.js).
export function setRigorBar(data: Record<string, unknown>): void {
  if (!rigorBar) {
    return;
  }
  const score = Number(data["score"] ?? NaN);
  const failing = (data["failing"] ?? []) as string[];
  const mission = String(data["mission_id"] ?? "?");
  if (!Number.isFinite(score)) {
    rigorBar.hide();
    return;
  }
  const icon = score >= 0.8 ? "$(check)" : score >= 0.5 ? "$(alert)" : "$(x)";
  rigorBar.text = `${icon} Rigor ${score.toFixed(2)}`;
  rigorBar.tooltip = [
    `Awino rigor — mission ${mission}`,
    `RigorScore: ${score.toFixed(2)} (${String(data["scored"] ?? "?")} checks scored)`,
    failing.length ? `Failing: ${failing.join(", ")}` : "No failing checks",
    "",
    "Click to show the full rigor report.",
  ].join("\n");
  rigorBar.show();
}

function rigorOutput(): vscode.OutputChannel {
  if (!rigorChannel) {
    rigorChannel = vscode.window.createOutputChannel("Awino Rigor");
  }
  return rigorChannel;
}

// Exported for node tests (test/rigor.js): pure rendering, no vscode
// calls beyond what the caller supplies.
export function renderJournaledRigor(data: Record<string, unknown>): string {
  const checks = (data["checks"] ?? {}) as Record<string, { status?: string }>;
  const lines = [
    `Rigor report — mission ${String(data["mission_id"] ?? "?")} (from journal)`,
    `RigorScore: ${Number(data["score"] ?? 0).toFixed(2)} (${String(data["scored"] ?? "?")} checks scored)`,
    "",
  ];
  for (const [name, c] of Object.entries(checks)) {
    lines.push(`  [${String(c.status ?? "?")}] ${name}`);
  }
  const failing = (data["failing"] ?? []) as string[];
  if (failing.length) {
    lines.push("", `Failing checks: ${failing.join(", ")}`);
  }
  const overrides = Number(data["overrides"] ?? 0);
  if (overrides > 0) {
    lines.push(`User overrides logged (principal, not penalized): ${overrides}`);
  }
  return lines.join("\n");
}

async function showRigorReport(): Promise<void> {
  // Disconnected truth: no session at all (mustSession throws) and a
  // session that isn't ready both land on the same honest warning —
  // never an unhandled rejection.
  let s: Session | undefined;
  try {
    s = mustSession();
  } catch {
    s = undefined;
  }
  if (!s || !s.ready) {
    vscode.window.showWarningMessage("Awino: not connected — no rigor report available.");
    return;
  }
  const ch = rigorOutput();
  ch.show(true);
  try {
    // Prefer the latest journaled report (no recompute). Generate + journal
    // a fresh one only when none exists yet.
    const r = (await query("events")) as { events?: Array<{ seq: number; type: string; data: Record<string, unknown> }> };
    const last = [...(r.events ?? [])].reverse().find((e) => e.type === "rigor_report");
    if (last) {
      ch.appendLine(renderJournaledRigor(last.data));
      setRigorBar(last.data);
      return;
    }
    const gen = (await query("rigor_report", {})) as Record<string, unknown>;
    if (!gen["ok"]) {
      const detail = String((gen as Record<string, unknown>)["detail"] ?? (gen as Record<string, unknown>)["code"] ?? "unknown");
      ch.appendLine(`Rigor report unavailable: ${detail}`);
      vscode.window.showWarningMessage(`Awino: rigor report unavailable — ${detail}`);
      return;
    }
    const reports = (gen["reports"] ?? []) as Array<Record<string, unknown>>;
    for (const rep of reports) {
      ch.appendLine(String(rep["text"] ?? ""));
      ch.appendLine("");
      setRigorBar({ score: rep["score"], failing: rep["failing"], mission_id: rep["mission_id"], scored: rep["scored"] });
    }
  } catch (e) {
    ch.appendLine(`Rigor report failed: ${e}`);
    vscode.window.showErrorMessage(`Awino: rigor report failed — ${String(e)}`);
  }
}

// Session-focus/resume: read-only reconstruction of where the session
// stands — mission, phase, verified criteria, last progress, next action —
// posted to the chat webview to render as a static summary block. Pure
// read path (session_resume never writes); failures are logged, never
// surfaced as chat noise.
async function postSessionResume(): Promise<void> {
  if (!session) {
    return;
  }
  try {
    const summary = (await query("session_resume")) as Record<string, unknown>;
    // Chrome, not transcript: re-queried fresh on every view resolve.
    postToChat({ type: "sessionResume", summary }, false);
  } catch (e) {
    log(`session resume failed: ${e}`);
  }
}

// ------------------------------------------------------------- approvals

interface ApprovalItem {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  diff?: string;
  old_exists?: boolean;
  // approval-target visibility: cwd-resolved file targets for shell
  // commands, with the out-of-workspace flag (visibility only)
  shell_targets?: {
    effective_cwd?: string;
    targets?: Array<{ raw: string; path: string; in_workspace: boolean; kind: string }>;
    unresolved?: Array<{ raw: string; reason: string }>;
    outside_workspace?: boolean;
  };
}

function formatShellTargets(st: NonNullable<ApprovalItem["shell_targets"]>): string {
  const lines: string[] = [];
  if (st.outside_workspace) {
    lines.push("⚠️ OUTSIDE WORKSPACE — this command addresses paths outside the workspace.");
  }
  lines.push(`resolved targets (cwd: ${st.effective_cwd ?? ""}):`);
  for (const t of st.targets ?? []) {
    lines.push(`  ${t.in_workspace ? "✓" : "⚠️ OUTSIDE"} ${t.path}`);
  }
  for (const u of st.unresolved ?? []) {
    lines.push(`  ? unresolved: ${u.raw} (${u.reason ?? ""})`);
  }
  return lines.join("\n");
}

async function handleApprovalRequested(ev: SidecarEvent): Promise<void> {
  if (!session) {
    return;
  }
  const approvals = (ev["approvals"] ?? []) as ApprovalItem[];
  // render cards in the webview regardless; the modal is the decision path
  postToChat({ type: "event", payload: ev });

  for (const a of approvals) {
    if (session.alwaysAllow.has(a.tool)) {
      log(`auto-approving ${a.tool} (session allow)`);
      session.client.approve(a.id, "approve");
      continue;
    }
    const detail = [
      `tool: ${a.tool}`,
      `args: ${JSON.stringify(a.args, null, 2)}`,
      a.diff ? `\n--- diff ---\n${a.diff}` : "",
      a.shell_targets ? `\n--- shell targets ---\n${formatShellTargets(a.shell_targets)}` : "",
    ]
      .filter(Boolean)
      .join("\n");
    const choice = await vscode.window.showWarningMessage(
      `Awino requests approval: ${a.tool}`,
      { modal: true, detail: detail.slice(0, 4000) },
      "Approve",
      `Always allow ${a.tool} (this session)`,
      "Deny"
    );
    if (choice === "Approve") {
      session.client.approve(a.id, "approve");
    } else if (choice && choice.startsWith("Always allow")) {
      session.alwaysAllow.add(a.tool);
      log(`session allow-listed: ${a.tool}`);
      session.client.approve(a.id, "approve");
    } else {
      // Deny or dismissed — deny is the safe direction
      session.client.approve(a.id, "deny");
    }
  }
}

async function handleCompactionProposed(ev: SidecarEvent): Promise<void> {
  if (!session) {
    return;
  }
  postToChat({ type: "event", payload: ev });
  const tiers = ev["tiers"] ?? ev["pinned"];
  const detail = [
    `projected tokens: ${ev["projected_tokens"]} / window ${ev["window"]}`,
    `estimated savings: ${ev["estimated_savings"] ?? "?"}`,
    `tiers: ${JSON.stringify(tiers)}`,
    `pinned (never reduced): ${JSON.stringify(ev["pinned"] ?? [])}`,
    "",
    String(ev["note"] ?? ""),
  ].join("\n");
  const choice = await vscode.window.showWarningMessage(
    "Awino: context window is filling — compact history?",
    { modal: true, detail: detail.slice(0, 4000) },
    "Approve compaction",
    "Deny"
  );
  const proposalId = String(ev["proposal_id"] ?? "");
  session.client.approve(proposalId, choice === "Approve compaction" ? "approve" : "deny");
}

// ------------------------------------------------------------ connection

/**
 * The user's explicit `awino.pythonPath`, or undefined when never set.
 * `get()` would return the package default ("python3") and hide whether
 * the user chose it, so we inspect the configuration scopes instead.
 */
function configuredPythonPath(): string | undefined {
  const inspected = vscode.workspace.getConfiguration("awino").inspect<string>("pythonPath");
  for (const v of [inspected?.workspaceFolderValue, inspected?.workspaceValue, inspected?.globalValue]) {
    if (typeof v === "string" && v.trim().length > 0) {
      return v;
    }
  }
  return undefined;
}

/** Last sidecar start failure, shown in the status-bar tooltip until the next successful connect. */
let lastConnectError: string | null = null;

/**
 * Whether the active provider needs an API key that is not in SecretStorage,
 * recomputed on every connect() before any early return so the chat webview
 * can show the "No model connected" setup card even when the sidecar never
 * starts (e.g. Bedrock selected with no Bedrock key).
 */
let lastKeyMissing: { missing: boolean; provider: string } | null = null;
// First-run onboarding wizard: true when activation is fresh (awino.onboarded
// not yet set) and the active provider needs an API key that is missing.
// Computed in connect() next to lastKeyMissing; the chat webview shows the
// guided flow instead of only the setup card while this is true.
let lastShowWizard = false;

// Spec 2.1 Step 3 ("Prove it"): after the wizard saves settings and the
// sidecar connects, onboarding waits for a successful test-message reply
// before marking onboarded. While true, the next turn_result/error settles it.
let awaitingProveIt = false;

// Wizard Step 3 ("Prove it"): set after a successful wizard save for a
// keyed provider. Keeps computeShowWizard() true so the post-save
// postChatState() does not hide the wizard before wizardProveReady reveals
// the prove-it step. Cleared on wizardProved, wizardDismiss, or a fresh
// wizardSave.
let wizardAwaitingProve = false;

// Spec 3: "Reconnect to apply" — hash of sidecar-affecting settings at the
// last successful connect. When the live config diverges, the status bar
// shows a warning and a one-shot notification offers one-click reconnect.
let settingsDirty = false;
let lastConnectSettingsHash = "";
let lastConnectSettings: Record<string, unknown> = {};
let settingsDirtyNotified = false;
let settingsDebounce: ReturnType<typeof setTimeout> | null = null;

// Sidecar-affecting settings (Spec 3.1). awino.keyLabels is cosmetic-only
// and explicitly excluded — it refreshes the Models panel, no reconnect.
const SIDECAR_SETTINGS = [
  "awino.provider",
  "awino.model",
  "awino.endpoint",
  "awino.timeout",
  "awino.mcpServers",
  "awino.bedrockRegion",
  "awino.bedrockAuthMode",
  "awino.bedrockAwsProfile",
  "awino.pythonPath",
] as const;

function settingsSnapshot(): Record<string, unknown> {
  const cfg = vscode.workspace.getConfiguration();
  const snap: Record<string, unknown> = {};
  for (const key of SIDECAR_SETTINGS) {
    snap[key] = cfg.get<unknown>(key, undefined);
  }
  return snap;
}

function settingsSnapshotHash(): string {
  return JSON.stringify(settingsSnapshot());
}

// Spec 3.1: debounced dirty check. Called 1.5s after the last awino.*
// change; compares the live settings hash to the connect-time snapshot.
function checkSettingsDirty(): void {
  if (!session?.ready) {
    return; // not connected — nothing for settings to be stale against
  }
  const snap = settingsSnapshot();
  const hash = JSON.stringify(snap);
  if (hash === lastConnectSettingsHash) {
    // Reverted to connect-time values — clear the flag.
    if (settingsDirty) {
      settingsDirty = false;
      settingsDirtyNotified = false;
      void vscode.commands.executeCommand("setContext", "awino:settingsDirty", false);
      publishBinding();
    }
    return;
  }
  if (!settingsDirty) {
    settingsDirty = true;
    void vscode.commands.executeCommand("setContext", "awino:settingsDirty", true);
    log("awino.* settings changed — reconnect to apply");
    publishBinding();
  }
  if (!settingsDirtyNotified) {
    settingsDirtyNotified = true;
    void vscode.window
      .showInformationMessage(
        "Awino settings changed. Reconnect the sidecar to apply them.",
        "Reconnect now",
        "Later"
      )
      .then((choice) => {
        if (choice === "Reconnect now") {
          void vscode.commands.executeCommand("awino.reconnect");
        }
      });
  }
}

// Spec 3.2: human-readable list of changed keys for the status bar tooltip.
function settingsDiffLines(): string[] {
  const cur = settingsSnapshot();
  const lines: string[] = [];
  for (const key of SIDECAR_SETTINGS) {
    const a = JSON.stringify(lastConnectSettings[key]);
    const b = JSON.stringify(cur[key]);
    if (a !== b) {
      lines.push(`• ${key}: ${a ?? "∅"} → ${b ?? "∅"}`);
    }
  }
  return lines;
}

function computeShowWizard(context: vscode.ExtensionContext): boolean {
  if (context.globalState.get<boolean>("awino.onboarded", false)) {
    return false;
  }
  // After a successful wizard save the key is no longer missing, but the
  // wizard must stay open for the prove-it step (Step 3) — it hides only
  // once the test message proves the provider (wizardProved) or the user
  // skips/dismisses.
  if (wizardAwaitingProve) {
    return true;
  }
  return lastKeyMissing?.missing ?? false;
}

/**
 * Connect (or reconnect) the sidecar, serialized through a ConnectGuard.
 * connect() is reachable from auto-connect, the Reconnect command, and the
 * Locate-Python recovery retry: without the guard, overlapping runs each
 * spawn a sidecar — the losing process leaks, and when the first run's
 * start() finally rejects its catch sets `session = null`, destroying the
 * newer live session. Concurrent callers now join the in-flight run, and a
 * stale (superseded) run never mutates the session.
 */
const connectGuard = new ConnectGuard();

async function connect(context: vscode.ExtensionContext): Promise<void> {
  return connectGuard.run((generation, isCurrent) =>
    doConnectInner(context, generation, isCurrent)
  );
}

async function doConnectInner(
  context: vscode.ExtensionContext,
  generation: number,
  isCurrent: () => boolean
): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    log("no workspace folder open — sidecar not started");
    return;
  }
  const cfg = readConfig();
  const secrets = context.secrets;
  const env: Record<string, string> = {};

  // Resolve the user-facing provider to what the sidecar understands.
  // "bedrock" rides the sidecar's OpenAI-compatible backend: Bedrock's
  // https://bedrock-runtime.{region}.amazonaws.com/openai/v1 endpoint
  // speaks the OpenAI chat-completions protocol with the Bedrock API key
  // as the Bearer token. See src/bedrock.ts for the full rationale.
  let sidecarProvider = cfg.provider;
  let sidecarEndpoint = cfg.endpoint || undefined;
  /** AWS profile name forwarded to the sidecar (bedrock SigV4 mode). */
  let sidecarAwsProfile: string | undefined;
  let displayProvider: string | undefined;

  // Read all three keys up front (SecretStorage only — never settings JSON)
  // so the setup-card state is known before any early return below.
  const openaiKey = await secrets.get(KEY_OPENAI);
  const anthropicKey = await secrets.get(KEY_ANTHROPIC);
  const bedrockKey = await secrets.get(KEY_BEDROCK);
  const userProvider = String(cfg.provider ?? "echo");
  const bedrockProfileAuth =
    userProvider === "bedrock" && cfg.bedrockAuthMode === "aws-profile";
  lastKeyMissing = {
    missing: keyMissingForProvider(
      userProvider,
      {
        openai: !!openaiKey,
        anthropic: !!anthropicKey,
        bedrock: !!bedrockKey,
      },
      { bedrockProfileAuth }
    ),
    provider: userProvider,
  };
  lastShowWizard = computeShowWizard(context);

  if (cfg.provider === "bedrock") {
    const resolved = resolveBedrockConnection({
      authMode: cfg.bedrockAuthMode,
      endpoint: cfg.endpoint,
      region: cfg.bedrockRegion,
      apiKey: bedrockKey ?? undefined,
      awsProfile: cfg.bedrockAwsProfile || undefined,
    });
    if (!resolved.ok) {
      vscode.window.showErrorMessage(`Awino: ${resolved.error}`);
      log(`bedrock connect refused: ${resolved.error}`);
      postChatState({ connected: false });
      return;
    }
    sidecarProvider = resolved.args.sidecarProvider;
    sidecarEndpoint = resolved.args.endpoint;
    if (resolved.args.keyEnvVar) {
      // api-key mode only. aws-profile mode deliberately hands the sidecar
      // no key: SigV4 signs from the AWS chain, and a key on the env would
      // be a silent fallback waiting to happen.
      env[resolved.args.keyEnvVar] = bedrockKey as string;
    }
    sidecarAwsProfile = resolved.args.awsProfile;
    displayProvider = "bedrock";
  } else {
    if (openaiKey) {
      env["AWINO_API_KEY"] = openaiKey;
    }
    if (anthropicKey) {
      env["ANTHROPIC_API_KEY"] = anthropicKey;
    }
  }

  await disconnect();
  // Resolve the interpreter: an explicit awino.pythonPath wins; then the
  // interpreter bundled inside the VSIX (0.5.0+, zero setup); on Windows
  // auto-detect (py -> python -> python3 — stock Windows installs have no
  // `python3`); otherwise the `python3` default.
  const bundled = bundledRuntimePath(process.platform, process.arch, context.extensionPath);
  if (bundled) {
    prepareBundledRuntime(bundled, process.platform);
  }
  const interp: ResolvedInterpreter = await resolvePythonInterpreter({
    configured: configuredPythonPath(),
    bundledPath: bundled ?? undefined,
  });
  log(`sidecar interpreter: ${interp.python} (source: ${interp.source})`);
  const client = new SidecarClient();
  client.on("log", (s: string) => output.append(s.replace(/\n$/, "")));
  // Bind the handler to this client's identity: a stale old-process exit
  // event arriving after a newer session connected must not clobber it.
  client.on("event", (ev: SidecarEvent) => {
    if (session?.client === client) {
      void onSidecarEvent(ev);
    }
  });
  session = {
    client,
    alwaysAllow: new Set(),
    ready: null,
    lastStatus: null,
    displayProvider,
  };
  updateStatusBar();
  try {
    const ready = await client.start({
      python: interp.python,
      sidecarPath: defaultSidecarPath(context.extensionPath),
      workspace: folder.uri.fsPath,
      provider: sidecarProvider,
      model: cfg.model || undefined,
      endpoint: sidecarEndpoint,
      awsProfile: sidecarAwsProfile,
      timeout: cfg.timeout,
      env,
      mcpServers: cfg.mcpServers,
      script: cfg.script,
    });
    lastConnectError = null;
    log(
      `connected: ${JSON.stringify({
        provider: displayProvider ?? ready["provider"],
        sidecar_provider: ready["provider"],
        model: ready["model"],
        project: ready["project"],
      })}`
    );
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    // Surface the actual OS error + which interpreter was tried + a fix
    // hint in the Output channel, the chat webview, and the status bar —
    // not the bare "not connected".
    const detail = describeSpawnFailure(interp, msg);
    if (!isCurrent()) {
      // Superseded: a newer connect run claimed the guard while this run
      // was parked (e.g. the recovery dialog's retry started a fresh run).
      // Never touch the live session from a stale run.
      log(`stale connect run (generation ${generation}) failed after being superseded — leaving the current session alone`);
      return;
    }
    lastConnectError = detail;
    log(`connect failed: ${detail}`);
    postChatState({ connected: false, connectError: detail });
    session = null;
    updateStatusBar();
    if (isInterpreterNotFound(msg)) {
      // Missing interpreter (ENOENT): offer the Locate-Python recovery
      // flow — per-platform install locations, a file picker, save to
      // awino.pythonPath, then retry — instead of a dead-end error.
      // Release the guard BEFORE the dialog: the dialog's retryConnect must
      // start a genuinely new run — re-awaiting this run's own in-flight
      // promise from inside itself would deadlock.
      connectGuard.release();
      await offerPythonRecovery(
        {
          showErrorMessage: (m, ...items) =>
            Promise.resolve(vscode.window.showErrorMessage(m, ...items)),
          showOpenDialog: (o) => Promise.resolve(vscode.window.showOpenDialog(o)),
          savePythonPath: (exe) => {
            // Write to the most specific level that already holds a value:
            // at resolve time workspaceFolderValue beats workspaceValue
            // beats globalValue, so a stale folder-level pythonPath (common
            // in multi-root workspaces) would otherwise keep winning and
            // the freshly picked interpreter would silently not take
            // effect.
            const folder = vscode.workspace.workspaceFolders?.[0];
            const level = pickPythonPathWriteLevel(
              vscode.workspace.getConfiguration("awino").inspect("pythonPath")
            );
            let target: vscode.ConfigurationTarget;
            let cfg = vscode.workspace.getConfiguration("awino");
            if (level === "workspaceFolder" && folder) {
              // WorkspaceFolder-targeted writes need a folder-scoped config.
              cfg = vscode.workspace.getConfiguration("awino", folder.uri);
              target = vscode.ConfigurationTarget.WorkspaceFolder;
            } else if (level === "workspace") {
              target = vscode.ConfigurationTarget.Workspace;
            } else {
              target = vscode.ConfigurationTarget.Global;
            }
            return Promise.resolve(cfg.update("pythonPath", exe, target)).then(
              () => undefined
            );
          },
          // Never let a retry rejection escape as an unhandled promise.
          retryConnect: () => connect(context).catch(log),
          log,
        },
        detail
      );
    } else {
      vscode.window.showErrorMessage(`Awino: sidecar failed to start — ${detail}`);
    }
  }
}

async function disconnect(): Promise<void> {
  waiters.forEach((w) => {
    clearTimeout(w.timer);
    w.reject(new Error("disconnected"));
  });
  waiters = [];
  if (session) {
    try {
      await session.client.close();
    } catch {
      /* best effort */
    }
    session = null;
  }
  // Spec 1.5: awino:connected must go false on every disconnect path.
  updateStatusBar();
}

// Spec 3.3: explicit reconnect — disconnect, then connect with the new
// settings. Chat history is preserved (the webview is never torn down).
// On success the ready handler snapshots the new settings hash and clears
// the dirty flag; on failure the dirty state is kept and recovery offered.
async function reconnect(context: vscode.ExtensionContext): Promise<void> {
  await disconnect();
  publishBinding(); // all surfaces to the disconnected state
  postChatState();
  await connect(context).catch((e) => log(`reconnect: ${e instanceof Error ? e.message : String(e)}`));
  // session.ready arrives via the ready event, asynchronously after
  // connect() resolves — wait briefly for it.
  const t0 = Date.now();
  while (!session?.ready && Date.now() - t0 < 15000) {
    await new Promise((r) => setTimeout(r, 250));
  }
  if (session?.ready) {
    const binding = (session.ready["binding"] ?? {}) as Record<string, unknown>;
    const p = String(session.displayProvider ?? binding["provider"] ?? "?");
    const m = String(binding["model"] ?? "?");
    vscode.window.showInformationMessage(`Awino reconnected: ${p} / ${m}`);
  } else {
    const choice = await vscode.window.showErrorMessage(
      `Awino reconnect failed: ${lastConnectError ?? "sidecar did not become ready"}`,
      "Retry",
      "Open Models & Providers"
    );
    if (choice === "Retry") {
      void reconnect(context);
    } else if (choice === "Open Models & Providers") {
      void vscode.commands.executeCommand("awino.openModels");
    }
  }
}

// -------------------------------------------------------------- commands

function mustSession(): Session {
  if (!session) {
    throw new Error("Awino is not connected (open a workspace folder first)");
  }
  return session;
}

// Spec 1.4: the chat header mode selector posts a preset mode id —
// Module-level so handleChatMessage (chat webview) can call it.
// same flow, skips the mode quickpick, still asks scope.
async function invokeModeFlow(presetModeId?: string): Promise<void> {
  mustSession();
  const modes = (await query("mode_list")) as {
    modes?: Array<{ id: string; label: string; custom: boolean }>;
    active?: { id?: string };
  };
  const list = modes.modes ?? [];
  let modeId = presetModeId;
  if (modeId && !list.some((m) => m.id === modeId)) {
    vscode.window.showWarningMessage(`Awino: unknown mode "${modeId}"`);
    return;
  }
  if (!modeId) {
    const items = list.map((m) => ({
      label: `${m.id === modes.active?.id ? "● " : ""}${m.label}`,
      description: m.id,
      id: m.id,
    }));
    const pick = await vscode.window.showQuickPick(items, { placeHolder: "Invoke mode (overlay — stage default stays underneath)" });
    if (!pick) {
      return;
    }
    modeId = pick.id;
  }
  const scope = await vscode.window.showQuickPick(["mission", "project", "turns"], { placeHolder: "Overlay scope" });
  if (!scope) {
    return;
  }
  let turns: number | undefined;
  if (scope === "turns") {
    const n = await vscode.window.showInputBox({ prompt: "Number of turns", value: "5" });
    turns = Number(n);
    if (!Number.isInteger(turns) || turns < 1) {
      vscode.window.showErrorMessage("Awino: turns must be a positive integer");
      return;
    }
  }
  const r = (await query("mode_invoke", { mode: modeId, scope, ...(turns ? { turns } : {}) })) as Record<string, unknown>;
  if (r["status"] === "refused") {
    vscode.window.showWarningMessage(`Awino: mode refused — ${String(r["code"] ?? "")}`);
  } else {
    vscode.window.showInformationMessage(`Awino: mode invoked — ${modeId} (${scope})`);
  }
  refreshViews();
  // The status bar shows the active mode from session.lastStatus — refresh
  // it now so the invoked mode appears immediately instead of lagging
  // until the next turn completion triggers a status refresh.
  await refreshStatus();
  // The header dropdown needs the new active mode.
  void refreshModesCache();
}

function registerCommands(context: vscode.ExtensionContext): void {
  const reg = (id: string, fn: (...args: unknown[]) => unknown) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, (...a) => fn(...a)));

  reg("awino.reconnect", () => reconnect(context));
  // Rigor coach (Honda): show the latest journaled report, or generate and
  // journal a fresh one when none exists. Read-only display in an output
  // channel; the only journal write is the report event itself.
  reg("awino.showRigorReport", () => showRigorReport());
  // Spec 2.4: Reset Onboarding — clears the onboarded flag so the wizard
  // runs again on the next chat state push. Does not touch provider keys
  // (SecretStorage) or settings; it only re-opens the first-run flow.
  reg("awino.resetOnboarding", async () => {
    await context.globalState.update("awino.onboarded", false);
    lastShowWizard = true;
    postChatState();
    vscode.window.showInformationMessage("Awino: onboarding reset — the setup wizard is showing in the chat.");
  });
  reg("awino.refreshViews", () => refreshViews());
  reg("awino.sessionResume", async () => {
    mustSession();
    await postSessionResume();
  });

  reg("awino.newMission", async () => {
    const s = mustSession();
    const text = await vscode.window.showInputBox({ prompt: "Mission objective", placeHolder: "e.g. Fix the login redirect bug" });
    if (!text) {
      return;
    }
    const critRaw = await vscode.window.showInputBox({
      prompt: "Done criteria (comma-separated)",
      placeHolder: "bug reproduced, fix verified by test, no regressions",
    });
    const criteria = (critRaw ?? "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    const r = (await query("mission", { text, criteria: criteria.length ? criteria : ["manual"] })) as Record<string, unknown>;
    if (r["status"] === "error") {
      vscode.window.showErrorMessage(`Awino: mission failed — ${String(r["said"] ?? r["status"])}`);
      return;
    }
    vscode.window.showInformationMessage(`Awino: mission started — ${String(r["status"] ?? "ok")}`);
    await refreshStatus();
    postChatState();
    await postSessionResume();
    refreshViews();
  });

  reg("awino.newMissionFromSeed", async () => {
    mustSession();
    const seeds = (await query("seeds_list")) as { seeds?: Array<{ name: string }> };
    const names = (seeds.seeds ?? []).map((x) => x.name);
    if (!names.length) {
      vscode.window.showInformationMessage("Awino: no seeds saved yet");
      return;
    }
    const pick = await vscode.window.showQuickPick(names, { placeHolder: "Pick a mission seed" });
    if (!pick) {
      return;
    }
    const r = (await query("mission_from_seed", { name: pick })) as Record<string, unknown>;
    vscode.window.showInformationMessage(`Awino: mission from seed — ${String(r["status"] ?? "ok")}`);
    await refreshStatus();
    postChatState();
    await postSessionResume();
    refreshViews();
  });

  reg("awino.saveSeed", async () => {
    mustSession();
    const name = await vscode.window.showInputBox({ prompt: "Seed name", placeHolder: "my-mission-template" });
    if (!name) {
      return;
    }
    const r = (await query("seed_save", { name })) as Record<string, unknown>;
    if (r["status"] === "error") {
      vscode.window.showErrorMessage(`Awino: seed save failed — ${String(r["said"] ?? r["status"])}`);
      return;
    }
    // The seed file is written even when registry task registration fails —
    // say so instead of reporting a clean save, with the sidecar's reason
    // (registry_error) in the notification and the Output channel.
    const registryError =
      r["task_registered"] === false ? String(r["registry_error"] ?? "") : "";
    const trackingNote =
      r["task_registered"] === false
        ? ` (task tracking failed${registryError ? `: ${registryError}` : ""})`
        : "";
    if (r["task_registered"] === false) {
      log(
        `seed_save: seed file written but registry task registration failed` +
          (registryError ? `: ${registryError}` : "")
      );
    }
    vscode.window.showInformationMessage(`Awino: seed saved as ${name}${trackingNote}`);
    refreshViews(); // the Tasks panel mirrors the seed-registered task
  });

  reg("awino.inspectContract", async () => {
    mustSession();
    const r = (await query("contract")) as Record<string, unknown>;
    const doc = await vscode.workspace.openTextDocument({
      content: String(r["contract"] ?? JSON.stringify(r, null, 2)),
      language: "markdown",
    });
    await vscode.window.showTextDocument(doc, { preview: true });
  });

  reg("awino.approveContract", async () => {
    mustSession();
    const scopeRaw = await vscode.window.showInputBox({
      prompt: "Approve contract — scope files (comma-separated, workspace-relative). Empty approves without scope.",
      placeHolder: "notes.txt, src/app.py",
    });
    if (scopeRaw === undefined) {
      return; // dismissed
    }
    const scope = scopeRaw
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    const r = (await query("approve-contract", { scope })) as Record<string, unknown>;
    vscode.window.showInformationMessage(
      `Awino: contract approval — ${JSON.stringify(r).slice(0, 300)}`
    );
    refreshViews();
  });

  reg("awino.rollback", async () => {
    mustSession();
    const seqRaw = await vscode.window.showInputBox({ prompt: "Roll back effects at or after sequence number", placeHolder: "42" });
    const seq = Number(seqRaw);
    if (!Number.isInteger(seq) || seq < 0) {
      vscode.window.showErrorMessage("Awino: seq must be a non-negative integer");
      return;
    }
    const ok = await vscode.window.showWarningMessage(
      `Roll back all recorded effects at or after seq ${seq}?`,
      { modal: true },
      "Roll back"
    );
    if (ok !== "Roll back") {
      return;
    }
    const r = (await query("rollback", { seq })) as Record<string, unknown>;
    vscode.window.showInformationMessage(`Awino: rollback — ${JSON.stringify(r).slice(0, 300)}`);
    refreshViews();
  });

  reg("awino.housekeep", async () => {
    mustSession();
    const r = (await query("housekeeping", { reason: "manual" })) as Record<string, unknown>;
    const hk = (r["housekeeping"] ?? {}) as Record<string, unknown>;
    vscode.window.showInformationMessage(
      `Awino: housekeeping done — archived ${hk["archived"] ?? 0} file(s), manifest written`
    );
    postToChat({ type: "event", payload: { event: "command_result", name: "housekeeping", ok: true, result: r } });
    refreshViews();
  });

  reg("awino.doctor", () => doctorProject());

  reg("awino.addSkill", async () => {
    mustSession();
    const files = await vscode.window.showOpenDialog({
      canSelectMany: false,
      openLabel: "Admit skill (verified + hash-pinned)",
      filters: { "Skill files": ["md", "txt"] },
    });
    if (!files?.length) {
      return;
    }
    const name =
      (await vscode.window.showInputBox({ prompt: "Skill name", value: path.basename(files[0].fsPath, path.extname(files[0].fsPath)) })) ?? "";
    if (!name.trim()) {
      return;
    }
    const r = (await query("skill_add", { name: name.trim(), path: files[0].fsPath })) as Record<string, unknown>;
    if (r["status"] && ["refused", "rejected", "error"].includes(String(r["status"]))) {
      vscode.window.showWarningMessage(`Awino: skill refused — ${String(r["detail"] ?? r["status"])}`);
    } else {
      vscode.window.showInformationMessage(`Awino: skill admitted — ${name.trim()} (sha ${(String(r["sha256"] ?? "")).slice(0, 12)})`);
    }
    refreshViews();
  });

  reg("awino.addContext", async () => {
    mustSession();
    const name = await vscode.window.showInputBox({ prompt: "Context file name", placeHolder: "architecture" });
    if (!name?.trim()) {
      return;
    }
    const content = await vscode.window.showInputBox({ prompt: `Content for ${name.trim()}` });
    await query("context_add", { name: name.trim(), content: content ?? "" });
    vscode.window.showInformationMessage(`Awino: context file added — ${name.trim()}`);
    refreshViews();
  });

  reg("awino.removeContext", async () => {
    mustSession();
    const files = (await query("context_list")) as { files?: Array<{ name: string }> };
    const names = (files.files ?? []).map((f) => f.name);
    if (!names.length) {
      vscode.window.showInformationMessage("Awino: no context files");
      return;
    }
    const pick = await vscode.window.showQuickPick(names, { placeHolder: "Remove context file" });
    if (!pick) {
      return;
    }
    await query("context_remove", { name: pick });
    refreshViews();
  });

  reg("awino.setApiKey", async () => {
    const provider = await vscode.window.showQuickPick(
      [
        { label: "OpenAI-compatible (AWINO_API_KEY)", key: KEY_OPENAI },
        { label: "Anthropic (ANTHROPIC_API_KEY)", key: KEY_ANTHROPIC },
        { label: "AWS Bedrock (Bedrock API key)", key: KEY_BEDROCK },
      ],
      { placeHolder: "Which provider's key?" }
    );
    if (!provider) {
      return;
    }
    const value = await vscode.window.showInputBox({
      prompt: `API key for ${provider.label}`,
      password: true,
      placeHolder: "stored in VS Code SecretStorage, never in settings",
    });
    if (!value) {
      return;
    }
    try {
      await storeSecret(context.secrets, provider.key, value);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`setApiKey: secret store failed: ${msg}`);
      vscode.window.showErrorMessage(`Awino: could not store the key — ${msg}`);
      return;
    }
    vscode.window.showInformationMessage(`Awino: key stored securely. Reconnect to apply.`);
  });

  reg("awino.clearApiKey", async () => {
    const provider = await vscode.window.showQuickPick(
      [
        { label: "OpenAI-compatible (AWINO_API_KEY)", key: KEY_OPENAI },
        { label: "Anthropic (ANTHROPIC_API_KEY)", key: KEY_ANTHROPIC },
        { label: "AWS Bedrock (Bedrock API key)", key: KEY_BEDROCK },
      ],
      { placeHolder: "Which provider's key to clear?" }
    );
    if (!provider) {
      return;
    }
    try {
      await deleteSecret(context.secrets, provider.key);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`clearApiKey: secret delete failed: ${msg}`);
      vscode.window.showErrorMessage(`Awino: could not clear the key — ${msg}`);
      return;
    }
    vscode.window.showInformationMessage(`Awino: key cleared from secret storage.`);
  });

  reg("awino.invokeMode", () => invokeModeFlow());


  reg("awino.dismissMode", async () => {
    mustSession();
    await query("mode_dismiss");
    vscode.window.showInformationMessage("Awino: back to stage-default mode");
    refreshViews();
  });

  reg("awino.assumePersona", async () => {
    mustSession();
    const skills = (await query("skills_list")) as {
      packaged?: Array<{ name: string }>;
      project?: Array<{ name: string }>;
    };
    const names = [...(skills.packaged ?? []), ...(skills.project ?? [])].map((s) => s.name);
    if (!names.length) {
      vscode.window.showInformationMessage("Awino: no admitted skills to personify");
      return;
    }
    const pick = await vscode.window.showQuickPick(names, {
      placeHolder: "Assume skill as persona (lens, not license — tools stay gated)",
    });
    if (!pick) {
      return;
    }
    const turnsRaw = await vscode.window.showInputBox({ prompt: "Persona lasts N turns", value: "10" });
    const turns = Number(turnsRaw);
    if (!Number.isInteger(turns) || turns < 1) {
      vscode.window.showErrorMessage("Awino: turns must be a positive integer");
      return;
    }
    const r = (await query("persona_assume", { skill: pick, turns })) as Record<string, unknown>;
    if (r["status"] === "refused") {
      vscode.window.showWarningMessage(`Awino: persona refused — ${String(r["detail"] ?? r["code"])}`);
    } else {
      vscode.window.showInformationMessage(`Awino: persona assumed — ${pick} (${turns} turns)`);
    }
    refreshViews();
  });

  reg("awino.dismissPersona", async () => {
    mustSession();
    await query("persona_dismiss");
    vscode.window.showInformationMessage("Awino: persona dismissed");
    refreshViews();
  });

  reg("awino.switchEnvironment", async () => {
    mustSession();
    const folder = vscode.workspace.workspaceFolders?.[0];
    const envs = folder ? listEnvironments(folder.uri.fsPath) : [];
    let pick: string | undefined;
    if (envs.length) {
      pick = await vscode.window.showQuickPick(envs, { placeHolder: "Switch to environment" });
    } else {
      pick = await vscode.window.showInputBox({
        prompt: "Environment name (from .awino/providers.yaml)",
        placeHolder: "default",
      });
    }
    if (!pick) {
      return;
    }
    const r = (await query("env_switch", { environment: pick })) as Record<string, unknown>;
    if (r["status"] === "refused") {
      vscode.window.showWarningMessage(`Awino: environment switch refused — ${String(r["detail"] ?? r["code"])}`);
    } else {
      vscode.window.showInformationMessage(`Awino: environment switched — ${pick}`);
      await refreshStatus();
      refreshViews();
    }
  });

  reg("awino.setupBedrock", async () => {
    // Guided AWS Bedrock setup: region -> auth -> key -> (test) -> model.
    // Every prompt explains itself; keys go to SecretStorage only.

    // 1. region
    const regionPick = await vscode.window.showQuickPick(
      [
        ...BEDROCK_REGIONS.map((r) => ({ label: r, description: `https://bedrock-runtime.${r}.amazonaws.com/openai/v1` })),
        { label: "Other region…", description: "type any valid AWS region" },
      ],
      { placeHolder: "AWS region for Bedrock (the endpoint URL is derived from it)" }
    );
    if (!regionPick) {
      return;
    }
    let region = regionPick.label;
    if (region === "Other region…") {
      const typed = await vscode.window.showInputBox({
        prompt: "AWS region",
        placeHolder: "us-east-1",
        validateInput: (v) =>
          isValidRegion(v) ? undefined : "That doesn't look like an AWS region name (e.g. us-east-1).",
      });
      if (!typed) {
        return;
      }
      region = typed.trim();
    }

    // 2. auth method — both are real, working options.
    const authPick = await vscode.window.showQuickPick(
      [
        {
          label: "AWS profile / SSO",
          description: "~/.aws/config — SigV4 signing, no API key needed",
        },
        {
          label: "Bedrock API key",
          description: "Bedrock console → API keys → Generate API key",
        },
      ],
      { placeHolder: "How should the extension authenticate to Bedrock?" }
    );
    if (!authPick) {
      return;
    }
    const authMode: BedrockAuthMode = authPick.label.startsWith("AWS profile")
      ? "aws-profile"
      : "api-key";

    let awsProfile = "";
    if (authMode === "aws-profile") {
      // 2b. pick a profile from the user's real ~/.aws/config.
      const configPath = path.join(os.homedir(), ".aws", "config");
      let profiles: string[] = [];
      try {
        profiles = parseAwsProfileNames(fs.readFileSync(configPath, "utf8"));
      } catch {
        profiles = [];
      }
      const profileItems = [
        ...profiles.map((p) => ({ label: p, description: "from ~/.aws/config" })),
        { label: "Type a profile name…", description: "" },
      ];
      const profPick = await vscode.window.showQuickPick(profileItems, {
        placeHolder:
          profiles.length > 0
            ? "AWS profile to sign Bedrock requests with"
            : "No profiles found in ~/.aws/config — type one (or run `aws configure` / `aws sso login` first)",
      });
      if (!profPick) {
        return;
      }
      if (profPick.label === "Type a profile name…") {
        const typed = await vscode.window.showInputBox({
          prompt: "AWS profile name",
          placeHolder: "my-sso-profile",
          validateInput: (v) => (v.trim() ? undefined : "The profile name can't be empty."),
        });
        if (!typed) {
          return;
        }
        awsProfile = typed.trim();
      } else {
        awsProfile = profPick.label;
      }
      vscode.window.showInformationMessage(
        `Awino: profile "${awsProfile}" — if it uses SSO, run \`aws sso login --profile ${awsProfile}\` first; ` +
          `the sidecar will refuse to connect with a named error otherwise.`
      );
    }

    // 3. key (kept only in SecretStorage) — api-key mode only.
    let key: string | undefined;
    if (authMode === "api-key") {
      const existing = await context.secrets.get(KEY_BEDROCK);
      key = existing ?? undefined;
      const replace =
        existing &&
        (await vscode.window.showQuickPick(["Keep the stored key", "Replace it"], {
          placeHolder: "A Bedrock API key is already stored",
        }));
      if (replace === undefined && existing) {
        return;
      }
      if (!existing || replace === "Replace it") {
        const typed = await vscode.window.showInputBox({
          prompt: "Bedrock API key",
          password: true,
          placeHolder: "from the Bedrock console → API keys → Generate API key",
          validateInput: (v) => (v.trim() ? undefined : "The key can't be empty."),
        });
        if (!typed) {
          return;
        }
        key = typed.trim();
        try {
          await storeSecret(context.secrets, KEY_BEDROCK, key);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          log(`bedrock setup: secret store failed: ${msg}`);
          vscode.window.showErrorMessage(`Awino: could not store the Bedrock key — ${msg}`);
          return;
        }
      }
    }

    // 4. optional live connection test (api-key mode only: lists models with
    // the key). For profile auth the connect itself is the test — the
    // sidecar fails closed with a named error if the profile is unusable.
    const endpoint = bedrockEndpointForRegion(region);
    if (!endpoint.ok) {
      vscode.window.showErrorMessage(`Awino: ${endpoint.error}`);
      return;
    }
    if (authMode === "api-key") {
      const testIt = await vscode.window.showQuickPick(["Test the connection", "Skip the test"], {
        placeHolder: "Verify the key and region against Bedrock now?",
      });
      if (testIt === undefined) {
        return;
      }
      if (testIt.startsWith("Test")) {
        const probe = await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: "Awino: testing Bedrock connection…" },
          () => probeBedrockModels(endpoint.endpoint, key as string)
        );
        if (!probe.ok) {
          const retry = await vscode.window.showErrorMessage(
            `Awino: connection test failed — ${probe.error}`,
            "Continue anyway",
            "Cancel setup"
          );
          if (retry !== "Continue anyway") {
            return;
          }
        } else {
          vscode.window.showInformationMessage(
            `Awino: Bedrock answered — ${probe.models?.length ?? 0} model(s) visible to this key.`
          );
        }
      }
    }

    // 5. model: friendly id, inference-profile id, or a full ARN (validated)
    let model = "";
    for (;;) {
      const typed = await vscode.window.showInputBox({
        prompt: "Bedrock model",
        placeHolder: "us.anthropic.claude-sonnet-4-5-20250929-v1:0 — or paste a full ARN",
        value: model || undefined,
      });
      if (typed === undefined) {
        return;
      }
      const parsed = parseBedrockModelRef(typed);
      if (parsed.kind === "invalid") {
        const again = await vscode.window.showErrorMessage(
          `Awino: ${parsed.error}`,
          "Try again",
          "Cancel setup"
        );
        if (again !== "Try again") {
          return;
        }
        model = typed;
        continue;
      }
      model = parsed.ref;
      if (parsed.description) {
        log(`bedrock model: ${parsed.description}`);
      }
      break;
    }

    // 6. validate, write config, and offer reconnect
    const setupErrors = validateBedrockSetup({
      region,
      modelRef: model,
      authMode,
      keyPresent: !!key,
      awsProfile,
    });
    if (setupErrors.length > 0) {
      vscode.window.showErrorMessage(`Awino: Bedrock setup is incomplete — ${setupErrors[0]}`);
      return;
    }
    const cfg = vscode.workspace.getConfiguration("awino");
    await cfg.update("provider", "bedrock", vscode.ConfigurationTarget.Workspace);
    await cfg.update("bedrockAuthMode", authMode, vscode.ConfigurationTarget.Workspace);
    await cfg.update("bedrockRegion", region, vscode.ConfigurationTarget.Workspace);
    await cfg.update(
      "bedrockAwsProfile",
      authMode === "aws-profile" ? awsProfile : "",
      vscode.ConfigurationTarget.Workspace
    );
    await cfg.update("model", model, vscode.ConfigurationTarget.Workspace);
    const authDesc =
      authMode === "aws-profile" ? `AWS profile "${awsProfile}" (SigV4)` : "Bedrock API key";
    const reconnect = await vscode.window.showInformationMessage(
      `Awino: Bedrock is configured (${region} → ${endpoint.endpoint}, ${authDesc}). Reconnect the sidecar to apply?`,
      "Reconnect",
      "Later"
    );
    if (reconnect === "Reconnect") {
      await connect(context);
    }
  });

  reg("awino.openModels", () => openModelsPanel(context));

  reg("awino.importConnections", async () => {
    await importConnectionsFlow(context);
  });
}

// --------------------------------------------- connection importer (UI)

// Permission-first import of model connection details from other tools.
// Nothing on disk is read before the user consents; nothing is written
// before the user ticks findings AND confirms. Secrets are never imported
// (see src/connection_importer.ts).

async function importConnectionsFlow(context: vscode.ExtensionContext): Promise<void> {
  const consent = await vscode.window.showWarningMessage(
    "Awino: may I read your Claude Code, Kilo CLI, and project .env configs to find model connection details? " +
      "I only read non-secret facts (provider, region, model, endpoint) — never keys or tokens — and nothing is applied without your approval.",
    { modal: true },
    "Yes, scan my configs",
    "No"
  );
  if (consent !== "Yes, scan my configs") {
    vscode.window.showInformationMessage("Awino: no problem — nothing was read.");
    return;
  }

  const folder = vscode.workspace.workspaceFolders?.[0];
  const result = scanSources(
    (p) => {
      try {
        return fs.readFileSync(p, "utf8");
      } catch {
        return null;
      }
    },
    os.homedir(),
    folder?.uri.fsPath ?? null
  );
  log(`connection import scan: ${result.scanned.length} files read, ${result.findings.length} findings`);

  const applicable = applicableFindings(result.findings);
  const informational = informationalFindings(result.findings);
  // Source transparency: the results UI names exactly which files were
  // read — paths only, never values or secrets.
  const lookedIn = `Looked in: ${formatScannedLine(result.scanned)}`;
  if (applicable.length === 0) {
    vscode.window.showInformationMessage(
      `Awino: no importable connection details found. ${lookedIn}.`
    );
    return;
  }

  const items: Array<vscode.QuickPickItem & { finding: ImportFinding }> = applicable.map((f) => ({
    label: `${f.provider} · ${f.kind}`,
    description: f.value,
    detail: `${f.source} — ${f.note}`,
    picked: true,
    finding: f,
  }));
  const picked = await vscode.window.showQuickPick(items, {
    canPickMany: true,
    placeHolder: "Tick the connection details to import into Awino",
  });
  if (!picked || picked.length === 0) {
    return; // user-confirm step: no selection, no writes
  }

  const writes = buildConfigWrites(picked.map((p) => p.finding));
  const infoNotes = informational
    .map((f) => `• ${f.note}`)
    .join("\n");
  const detail =
    `${lookedIn}\n\n` +
    "This will set:\n" +
    summarizeWrites(writes) +
    (infoNotes ? `\n\nAlso found (not imported):\n${infoNotes}` : "");
  const confirm = await vscode.window.showWarningMessage(
    "Awino: apply these settings?",
    { modal: true, detail },
    "Apply",
    "Cancel"
  );
  if (confirm !== "Apply") {
    return; // user-confirm step: no confirm, no writes
  }

  const cfg = vscode.workspace.getConfiguration("awino");
  for (const w of writes) {
    await cfg.update(w.key, w.value, vscode.ConfigurationTarget.Workspace);
  }
  log(`connection import applied: ${summarizeWrites(writes).replace(/\n/g, "; ")}`);
  const reconnect = await vscode.window.showInformationMessage(
    "Awino: imported connection settings applied. Reconnect the sidecar to use them?",
    "Reconnect",
    "Later"
  );
  if (reconnect === "Reconnect") {
    await connect(context);
  }
}

/** First-run offer: shown once ever. "Not now" still counts as offered. */
async function offerConnectionImportOnce(context: vscode.ExtensionContext): Promise<void> {
  const FLAG = "awino.importOfferShown";
  if (context.globalState.get<boolean>(FLAG)) {
    return;
  }
  await context.globalState.update(FLAG, true);
  const cfg = readConfig();
  if (cfg.provider && cfg.provider !== "echo") {
    return; // already configured — no need to offer
  }
  const choice = await vscode.window.showInformationMessage(
    "Awino: I can import model connections from Claude Code, Kilo CLI, or your project's .env file so you don't retype them. May I scan those configs?",
    "Import connections",
    "Not now",
    "Don't ask again"
  );
  if (choice === "Import connections") {
    await importConnectionsFlow(context);
  }
  // Every other choice (including dismiss) leaves the flag set: offered once.
}

// --------------------------------------------------------------- doctor

async function doctorProject(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    vscode.window.showWarningMessage("Awino: open a folder first");
    return;
  }
  const root = folder.uri.fsPath;
  const exists = (p: string) => fs.existsSync(path.join(root, p));
  const findings: Array<{ label: string; fix: string; run: () => Promise<string> }> = [];

  if (!exists(".env") && exists(".env.example")) {
    findings.push({
      label: ".env is missing (.env.example exists)",
      fix: "Copy .env.example to .env",
      run: async () => {
        fs.copyFileSync(path.join(root, ".env.example"), path.join(root, ".env"));
        return "created .env from .env.example";
      },
    });
  }
  const isPython = exists("requirements.txt") || exists("pyproject.toml") || exists("setup.py");
  if (isPython && !exists(".venv") && !exists("venv")) {
    // Windows: stock installs provide `py` (the launcher), not `python3` —
    // a hard-coded `python3` spawn is a guaranteed ENOENT there.
    const venvCmd = process.platform === "win32" ? "py" : "python3";
    findings.push({
      label: "No Python virtualenv (.venv)",
      fix: `Create with ${venvCmd} -m venv .venv`,
      run: () =>
        new Promise((resolve, reject) => {
          const p = spawn(venvCmd, ["-m", "venv", ".venv"], { cwd: root, windowsHide: true });
          let err = "";
          p.stderr.on("data", (d) => (err += d));
          // Without an "error" listener a spawn failure (e.g. ENOENT)
          // throws inside the extension host and this promise never
          // settles — the doctor UI hangs.
          p.on("error", (e) => reject(e instanceof Error ? e : new Error(String(e))));
          p.on("close", (code) => (code === 0 ? resolve("created .venv") : reject(new Error(err.slice(0, 200) || `venv failed (code ${code})`))));
        }),
    });
  }
  if (exists("package.json") && !exists("node_modules")) {
    // Windows: `npm` is npm.cmd — CreateProcess only appends `.exe`, so a
    // bare "npm" spawn is a guaranteed ENOENT without shell:true.
    const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm";
    findings.push({
      label: "node_modules missing",
      fix: "Run npm install",
      run: () =>
        new Promise((resolve, reject) => {
          const p = spawn(npmCmd, ["install", "--no-audit", "--no-fund"], { cwd: root, windowsHide: true });
          p.on("error", (e) => reject(e instanceof Error ? e : new Error(String(e))));
          p.on("close", (code) => (code === 0 ? resolve("npm install done") : reject(new Error(`npm install failed (code ${code})`))));
        }),
    });
  }
  if (!exists(".git")) {
    findings.push({
      label: "Not a git repository",
      fix: "Run git init",
      run: () =>
        new Promise((resolve, reject) => {
          const p = spawn("git", ["init"], { cwd: root, windowsHide: true });
          p.on("error", (e) => reject(e instanceof Error ? e : new Error(String(e))));
          p.on("close", (code) => (code === 0 ? resolve("git init done") : reject(new Error(`git init failed (code ${code})`))));
        }),
    });
  }

  if (!findings.length) {
    vscode.window.showInformationMessage("Awino Doctor: project looks healthy — nothing to fix.");
    return;
  }
  for (const f of findings) {
    const choice = await vscode.window.showWarningMessage(
      `Awino Doctor: ${f.label}`,
      { modal: true, detail: "Approval-gated fix. Nothing runs without your explicit approval." },
      `Apply: ${f.fix}`,
      "Skip"
    );
    if (choice?.startsWith("Apply")) {
      try {
        const done = await f.run();
        vscode.window.showInformationMessage(`Awino Doctor: ${done}`);
      } catch (e) {
        vscode.window.showErrorMessage(`Awino Doctor: fix failed — ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  }
}

// ---------------------------------------------------------- models panel

function openModelsPanel(context: vscode.ExtensionContext): void {
  if (modelsPanel) {
    modelsPanel.reveal();
    return;
  }
  const webviewDir = vscode.Uri.file(path.join(context.extensionPath, "webview"));
  const panel = vscode.window.createWebviewPanel(
    "awinoModels",
    "Awino: Models & Providers",
    vscode.ViewColumn.Beside,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [webviewDir],
    }
  );
  modelsPanel = panel;
  panel.webview.html = loadWebviewHtml(context, "models.html")
    .replace(/\{\{CSP_SOURCE\}\}/g, panel.webview.cspSource)
    .replace(
      "{{SETUP_SHARED_JS}}",
      String(panel.webview.asWebviewUri(vscode.Uri.joinPath(webviewDir, "setup-shared.js")))
    )
    .replace(
      "{{MODELS_JS}}",
      String(panel.webview.asWebviewUri(vscode.Uri.joinPath(webviewDir, "models.js")))
    );
  panel.onDidDispose(() => {
    modelsPanel = null;
  });
  panel.webview.onDidReceiveMessage(async (m: { type: string; [k: string]: unknown }) => {
    switch (m.type) {
      case "reconnect": {
        // Models panel "Reconnect to apply" button (disconnected state).
        await vscode.commands.executeCommand("awino.reconnect");
        break;
      }
      case "init": {
        const cfg = readConfig();
        const folder = vscode.workspace.workspaceFolders?.[0];
        const binding = (session?.ready?.["binding"] ?? null) as Record<string, unknown> | null;
        panel.webview.postMessage({
          type: "state",
          config: cfg,
          binding: binding ? { ...binding, provider: session?.displayProvider ?? binding["provider"] } : null,
          environments: folder ? listEnvironments(folder.uri.fsPath) : [],
          openaiKeySet: !!(await context.secrets.get(KEY_OPENAI)),
          anthropicKeySet: !!(await context.secrets.get(KEY_ANTHROPIC)),
          bedrockKeySet: !!(await context.secrets.get(KEY_BEDROCK)),
          keyLabels: readKeyLabels(),
          bedrockRegions: BEDROCK_REGIONS,
        });
        break;
      }
      case "fetchModels": {
        // Model discovery: query the provider's list endpoint. The key comes
        // from what the user just typed (unsaved is fine); for openai we fall
        // back to the stored secret. Failures never block saving — the panel
        // keeps its manual text input and shows the plain-language reason.
        const r = await runModelDiscovery(
          context,
          String(m.provider ?? "openai"),
          String(m.endpoint ?? "").trim(),
          typeof m.key === "string" && m.key ? m.key : undefined
        );
        panel.webview.postMessage({
          type: "modelsFetched",
          ok: r.ok,
          models: r.models,
          error: r.error ?? null,
        });
        break;
      }
      case "openExternal": {
        await openExternal(m.url);
        break;
      }
      case "save": {
        const cfg = vscode.workspace.getConfiguration("awino");
        await cfg.update("provider", String(m.provider ?? "echo"), vscode.ConfigurationTarget.Workspace);
        await cfg.update("endpoint", String(m.endpoint ?? ""), vscode.ConfigurationTarget.Workspace);
        await cfg.update("model", String(m.model ?? ""), vscode.ConfigurationTarget.Workspace);
        await cfg.update("bedrockRegion", String(m.bedrockRegion ?? ""), vscode.ConfigurationTarget.Workspace);
        await cfg.update(
          "bedrockAuthMode",
          m.bedrockAuthMode === "aws-profile" ? "aws-profile" : "api-key",
          vscode.ConfigurationTarget.Workspace
        );
        await cfg.update("bedrockAwsProfile", String(m.bedrockAwsProfile ?? ""), vscode.ConfigurationTarget.Workspace);
        await cfg.update("timeout", Number(m.timeout ?? 180), vscode.ConfigurationTarget.Workspace);
        // Secret writes go through the timeout helper: a hanging keyring
        // must surface an error in the panel, never freeze the save.
        const secretError = async (op: Promise<void>): Promise<string | null> => {
          try {
            await op;
            return null;
          } catch (err) {
            return err instanceof Error ? err.message : String(err);
          }
        };
        let keyError: string | null = null;
        if (typeof m.openaiKey === "string" && m.openaiKey) {
          keyError = await secretError(storeSecret(context.secrets, KEY_OPENAI, m.openaiKey));
        }
        if (!keyError && typeof m.anthropicKey === "string" && m.anthropicKey) {
          keyError = await secretError(storeSecret(context.secrets, KEY_ANTHROPIC, m.anthropicKey));
        }
        if (!keyError && typeof m.bedrockKey === "string" && m.bedrockKey) {
          keyError = await secretError(storeSecret(context.secrets, KEY_BEDROCK, m.bedrockKey));
        }
        if (keyError) {
          log(`models panel save: secret store failed: ${keyError}`);
          panel.webview.postMessage({ type: "saveFailed", error: keyError });
          break;
        }
        // Key labels are not secret — they live in settings, next to the
        // other awino.* values. Only non-empty labels are stored.
        const labels: Record<string, string> = {};
        const labelFields: Array<[string, string]> = [
          ["openaiKeyLabel", "openai"],
          ["anthropicKeyLabel", "anthropic"],
          ["bedrockKeyLabel", "bedrock"],
        ];
        for (const [field, name] of labelFields) {
          const v = String(m[field] ?? "").trim().slice(0, 40);
          if (v) {
            labels[name] = v;
          }
        }
        await cfg.update("keyLabels", labels, vscode.ConfigurationTarget.Workspace);
        if (m.clearKeys) {
          const delError =
            (await secretError(deleteSecret(context.secrets, KEY_OPENAI))) ??
            (await secretError(deleteSecret(context.secrets, KEY_ANTHROPIC))) ??
            (await secretError(deleteSecret(context.secrets, KEY_BEDROCK)));
          if (delError) {
            log(`models panel save: secret delete failed: ${delError}`);
            panel.webview.postMessage({ type: "saveFailed", error: delError });
            break;
          }
        }
        vscode.window.showInformationMessage("Awino: settings saved — reconnecting sidecar…");
        await connect(context);
        panel.webview.postMessage({ type: "reconnected", ok: !!session?.ready });
        break;
      }
      case "switchEnv": {
        if (!session) {
          break;
        }
        const r = (await query("env_switch", { environment: String(m.environment) })) as Record<string, unknown>;
        panel.webview.postMessage({ type: "envSwitched", ok: r["status"] !== "refused", result: r });
        await refreshStatus();
        break;
      }
      default:
        log(`unknown models message: ${m.type}`);
    }
  });
}

// ---------------------------------------------------------------- activate

export function activate(context: vscode.ExtensionContext): void {
  extContext = context;
  output = vscode.window.createOutputChannel("Awino");
  context.subscriptions.push(output);
  log("activating");

  const queryFn: QueryFn = (name, args) => query(name, args ?? {});
  contractView = new ContractView(queryFn);
  journalView = new JournalView(queryFn);
  learningsView = new LearningsView(queryFn);
  skillsView = new SkillsView(queryFn);
  contextView = new ContextView(queryFn);
  modesView = new ModesView(queryFn);
  tasksView = new TasksView(queryFn);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("awino.contract", contractView),
    vscode.window.registerTreeDataProvider("awino.journal", journalView),
    vscode.window.registerTreeDataProvider("awino.learnings", learningsView),
    vscode.window.registerTreeDataProvider("awino.skills", skillsView),
    vscode.window.registerTreeDataProvider("awino.context", contextView),
    vscode.window.registerTreeDataProvider("awino.modes", modesView),
    vscode.window.registerTreeDataProvider("awino.tasks", tasksView),
    // retainContextWhenHidden: keep the webview DOM alive when the user
    // switches tabs (Explorer etc.). The host-side transcript replay on
    // the "chatReady" handshake is the recovery path if the webview is
    // still ever recreated.
    vscode.window.registerWebviewViewProvider("awino.chat", new ChatViewProvider(context),
      { webviewOptions: { retainContextWhenHidden: true } })
  );

  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "awino.openModels";
  context.subscriptions.push(statusBar);
  statusBar.show();
  // Rigor coach: separate small status item (priority 90, below the
  // provider/connection bar). Starts hidden; refreshRigorBar shows it only
  // when a journaled rigor_report exists.
  rigorBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
  rigorBar.command = "awino.showRigorReport";
  context.subscriptions.push(rigorBar);
  updateStatusBar();

  registerCommands(context);

  // First-run offer: import connections from other tools (once ever).
  void offerConnectionImportOnce(context);

  // context-file / mode tree item commands (view/item)
  context.subscriptions.push(
    vscode.commands.registerCommand("awino.invokeModeItem", (item: vscode.TreeItem) => {
      const m = ModesView.modeOf(item);
      if (m) {
        void vscode.commands.executeCommand("awino.invokeMode");
      }
    })
  );

  // auto-connect on activation when a folder is open
  void connect(context);

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (!e.affectsConfiguration("awino")) {
        return;
      }
      // Spec 3.1: debounce 1.5s so typing in settings.json doesn't spam.
      if (settingsDebounce) {
        clearTimeout(settingsDebounce);
      }
      settingsDebounce = setTimeout(() => {
        settingsDebounce = null;
        checkSettingsDirty();
      }, 1500);
    })
  );
}

export async function deactivate(): Promise<void> {
  await disconnect();
}
