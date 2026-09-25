/**
 * views.ts — TreeDataProviders for the A.W.I.N.O. activity-bar views.
 *
 * Each provider pulls its data from the sidecar through the injected
 * `query(name, args)` function (a thin wrapper around the sidecar
 * `command` verb). Providers are refreshed by the extension host
 * whenever a turn_result / command_result arrives, or on demand.
 */

import * as vscode from "vscode";

export type QueryFn = (name: string, args?: Record<string, unknown>) => Promise<unknown>;

function esc(s: unknown): string {
  return String(s ?? "");
}

class Leaf extends vscode.TreeItem {
  constructor(label: string, description?: string, tooltip?: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.tooltip = tooltip ?? description;
  }
}

class Group extends vscode.TreeItem {
  children: vscode.TreeItem[] = [];
  constructor(label: string) {
    super(label, vscode.TreeItemCollapsibleState.Expanded);
  }
}

export { Group };

abstract class BaseView implements vscode.TreeDataProvider<vscode.TreeItem> {
  protected query: QueryFn;
  private emitter = new vscode.EventEmitter<vscode.TreeItem | undefined | null>();
  readonly onDidChangeTreeData = this.emitter.event;

  constructor(query: QueryFn) {
    this.query = query;
  }

  refresh(): void {
    this.emitter.fire(undefined);
  }

  getTreeItem(item: vscode.TreeItem): vscode.TreeItem {
    return item;
  }

  abstract getChildren(
    element?: vscode.TreeItem
  ): vscode.ProviderResult<vscode.TreeItem[]>;
}

/** Contract view: mission, phase, mode, live criteria checkboxes, scope, approvals. */
export class ContractView extends BaseView {
  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element instanceof Group) {
      return element.children;
    }
    let st: Record<string, unknown>;
    try {
      st = (await this.query("status")) as Record<string, unknown>;
    } catch (e) {
      return [new Leaf("sidecar unavailable", esc(e))];
    }
    const status = (st["status"] ?? {}) as Record<string, unknown>;
    const out: vscode.TreeItem[] = [];
    const mission = status["mission"];
    out.push(new Leaf("Mission", mission ? esc(mission) : "(none — open a mission to start)"));
    out.push(new Leaf("Phase", esc(status["phase"] ?? "?").toUpperCase()));
    const mode = (status["active_mode"] ?? {}) as Record<string, unknown>;
    out.push(new Leaf("Mode", `${esc(mode["id"] ?? "?")} (${esc(mode["source"] ?? "?")})`));
    const persona = status["persona"] as Record<string, unknown> | null;
    out.push(new Leaf("Persona", persona ? esc(persona["skill"]) : "(none)"));
    const crit = (status["criteria"] ?? []) as Array<{ ok: boolean; label: string }>;
    if (crit.length) {
      const g = new Group(`Done criteria (${crit.filter((c) => c.ok).length}/${crit.length})`);
      for (const c of crit) {
        g.children.push(new Leaf(`${c.ok ? "☑" : "☐"} ${c.label}`));
      }
      out.push(g);
    }
    const approvals = (status["pending_approvals"] ?? []) as string[];
    out.push(new Leaf("Pending approvals", String(approvals.length)));
    out.push(new Leaf("Scope", esc(status["scope"])));
    out.push(new Leaf("Turns", String(status["turns"] ?? 0)));
    const next = status["next_action"];
    if (next) {
      out.push(new Leaf("Next", esc(next)));
    }
    return out;
  }
}

/** Journal view: effect journal (seq, tool, args summary). */
export class JournalView extends BaseView {
  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element instanceof Group) {
      return element.children;
    }
    let r: Record<string, unknown>;
    try {
      r = (await this.query("journal")) as Record<string, unknown>;
    } catch (e) {
      return [new Leaf("sidecar unavailable", esc(e))];
    }
    const entries = (r["journal"] ?? []) as Array<Record<string, unknown>>;
    if (!entries.length) {
      return [new Leaf("(no journal entries yet)")];
    }
    return entries.slice(-50).reverse().map((e) => {
      const args = e["args"] as Record<string, unknown> | undefined;
      const summary = args
        ? Object.entries(args)
            .slice(0, 2)
            .map(([k, v]) => `${k}=${esc(v).slice(0, 40)}`)
            .join(" ")
        : "";
      const reused = e["reused"] ? " (reused)" : "";
      const tool = esc(e["tool"]);
      const denied = tool === "deny" || tool === "approval_denied" || tool === "error";
      const approved = tool.indexOf("approv") === 0;
      const mark = denied ? "✕ " : approved ? "✓ " : "";
      return new Leaf(`${mark}#${esc(e["seq"])} ${tool}`, `${summary}${reused}`);
    });
  }
}

