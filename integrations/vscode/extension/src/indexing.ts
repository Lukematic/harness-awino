/**
 * Codebase index integration (Cursor-inspired, local-first).
 *
 * - After connect: starts a workspace file watcher and kicks off the
 *   initial index build via the sidecar (`index_rebuild`).
 * - File saves/changes/deletes: debounced incremental `index_rebuild`
 *   with the touched paths (the sidecar's change detection makes this
 *   cheap — unchanged files are skipped by hash).
 * - Status bar: "Indexing…" while a build is in flight, "Indexed N
 *   files" when done.
 * - `@codebase <query>` mentions in chat: runs `index_query`
 *   (search_symbols) and injects the results as context before the turn.
 *   A bare `@codebase` uses related_files() on the active editor file.
 *
 * Everything here is a read or a rebuild trigger — the index itself is
 * written only by the sidecar's indexer path.
 */

import * as vscode from "vscode";

export type QueryFn = (name: string, args?: Record<string, unknown>) => Promise<unknown>;

let indexStatusBar: vscode.StatusBarItem | null = null;
let watcher: vscode.FileSystemWatcher | null = null;
let queryFn: QueryFn | null = null;
let indexing = false;
let lastCount = -1;
let debounce: NodeJS.Timeout | null = null;
let pendingPaths = new Set<string>();
let connected = false;

export function initIndexing(context: vscode.ExtensionContext, q: QueryFn): void {
  queryFn = q;
  indexStatusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
  indexStatusBar.command = "awino.reindex";
  indexStatusBar.tooltip = "Awino codebase index — click to rebuild";
  context.subscriptions.push(indexStatusBar);
  context.subscriptions.push({ dispose: () => stopIndexing() });
}

function setStatus(): void {
  if (!indexStatusBar) {
    return;
  }
  if (!connected) {
    indexStatusBar.hide();
    return;
  }
  indexStatusBar.show();
  if (indexing) {
    indexStatusBar.text = "$(sync~spin) Awino: Indexing…";
    indexStatusBar.tooltip = "Awino is indexing your codebase…";
  } else if (lastCount >= 0) {
    indexStatusBar.text = `$(database) Awino: Indexed ${lastCount} files`;
    indexStatusBar.tooltip = "Awino codebase index is up to date — click to rebuild";
  } else {
    indexStatusBar.text = "$(database) Awino: Index not built";
    indexStatusBar.tooltip = "Awino codebase index — click to build";
  }
}

/** Called from the sidecar `ready` handler. Starts the watcher (once per
 * workspace folder) and kicks off the initial build. */
export function onIndexConnected(): void {
  connected = true;
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder && !watcher) {
    // Watch everything; the sidecar's indexer skips node_modules/.git/
    // binaries itself, and unchanged files are skipped by content hash.
    watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(folder, "**/*")
    );
    const touch = (uri: vscode.Uri) => {
      const rel = vscode.workspace.asRelativePath(uri, false);
      pendingPaths.add(rel);
      scheduleReindex();
    };
    watcher.onDidChange(touch);
    watcher.onDidCreate(touch);
    watcher.onDidDelete(touch);
  }
  setStatus();
  void refreshIndexStatus(true);
}

/** Called when the sidecar disconnects. */
export function onIndexDisconnected(): void {
  connected = false;
  indexing = false;
  if (debounce) {
    clearTimeout(debounce);
    debounce = null;
  }
  pendingPaths.clear();
  stopWatcher();
  setStatus();
}

function stopWatcher(): void {
  if (watcher) {
    watcher.dispose();
    watcher = null;
  }
}

function stopIndexing(): void {
  onIndexDisconnected();
}

function scheduleReindex(): void {
  if (!connected || !queryFn) {
    return;
  }
  if (debounce) {
    clearTimeout(debounce);
  }
  debounce = setTimeout(() => {
    debounce = null;
    const paths = [...pendingPaths];
    pendingPaths.clear();
    void runRebuild(paths.length ? { paths } : {});
  }, 2000);
}

