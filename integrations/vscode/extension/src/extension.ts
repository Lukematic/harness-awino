/**
 * extension.ts — A.W.I.N.O. VS Code extension host.
 *
 * The extension is a surface. The Python sidecar owns the turn loop:
 * every model-proposed action flows through Loop.run_user_turn in the
 * sidecar process. This file spawns the sidecar, routes its §4 events
 * into the chat webview / tree views / status bar, and forwards only
 * the §4 command verbs back. It never constructs turns or calls tools.
 */

import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";
import { spawn } from "child_process";
import { SidecarClient, SidecarEvent, defaultSidecarPath } from "./sidecar";
import {
  QueryFn,
  ContractView,
  JournalView,
  LearningsView,
  SkillsView,
  ContextView,
  ModesView,
} from "./views";

const EXT_ID = "awino-loop-owner";
const KEY_OPENAI = "awino.apiKey.openai"; // -> AWINO_API_KEY
const KEY_ANTHROPIC = "awino.apiKey.anthropic"; // -> ANTHROPIC_API_KEY

// ------------------------------------------------------------------ config

interface AwinoConfig {
  provider: string;
  endpoint: string;
  model: string;
  timeout: number;
  pythonPath: string;
  mcpServers: Array<{ name: string; command: string; args?: string[]; env?: Record<string, string> }>;
}

function readConfig(): AwinoConfig {
  const c = vscode.workspace.getConfiguration("awino");
  return {
    provider: c.get<string>("provider", "echo"),
    endpoint: c.get<string>("endpoint", ""),
    model: c.get<string>("model", ""),
    timeout: c.get<number>("timeout", 180),
    pythonPath: c.get<string>("pythonPath", "python3"),
    mcpServers: c.get<Array<{ name: string; command: string; args?: string[]; env?: Record<string, string> }>>(
      "mcpServers",
      []
    ),
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
}

// Webview handles live outside the session so a reconnect (sidecar restart)
// doesn't orphan the already-resolved chat/models views.
let chatPanel: vscode.WebviewView | null = null;
let modelsPanel: vscode.WebviewPanel | null = null;

let session: Session | null = null;
let statusBar: vscode.StatusBarItem;
let output: vscode.OutputChannel;

function log(s: string): void {
  output.appendLine(`[awino] ${s}`);
}

// ------------------------------------------------- sidecar query plumbing

type Waiter = { name: string; resolve: (r: unknown) => void; reject: (e: Error) => void; timer: NodeJS.Timeout };
let waiters: Waiter[] = [];

/** Send a `command` verb and resolve with the next matching command_result. */
function query(name: string, args: Record<string, unknown> = {}): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (!session) {
      reject(new Error("not connected"));
      return;
    }
    const timer = setTimeout(() => {
      waiters = waiters.filter((w) => w !== waiter);
      reject(new Error(`timed out waiting for command_result:${name}`));
    }, 120_000);
    const waiter: Waiter = { name, resolve, reject, timer };
    waiters.push(waiter);
    try {
      session.client.command(name, args);
    } catch (e) {
      clearTimeout(timer);
      waiters = waiters.filter((w) => w !== waiter);
      reject(e instanceof Error ? e : new Error(String(e)));
    }
  });
}

