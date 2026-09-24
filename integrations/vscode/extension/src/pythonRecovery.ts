/**
 * pythonRecovery.ts — "Locate Python..." recovery flow for sidecar spawn
 * failures caused by a missing interpreter.
 *
 * When the sidecar can't spawn because no Python exists (ENOENT), the user
 * gets an error message that (a) lists the common install locations for
 * their platform and (b) offers a file picker. The chosen executable is
 * saved to `awino.pythonPath` at the most specific configuration level
 * that already holds a value (workspace wins over user/global) — a stale
 * workspace-level value would otherwise keep winning at resolve time and
 * the freshly picked interpreter would silently not take effect.
 *
 * All vscode access is injected through `PythonRecoveryDeps` so plain node
 * tests can drive the whole flow with a mocked API surface.
 */

import * as vscode from "vscode";
import { commonPythonLocations } from "./python";

export interface PythonRecoveryDeps {
  /** Override for tests; defaults to process.platform. */
  platform?: NodeJS.Platform;
  showErrorMessage: (
    message: string,
    ...items: string[]
  ) => Promise<string | undefined>;
  showOpenDialog: (
    options: vscode.OpenDialogOptions
  ) => Promise<vscode.Uri[] | undefined>;
  /** Persist the picked interpreter (awino.pythonPath). */
  savePythonPath: (exePath: string) => Promise<void>;
  /** Retry the sidecar connection after a successful pick. */
  retryConnect: () => Promise<void>;
  log: (msg: string) => void;
}

export type RecoveryOutcome = "located" | "dismissed" | "cancelled";

/**
 * Where to write `awino.pythonPath` after the user picks an interpreter.
 *
 * Returns "workspace" when a workspace-level value already exists — that
 * level wins at resolve time, so writing global/user would leave the stale
 * workspace value in effect and the pick would silently do nothing.
 * Returns "global" otherwise (fresh pick, or only a user-level value set).
 * Pure function over the `inspect()` shape so node tests can drive it.
 */
export type PythonPathWriteLevel = "workspace" | "global";
export function pickPythonPathWriteLevel(
  inspect: { workspaceValue?: unknown } | undefined
): PythonPathWriteLevel {
  return inspect && inspect.workspaceValue !== undefined ? "workspace" : "global";
}

export async function offerPythonRecovery(
  deps: PythonRecoveryDeps,
  detail: string
): Promise<RecoveryOutcome> {
  const locations = commonPythonLocations(deps.platform)
    .map((l) => `\u2022 ${l}`)
    .join("\n");
  const choice = await deps.showErrorMessage(
    `Awino: no Python interpreter found \u2014 the sidecar can't start.\n\n${detail}\n\nCommon locations:\n${locations}`,
    "Locate Python...",
    "Dismiss"
  );
  if (choice !== "Locate Python...") {
    return "dismissed";
  }
  const picked = await deps.showOpenDialog({
    canSelectFiles: true,
    canSelectFolders: false,
    canSelectMany: false,
    openLabel: "Use this Python",
    title: "Locate your Python 3 interpreter",
  });
  if (!picked || picked.length === 0) {
    return "cancelled";
  }
  const exe = picked[0].fsPath;
  await deps.savePythonPath(exe);
  deps.log(`awino.pythonPath set to ${exe} \u2014 retrying sidecar connect`);
  await deps.retryConnect();
  return "located";
}
