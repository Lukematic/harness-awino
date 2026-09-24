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
import { execFile } from "child_process";
import { commonPythonLocations, isPython3VersionOutput } from "./python";

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
  /**
   * Pre-flight check for the picked file (must behave like a Python 3
   * interpreter). Override for tests; defaults to a real `--version` run.
   */
  validatePython?: (exePath: string) => Promise<boolean>;
  log: (msg: string) => void;
}

export type RecoveryOutcome = "located" | "dismissed" | "cancelled";

/**
 * Default picked-file validation: the file must execute `--version`
 * successfully and identify as Python 3. Never throws.
 */
function defaultValidatePython(exePath: string): Promise<boolean> {
  return new Promise((resolve) => {
    execFile(exePath, ["--version"], { timeout: 10_000 }, (err, stdout, stderr) => {
      resolve(!err && isPython3VersionOutput(String(stdout), String(stderr)));
    });
  });
}

/**
 * Where to write `awino.pythonPath` after the user picks an interpreter.
 *
 * Returns "workspaceFolder" when a folder-level value already exists — at
 * resolve time workspaceFolderValue beats workspaceValue beats globalValue,
 * so writing anywhere else would leave the stale folder value (common in
 * multi-root workspaces) in effect and the pick would silently do nothing.
 * Returns "workspace" when only a workspace-level value exists, "global"
 * otherwise. Pure function over the `inspect()` shape so node tests can
 * drive it.
 */
export type PythonPathWriteLevel = "workspaceFolder" | "workspace" | "global";
export function pickPythonPathWriteLevel(
  inspect: { workspaceFolderValue?: unknown; workspaceValue?: unknown } | undefined
): PythonPathWriteLevel {
  if (inspect?.workspaceFolderValue !== undefined) {
    return "workspaceFolder";
  }
  return inspect && inspect.workspaceValue !== undefined ? "workspace" : "global";
}

export async function offerPythonRecovery(
  deps: PythonRecoveryDeps,
  detail: string
): Promise<RecoveryOutcome> {
  const platform = deps.platform ?? process.platform;
  const locations = commonPythonLocations(platform)
    .map((l) => `\u2022 ${l}`)
    .join("\n");
  const choice = await deps.showErrorMessage(
    // 0.5.0+: this dialog is rare — it only appears when the bundled
    // runtime is missing/unusable AND no system interpreter was found.
    `Awino: no Python interpreter found \u2014 the bundled runtime is missing or unusable and no system Python was found, so the sidecar can't start.\n\n${detail}\n\nCommon locations:\n${locations}`,
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
    // Windows: restrict the picker to executables — a picked .zip/.txt can
    // never be an interpreter and used to fail later at spawn time.
    ...(platform === "win32" ? { filters: { Executables: ["exe"] } } : {}),
  });
  if (!picked || picked.length === 0) {
    return "cancelled";
  }
  const exe = picked[0].fsPath;
  // Pre-flight: the picked file must actually run `--version` as Python 3.
  // Without this the retry just fails again at spawn with a bare ENOENT.
  const valid = await (deps.validatePython ?? defaultValidatePython)(exe);
  if (!valid) {
    deps.log(`picked file failed the Python 3 --version check: ${exe}`);
    await deps.showErrorMessage(
      `Awino: "${exe}" doesn't look like a Python 3 interpreter (its --version check failed) — pick another file.`
    );
    return "cancelled";
  }
  await deps.savePythonPath(exe);
  deps.log(`awino.pythonPath set to ${exe} \u2014 retrying sidecar connect`);
  await deps.retryConnect();
  return "located";
}
