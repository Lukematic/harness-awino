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
    out.push(new Leaf("Phase", esc(status["phase"] ?? "?")));
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
      return new Leaf(`#${esc(e["seq"])} ${esc(e["tool"])}`, `${summary}${reused}`);
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
        `${m.id}${m.custom ? " (custom)" : ""} · ${m.stages.join("/")}`
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