function routeCommandResult(ev: SidecarEvent): void {
  const idx = waiters.findIndex((w) => w.name === String(ev.name));
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

function refreshViews(): void {
  contractView?.refresh();
  journalView?.refresh();
  learningsView?.refresh();
  skillsView?.refresh();
  contextView?.refresh();
  modesView?.refresh();
}

function updateStatusBar(): void {
  if (!session?.ready) {
    statusBar.text = "$(circle-slash) A.W.I.N.O.: not connected";
    statusBar.tooltip = "A.W.I.N.O. sidecar is not running";
    return;
  }
  const binding = (session.ready["binding"] ?? {}) as Record<string, unknown>;
  const mode = (session.lastStatus?.["active_mode"] ?? session.ready["active_mode"] ?? {}) as Record<string, unknown>;
  const provider = String(binding["provider"] ?? session.ready["provider"] ?? "?");
  const env = String(binding["environment"] ?? "(global)");
  const modeId = String(mode["id"] ?? "?");
  const persona = session.lastStatus?.["persona"] as Record<string, unknown> | null;
  statusBar.text = `$(hubot) ${provider} · ${env} · ${modeId}${persona ? ` · ${persona["skill"]}` : ""}`;
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

// ------------------------------------------------------------------ chat webview

function postToChat(msg: unknown): void {
  chatPanel?.webview.postMessage(msg);
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
    view.webview.html = loadWebviewHtml(this.ctx, "chat.html").replace(
      "{{CHAT_JS}}",
      String(view.webview.asWebviewUri(vscode.Uri.joinPath(webviewDir, "chat.js")))
    );
    view.webview.onDidReceiveMessage((m) => void handleChatMessage(m));
    view.onDidDispose(() => {
      chatPanel = null;
    });
    // initial state push
    view.webview.postMessage({
      type: "state",
      connected: !!session?.ready,
      ready: session?.ready ?? null,
      status: session?.lastStatus ?? null,
    });
  }
}

function loadWebviewHtml(ctx: vscode.ExtensionContext, file: string): string {
  const p = path.join(ctx.extensionPath, "webview", file);
  return fs.readFileSync(p, "utf8");
}

async function handleChatMessage(m: { type: string; [k: string]: unknown }): Promise<void> {
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
    case "approve":
      session.client.approve(String(m.id), m.decision === "deny" ? "deny" : "approve");
      break;
    case "models":
      await vscode.commands.executeCommand("awino.openModels");
      break;
    default:
      log(`unknown chat message type: ${m.type}`);
  }
}

// ------------------------------------------------------- event routing

async function onSidecarEvent(ev: SidecarEvent): Promise<void> {
  switch (ev.event) {
    case "ready":
      if (session) {
        session.ready = ev;
      }
      updateStatusBar();
      postToChat({ type: "event", payload: ev });
      postToChat({ type: "state", connected: true, ready: ev });
      await refreshStatus();
      break;
    case "turn_result": {
      const result = (ev["result"] ?? {}) as Record<string, unknown>;
      if (session) {
        session.lastStatus = {
          active_mode: result["active_mode"],
          persona: result["persona"],
          provider: result["provider"],
          phase: result["phase"],
        };
      }
      updateStatusBar();
      postToChat({ type: "event", payload: ev });
      await refreshStatus();
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
      vscode.window.showWarningMessage(`A.W.I.N.O.: ${String(ev.message)}`);
      postToChat({ type: "event", payload: ev });
      break;
    case "error":
      log(`sidecar error: ${String(ev.message)}`);
      postToChat({ type: "event", payload: ev });
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
  } catch (e) {
    log(`status refresh failed: ${e}`);
  }
}

// ------------------------------------------------------------- approvals

interface ApprovalItem {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  diff?: string;
  old_exists?: boolean;
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
    ]
      .filter(Boolean)
      .join("\n");
    const choice = await vscode.window.showWarningMessage(
      `A.W.I.N.O. requests approval: ${a.tool}`,
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
    "A.W.I.N.O.: context window is filling — compact history?",
    { modal: true, detail: detail.slice(0, 4000) },
    "Approve compaction",
    "Deny"
  );
  const proposalId = String(ev["proposal_id"] ?? "");
  session.client.approve(proposalId, choice === "Approve compaction" ? "approve" : "deny");
}

// ------------------------------------------------------------ connection

async function connect(context: vscode.ExtensionContext): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    log("no workspace folder open — sidecar not started");
    return;
  }
  const cfg = readConfig();
  const secrets = context.secrets;
  const env: Record<string, string> = {};
  const openaiKey = await secrets.get(KEY_OPENAI);
  const anthropicKey = await secrets.get(KEY_ANTHROPIC);
  if (openaiKey) {
    env["AWINO_API_KEY"] = openaiKey;
  }
  if (anthropicKey) {
    env["ANTHROPIC_API_KEY"] = anthropicKey;
  }

  await disconnect();
  const client = new SidecarClient();
  client.on("log", (s: string) => output.append(s.replace(/\n$/, "")));
  client.on("event", (ev: SidecarEvent) => void onSidecarEvent(ev));
  session = {
    client,
    alwaysAllow: new Set(),
    ready: null,
    lastStatus: null,
  };
  updateStatusBar();
  try {
    const ready = await client.start({
      python: cfg.pythonPath,
      sidecarPath: defaultSidecarPath(context.extensionPath),
      workspace: folder.uri.fsPath,
      provider: cfg.provider,
      model: cfg.model || undefined,
      endpoint: cfg.endpoint || undefined,
      timeout: cfg.timeout,
      env,
      mcpServers: cfg.mcpServers,
    });
    log(`connected: ${JSON.stringify({ provider: ready["provider"], model: ready["model"], project: ready["project"] })}`);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    vscode.window.showErrorMessage(`A.W.I.N.O.: sidecar failed to start — ${msg}`);
    log(`connect failed: ${msg}`);
    session = null;
    updateStatusBar();
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
}