async function runRebuild(args: Record<string, unknown>): Promise<void> {
  if (!queryFn || indexing) {
    return;
  }
  indexing = true;
  setStatus();
  try {
    const r = (await queryFn("index_rebuild", args)) as {
      index?: { files?: number };
      status?: string;
    };
    if (r && typeof r.index?.files === "number") {
      lastCount = r.index.files;
    }
  } catch {
    // Best effort — the status bar just keeps its last state.
  } finally {
    indexing = false;
    setStatus();
  }
}

/** Manual rebuild (status-bar click / command). */
export async function reindexNow(force = false): Promise<void> {
  pendingPaths.clear();
  if (debounce) {
    clearTimeout(debounce);
    debounce = null;
  }
  await runRebuild(force ? { force: true } : {});
}

async function refreshIndexStatus(buildIfMissing: boolean): Promise<void> {
  if (!queryFn) {
    return;
  }
  // index_status auto-builds on first call (sidecar side); show the
  // spinner while that initial build runs.
  indexing = true;
  setStatus();
  try {
    const r = (await queryFn("index_status")) as {
      index?: { indexed?: boolean; files?: number; built?: boolean };
      status?: string;
    };
    const idx = r?.index;
    if (idx?.indexed && typeof idx.files === "number") {
      lastCount = idx.files;
    }
  } catch {
    // Best effort.
  } finally {
    indexing = false;
    setStatus();
  }
  void buildIfMissing;
}

/**
 * Expand `@codebase <query>` mentions before the message goes to the
 * sidecar. Each mention is replaced by the message text plus an injected
 * context block with the top symbol hits. A bare `@codebase` (no query)
 * injects files related to the active editor file instead.
 */
export async function expandCodebaseMentions(text: string): Promise<string> {
  if (!queryFn || !connected || !text.includes("@codebase")) {
    return text;
  }
  const blocks: string[] = [];
  const mention = /@codebase(?:\s+([^\n@]{1,120}))?/g;
  let m: RegExpExecArray | null;
  const seen = new Set<string>();
  while ((m = mention.exec(text)) !== null) {
    const rawQuery = (m[1] ?? "").trim();
    const key = rawQuery || "<related>";
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    try {
      if (rawQuery) {
        const r = (await queryFn("index_query", {
          op: "search_symbols",
          query: rawQuery,
          limit: 12,
        })) as { results?: Array<Record<string, unknown>> };
        const hits = r?.results ?? [];
        if (hits.length) {
          const lines = hits.map(
            (h) => `- ${String(h["name"])} (${String(h["kind"])}) — ${String(h["file"])}:${String(h["line"])}\n  ${String(h["signature"] ?? "").slice(0, 160)}`
          );
          blocks.push(
            `[codebase index: symbols matching "${rawQuery}"]\n${lines.join("\n")}`
          );
        } else {
          blocks.push(`[codebase index: no symbols matching "${rawQuery}"]`);
        }
      } else {
        const editor = vscode.window.activeTextEditor;
        const rel = editor
          ? vscode.workspace.asRelativePath(editor.document.uri, false)
          : "";
        if (rel) {
          const r = (await queryFn("index_query", {
            op: "related_files",
            path: rel,
            limit: 10,
          })) as { results?: Array<{ file?: string; reasons?: string[] }> };
          const hits = r?.results ?? [];
          if (hits.length) {
            const lines = hits.map(
              (h) => `- ${String(h.file)} (${(h.reasons ?? []).join("; ")})`
            );
            blocks.push(
              `[codebase index: files related to ${rel}]\n${lines.join("\n")}`
            );
          }
        }
      }
    } catch {
      // A failed lookup must never block the message.
    }
  }
  if (!blocks.length) {
    return text;
  }
  return `${text}\n\n${blocks.join("\n\n")}`;
}