/** Learnings view: recorded learnings + synthesized-skill status. */
export class LearningsView extends BaseView {
  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element instanceof Group) {
      return element.children;
    }
    let r: Record<string, unknown>;
    try {
      r = (await this.query("learnings")) as Record<string, unknown>;
    } catch (e) {
      return [new Leaf("sidecar unavailable", esc(e))];
    }
    const learnings = (r["learnings"] ?? []) as Array<Record<string, unknown>>;
    const flags = (r["flags"] ?? []) as Array<Record<string, unknown>>;
    const out: vscode.TreeItem[] = [];
    if (learnings.length) {
      const g = new Group(`Learnings (${learnings.length})`);
      for (const l of learnings.slice(-20).reverse()) {
        g.children.push(
          new Leaf(esc(l["text"] ?? l["summary"] ?? JSON.stringify(l)).slice(0, 80))
        );
      }
      out.push(g);
    }
    if (flags.length) {
      const g = new Group(`Flags (${flags.length})`);
      for (const f of flags.slice(-10).reverse()) {
        g.children.push(new Leaf(esc(f["text"] ?? JSON.stringify(f)).slice(0, 80)));
      }
      out.push(g);
    }
    if (!out.length) {
      out.push(new Leaf("(no learnings recorded yet)"));
    }
    return out;
  }
}

/** Skills view: packaged + project-admitted skills with short sha256. */
export class SkillsView extends BaseView {
  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element instanceof Group) {
      return element.children;
    }
    let r: Record<string, unknown>;
    try {
      r = (await this.query("skills_list")) as Record<string, unknown>;
    } catch (e) {
      return [new Leaf("sidecar unavailable", esc(e))];
    }
    const out: vscode.TreeItem[] = [];
    const show = (title: string, skills: Array<{ name: string; sha256: string }>) => {
      if (!skills.length) {
        return;
      }
      const g = new Group(`${title} (${skills.length})`);
      for (const s of skills) {
        g.children.push(new Leaf(s.name, (s.sha256 ?? "").slice(0, 12), `sha256: ${s.sha256}`));
      }
      out.push(g);
    };
    show("Packaged", (r["packaged"] ?? []) as Array<{ name: string; sha256: string }>);
    show("Project", (r["project"] ?? []) as Array<{ name: string; sha256: string }>);
    if (r["error"]) {
      out.push(new Leaf("registry error", esc(r["error"])));
    }
    if (!out.length) {
      out.push(new Leaf("(no skills)"));
    }
    return out;
  }
}

/** Context view: .awino/context.md + .awino/context/*.md with reorder support. */
export class ContextView extends BaseView {
  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element instanceof Group) {
      return element.children;
    }
    let r: Record<string, unknown>;
    try {
      r = (await this.query("context_list")) as Record<string, unknown>;
    } catch (e) {
      return [new Leaf("sidecar unavailable", esc(e))];
    }
    const files = (r["files"] ?? []) as Array<{ name: string; path: string; chars: number }>;
    if (!files.length) {
      return [new Leaf("(no context files — Add Context File to create one)")];
    }
    return files.map((f, i) => {
      const item = new Leaf(`${i + 1}. ${f.name}`, `${f.path} · ${f.chars} chars`);
      item.contextValue = "awinoContextFile";
      (item as unknown as { __awinoFile: unknown }).__awinoFile = f;
      return item;
    });
  }

  static fileOf(item: vscode.TreeItem): { name: string; path: string } | null {
    const f = (item as unknown as { __awinoFile?: { name: string; path: string } }).__awinoFile;
    return f ?? null;
  }
}