// -------------------------------------------------------------- commands

function mustSession(): Session {
  if (!session) {
    throw new Error("A.W.I.N.O. is not connected (open a workspace folder first)");
  }
  return session;
}

function registerCommands(context: vscode.ExtensionContext): void {
  const reg = (id: string, fn: (...args: unknown[]) => unknown) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, (...a) => fn(...a)));

  reg("awino.reconnect", () => connect(context));
  reg("awino.refreshViews", () => refreshViews());

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
    vscode.window.showInformationMessage(`A.W.I.N.O.: mission started — ${String(r["status"] ?? "ok")}`);
    refreshViews();
  });

  reg("awino.newMissionFromSeed", async () => {
    mustSession();
    const seeds = (await query("seeds_list")) as { seeds?: Array<{ name: string }> };
    const names = (seeds.seeds ?? []).map((x) => x.name);
    if (!names.length) {
      vscode.window.showInformationMessage("A.W.I.N.O.: no seeds saved yet");
      return;
    }
    const pick = await vscode.window.showQuickPick(names, { placeHolder: "Pick a mission seed" });
    if (!pick) {
      return;
    }
    const r = (await query("mission_from_seed", { name: pick })) as Record<string, unknown>;
    vscode.window.showInformationMessage(`A.W.I.N.O.: mission from seed — ${String(r["status"] ?? "ok")}`);
    refreshViews();
  });

  reg("awino.saveSeed", async () => {
    mustSession();
    const name = await vscode.window.showInputBox({ prompt: "Seed name", placeHolder: "my-mission-template" });
    if (!name) {
      return;
    }
    await query("seed_save", { name });
    vscode.window.showInformationMessage(`A.W.I.N.O.: seed saved as ${name}`);
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

  reg("awino.rollback", async () => {
    mustSession();
    const seqRaw = await vscode.window.showInputBox({ prompt: "Roll back effects at or after sequence number", placeHolder: "42" });
    const seq = Number(seqRaw);
    if (!Number.isInteger(seq) || seq < 0) {
      vscode.window.showErrorMessage("A.W.I.N.O.: seq must be a non-negative integer");
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
    vscode.window.showInformationMessage(`A.W.I.N.O.: rollback — ${JSON.stringify(r).slice(0, 300)}`);
    refreshViews();
  });

  reg("awino.housekeep", async () => {
    mustSession();
    const r = (await query("housekeeping", { reason: "manual" })) as Record<string, unknown>;
    const hk = (r["housekeeping"] ?? {}) as Record<string, unknown>;
    vscode.window.showInformationMessage(
      `A.W.I.N.O.: housekeeping done — archived ${hk["archived"] ?? 0} file(s), manifest written`
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
      vscode.window.showWarningMessage(`A.W.I.N.O.: skill refused — ${String(r["detail"] ?? r["status"])}`);
    } else {
      vscode.window.showInformationMessage(`A.W.I.N.O.: skill admitted — ${name.trim()} (sha ${(String(r["sha256"] ?? "")).slice(0, 12)})`);
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
    vscode.window.showInformationMessage(`A.W.I.N.O.: context file added — ${name.trim()}`);
    refreshViews();
  });

  reg("awino.removeContext", async () => {
    mustSession();
    const files = (await query("context_list")) as { files?: Array<{ name: string }> };
    const names = (files.files ?? []).map((f) => f.name);
    if (!names.length) {
      vscode.window.showInformationMessage("A.W.I.N.O.: no context files");
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
    await context.secrets.store(provider.key, value);
    vscode.window.showInformationMessage(`A.W.I.N.O.: key stored securely. Reconnect to apply.`);
  });

  reg("awino.clearApiKey", async () => {
    const provider = await vscode.window.showQuickPick(
      [
        { label: "OpenAI-compatible (AWINO_API_KEY)", key: KEY_OPENAI },
        { label: "Anthropic (ANTHROPIC_API_KEY)", key: KEY_ANTHROPIC },
      ],
      { placeHolder: "Which provider's key to clear?" }
    );
    if (!provider) {
      return;
    }
    await context.secrets.delete(provider.key);
    vscode.window.showInformationMessage(`A.W.I.N.O.: key cleared from secret storage.`);
  });

  reg("awino.invokeMode", async () => {
    mustSession();
    const modes = (await query("mode_list")) as {
      modes?: Array<{ id: string; label: string; custom: boolean }>;
      active?: { id?: string };
    };
    const items = (modes.modes ?? []).map((m) => ({
      label: `${m.id === modes.active?.id ? "● " : ""}${m.label}`,
      description: m.id,
      id: m.id,
    }));
    const pick = await vscode.window.showQuickPick(items, { placeHolder: "Invoke mode (overlay — stage default stays underneath)" });
    if (!pick) {
      return;
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
        vscode.window.showErrorMessage("A.W.I.N.O.: turns must be a positive integer");
        return;
      }
    }
    const r = (await query("mode_invoke", { mode: pick.id, scope, ...(turns ? { turns } : {}) })) as Record<string, unknown>;
    if (r["status"] === "refused") {
      vscode.window.showWarningMessage(`A.W.I.N.O.: mode refused — ${String(r["code"] ?? "")}`);
    } else {
      vscode.window.showInformationMessage(`A.W.I.N.O.: mode invoked — ${pick.id} (${scope})`);
    }
    refreshViews();
  });

  reg("awino.dismissMode", async () => {
    mustSession();
    await query("mode_dismiss");
    vscode.window.showInformationMessage("A.W.I.N.O.: back to stage-default mode");
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
      vscode.window.showInformationMessage("A.W.I.N.O.: no admitted skills to personify");
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
      vscode.window.showErrorMessage("A.W.I.N.O.: turns must be a positive integer");
      return;
    }
    const r = (await query("persona_assume", { skill: pick, turns })) as Record<string, unknown>;
    if (r["status"] === "refused") {
      vscode.window.showWarningMessage(`A.W.I.N.O.: persona refused — ${String(r["detail"] ?? r["code"])}`);
    } else {
      vscode.window.showInformationMessage(`A.W.I.N.O.: persona assumed — ${pick} (${turns} turns)`);
    }
    refreshViews();
  });

  reg("awino.dismissPersona", async () => {
    mustSession();
    await query("persona_dismiss");
    vscode.window.showInformationMessage("A.W.I.N.O.: persona dismissed");
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
      vscode.window.showWarningMessage(`A.W.I.N.O.: environment switch refused — ${String(r["detail"] ?? r["code"])}`);
    } else {
      vscode.window.showInformationMessage(`A.W.I.N.O.: environment switched — ${pick}`);
      await refreshStatus();
      refreshViews();
    }
  });

  reg("awino.openModels", () => openModelsPanel(context));
}

// --------------------------------------------------------------- doctor

async function doctorProject(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    vscode.window.showWarningMessage("A.W.I.N.O.: open a folder first");
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
    findings.push({
      label: "No Python virtualenv (.venv)",
      fix: "Create with python3 -m venv .venv",
      run: () =>
        new Promise((resolve, reject) => {
          const p = spawn("python3", ["-m", "venv", ".venv"], { cwd: root });
          let err = "";
          p.stderr.on("data", (d) => (err += d));
          p.on("close", (code) => (code === 0 ? resolve("created .venv") : reject(new Error(err.slice(0, 200) || `venv failed (code ${code})`))));
        }),
    });
  }
  if (exists("package.json") && !exists("node_modules")) {
    findings.push({
      label: "node_modules missing",
      fix: "Run npm install",
      run: () =>
        new Promise((resolve, reject) => {
          const p = spawn("npm", ["install", "--no-audit", "--no-fund"], { cwd: root });
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
          const p = spawn("git", ["init"], { cwd: root });
          p.on("close", (code) => (code === 0 ? resolve("git init done") : reject(new Error(`git init failed (code ${code})`))));
        }),
    });
  }

  if (!findings.length) {
    vscode.window.showInformationMessage("A.W.I.N.O. Doctor: project looks healthy — nothing to fix.");
    return;
  }
  for (const f of findings) {
    const choice = await vscode.window.showWarningMessage(
      `A.W.I.N.O. Doctor: ${f.label}`,
      { modal: true, detail: "Approval-gated fix. Nothing runs without your explicit approval." },
      `Apply: ${f.fix}`,
      "Skip"
    );
    if (choice?.startsWith("Apply")) {
      try {
        const done = await f.run();
        vscode.window.showInformationMessage(`A.W.I.N.O. Doctor: ${done}`);
      } catch (e) {
        vscode.window.showErrorMessage(`A.W.I.N.O. Doctor: fix failed — ${e instanceof Error ? e.message : String(e)}`);
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
    "A.W.I.N.O.: Models & Providers",
    vscode.ViewColumn.Beside,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [webviewDir],
    }
  );
  modelsPanel = panel;
  panel.webview.html = loadWebviewHtml(context, "models.html").replace(
    "{{MODELS_JS}}",
    String(panel.webview.asWebviewUri(vscode.Uri.joinPath(webviewDir, "models.js")))
  );
  panel.onDidDispose(() => {
    modelsPanel = null;
  });
  panel.webview.onDidReceiveMessage(async (m: { type: string; [k: string]: unknown }) => {
    switch (m.type) {
      case "init": {
        const cfg = readConfig();
        const folder = vscode.workspace.workspaceFolders?.[0];
        panel.webview.postMessage({
          type: "state",
          config: cfg,
          binding: session?.ready?.["binding"] ?? null,
          environments: folder ? listEnvironments(folder.uri.fsPath) : [],
          openaiKeySet: !!(await context.secrets.get(KEY_OPENAI)),
          anthropicKeySet: !!(await context.secrets.get(KEY_ANTHROPIC)),
        });
        break;
      }
      case "save": {
        const cfg = vscode.workspace.getConfiguration("awino");
        await cfg.update("provider", String(m.provider ?? "echo"), vscode.ConfigurationTarget.Workspace);
        await cfg.update("endpoint", String(m.endpoint ?? ""), vscode.ConfigurationTarget.Workspace);
        await cfg.update("model", String(m.model ?? ""), vscode.ConfigurationTarget.Workspace);
        await cfg.update("timeout", Number(m.timeout ?? 180), vscode.ConfigurationTarget.Workspace);
        if (typeof m.openaiKey === "string" && m.openaiKey) {
          await context.secrets.store(KEY_OPENAI, m.openaiKey);
        }
        if (typeof m.anthropicKey === "string" && m.anthropicKey) {
          await context.secrets.store(KEY_ANTHROPIC, m.anthropicKey);
        }
        if (m.clearKeys) {
          await context.secrets.delete(KEY_OPENAI);
          await context.secrets.delete(KEY_ANTHROPIC);
        }
        vscode.window.showInformationMessage("A.W.I.N.O.: settings saved — reconnecting sidecar…");
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
  output = vscode.window.createOutputChannel("A.W.I.N.O.");
  context.subscriptions.push(output);
  log("activating");

  const queryFn: QueryFn = (name, args) => query(name, args ?? {});
  contractView = new ContractView(queryFn);
  journalView = new JournalView(queryFn);
  learningsView = new LearningsView(queryFn);
  skillsView = new SkillsView(queryFn);
  contextView = new ContextView(queryFn);
  modesView = new ModesView(queryFn);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("awino.contract", contractView),
    vscode.window.registerTreeDataProvider("awino.journal", journalView),
    vscode.window.registerTreeDataProvider("awino.learnings", learningsView),
    vscode.window.registerTreeDataProvider("awino.skills", skillsView),
    vscode.window.registerTreeDataProvider("awino.context", contextView),
    vscode.window.registerTreeDataProvider("awino.modes", modesView),
    vscode.window.registerWebviewViewProvider("awino.chat", new ChatViewProvider(context))
  );

  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "awino.openModels";
  context.subscriptions.push(statusBar);
  statusBar.show();
  updateStatusBar();

  registerCommands(context);

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
      if (e.affectsConfiguration("awino")) {
        log("awino.* settings changed — reconnect to apply");
      }
    })
  );
}

export async function deactivate(): Promise<void> {
  await disconnect();
}