/** Modes view: all modes with the active one highlighted; invoke via command. */
export class ModesView extends BaseView {
  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element instanceof Group) {
      return element.children;
    }
    let r: Record<string, unknown>;
    try {
      r = (await this.query("mode_list")) as Record<string, unknown>;
    } catch (e) {
      return [new Leaf("sidecar unavailable", esc(e))];
    }
    const modes = (r["modes"] ?? []) as Array<{
      id: string;
      label: string;
      stages: string[];
      sampling: Record<string, unknown>;
      custom: boolean;
    }>;
    const active = (r["active"] ?? {}) as { id?: string; source?: string };
    return modes.map((m) => {
      const isActive = m.id === active.id;
      const item = new Leaf(
        `${isActive ? "● " : "○ "}${m.label}`,
        `${m.id}${m.custom ? " (custom)" : ""}${isActive ? " (active)" : ""} · ${m.stages.join("/")}`
      );
      item.tooltip = `Stages: ${m.stages.join(", ")}. Temperature: ${
        (m.sampling ?? {})["temperature"] ?? "?"
      }${isActive ? `\nACTIVE (source: ${active.source ?? "?"})` : ""}`;
      item.contextValue = "awinoMode";
      (item as unknown as { __awinoMode: unknown }).__awinoMode = m;
      return item;
    });
  }

  static modeOf(item: vscode.TreeItem): { id: string; label: string } | null {
    const m = (item as unknown as { __awinoMode?: { id: string; label: string } }).__awinoMode;
    return m ?? null;
  }
}

/**
 * Tasks view: the harness registry task tracker (Track B).
 *
 * Read-only mirror of exactly what the registry believes. Task states
 * change only in code (registry.set_task_state on verified completion) —
 * this view never invents, edits, or checks off tasks. When no registry
 * is attached yet (no mission started in the project), it says so
 * instead of showing a stale or fabricated list.
 */
const TASK_STATE_MARK: Record<string, string> = {
  doing: "◐",
  open: "○",
  blocked: "✕",
  done: "☑",
};

export class TasksView extends BaseView {
  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element instanceof Group) {
      return element.children;
    }
    let r: Record<string, unknown>;
    try {
      r = (await this.query("tasks_list")) as Record<string, unknown>;
    } catch (e) {
      return [new Leaf("sidecar unavailable", esc(e))];
    }
    const tasks = (r["tasks"] ?? []) as Array<Record<string, unknown>>;
    // Registry failed to load (hello-time re-attach blew up): show the
    // error, not a misleadingly empty list. This is distinct from "no
    // registry attached yet" below.
    if (r["error"]) {
      return [new Leaf("registry failed to load", esc(r["error"]))];
    }
    if (!tasks.length) {
      return [
        new Leaf(
          r["attached"]
            ? "(no tasks tracked yet)"
            : "(no registry attached yet — the harness tracks tasks once a mission starts)"
        ),
      ];
    }
    const order = ["doing", "open", "blocked", "done"];
    const out: vscode.TreeItem[] = [];
    for (const state of order) {
      const group_tasks = tasks.filter((t) => String(t["state"]) === state);
      if (!group_tasks.length) {
        continue;
      }
      const g = new Group(`${state} (${group_tasks.length})`);
      for (const t of group_tasks) {
        const mark = TASK_STATE_MARK[state] ?? "?";
        const label = `${mark} ${esc(t["text"])}`;
        const descBits = [esc(t["source"] ?? "")].filter(Boolean);
        const dc = String(t["done_criteria"] ?? "").trim();
        if (dc) {
          descBits.push(dc.slice(0, 60));
        }
        const deps = (t["depends_on"] ?? []) as unknown[];
        const item = new Leaf(label, descBits.join(" · ") || undefined);
        // Seed-imported checklist items are human attestation, not harness
        // verification — the tooltip must not claim otherwise.
        const isSeedTask = String(t["source"] ?? "").startsWith("seed:");
        item.tooltip =
          `state: ${state}\n` +
          `id: ${esc(t["id"])}\n` +
          `source: ${esc(t["source"])}\n` +
          (dc ? `done criteria: ${dc}\n` : "") +
          (deps.length ? `depends on: ${deps.map(esc).join(", ")}\n` : "") +
          `evidence: ${(t["evidence"] as unknown[] ?? []).length} item(s)\n` +
          (isSeedTask
            ? `(read-only — seed-imported checklist item: human attestation, not harness-verified)`
            : `(read-only — states change only when the harness verifies completion)`);
        g.children.push(item);
      }
      out.push(g);
    }
    // Any task in an unexpected state still shows up rather than vanishing.
    const known = new Set(order);
    const other = tasks.filter((t) => !known.has(String(t["state"])));
    if (other.length) {
      const g = new Group(`other (${other.length})`);
      for (const t of other) {
        g.children.push(
          new Leaf(`? ${esc(t["text"])}`, `state: ${esc(t["state"])}`)
        );
      }
      out.push(g);
    }
    return out;
  }
}

export interface StoryRow {
  id: string;
  title: string;
  type?: string;
  status: string;
  problem?: string;
  outcome?: string;
  branch?: string;
  closed_ts?: number | null;
  time_s?: number;
  ready_to_close?: boolean;
  revisit_on?: string;
  revisit_due?: boolean;
}

/** Tree item for one story; carries its id for the context-menu commands. */
export class StoryItem extends vscode.TreeItem {
  constructor(readonly story: StoryRow, label: string, description?: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.contextValue = story.status === "done" ? "awinoStoryDone" : "awinoStory";
  }
}

export function formatDuration(seconds: number | undefined): string {
  const s = Math.max(0, Math.round(seconds ?? 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return s ? "<1m" : "0m";
}

function day(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toISOString().slice(0, 10);
}

/** Stories view: this project's issues by status, then the brag board. */
export class StoriesView extends BaseView {
  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (element instanceof Group) {
      return element.children;
    }
    let r: Record<string, unknown>;
    try {
      r = (await this.query("stories")) as Record<string, unknown>;
    } catch (e) {
      return [new Leaf("sidecar unavailable", esc(e))];
    }
    const stories = (r["stories"] ?? []) as StoryRow[];
    if (!stories.length) {
      return [new Leaf("(no stories yet — Start Story, or plan one in chat)")];
    }
    const groups: Array<[string, string]> = [
      ["doing", "In progress"],
      ["open", "Open"],
      ["blocked", "Blocked"],
      ["parked", "Parked ideas"],
    ];
    const out: vscode.TreeItem[] = [];
    for (const [status, name] of groups) {
      const rows = stories.filter((s) => s.status === status);
      if (!rows.length) continue;
      const g = new Group(`${name} (${rows.length})`);
      for (const s of rows) {
        const flags: string[] = [];
        if (s.ready_to_close) flags.push("ready to close");
        if (s.revisit_due) flags.push("revisit due");
        if (status === "parked" && s.revisit_on && !s.revisit_due) flags.push(`revisit ${s.revisit_on}`);
        flags.push(formatDuration(s.time_s));
        const item = new StoryItem(s, `${s.ready_to_close ? "★" : "•"} ${esc(s.title)}`, flags.join(" · "));
        item.tooltip =
          `${s.type ?? "story"} · ${status}\n` +
          (s.problem ? `problem: ${s.problem}\n` : "") +
          (s.branch ? `branch: ${s.branch}\n` : "") +
          `time dedicated: ${formatDuration(s.time_s)}\nid: ${s.id}`;
        g.children.push(item);
      }
      out.push(g);
    }
    const brag = ((r["brag"] ?? []) as StoryRow[]);
    const g = new Group(`Brag board (${brag.length})`);
    for (const s of brag) {
      const item = new StoryItem(
        s,
        `✓ ${esc(s.title)}`,
        [day(s.closed_ts), formatDuration(s.time_s), esc(s.outcome ?? "")].filter(Boolean).join(" · ")
      );
      item.tooltip = `closed ${day(s.closed_ts)}\noutcome: ${s.outcome ?? ""}\ntime dedicated: ${formatDuration(s.time_s)}`;
      g.children.push(item);
    }
    if (!brag.length) g.children.push(new Leaf("(nothing closed yet)"));
    out.push(g);
    return out;
  }
}
