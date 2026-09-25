/**
 * Pure (vscode-free) helpers for native tool application.
 *
 * Kept free of the vscode module so the Node test suite can exercise
 * them without an extension host. The vscode-dependent application
 * itself lives in nativeApply.ts / awinoTerminal.ts and is proven by
 * the Windows GUI workflow (see NATIVE_APPLY_GUI_ASSERTIONS.md).
 */
import * as crypto from "crypto";
import * as path from "path";

/** sha256 hex of a UTF-8 string — the digest the loop and the extension
 *  both compute so a mismatch proves the bytes diverged. */
export function sha256Hex(s: string): string {
  return crypto.createHash("sha256").update(s, "utf8").digest("hex");
}

/** Strip ANSI escape sequences (colors, cursor moves, OSC 633 shell-
 *  integration markers) from terminal output chunks. */
export function stripAnsi(s: string): string {
  // CSI sequences: ESC [ ... final byte
  // OSC sequences: ESC ] ... terminated by BEL or ESC \
  return s
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, "")
    .replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, "")
    .replace(/\x1b[()][0-9A-B]/g, "")
    .replace(/\x1b[=>M78]/g, "");
}

export interface DelegatedEditWire {
  call_id: string;
  idem_key: string;
  tool: string;
  path: string;
  content: string;
  old_digest: string | null;
}

/**
 * Validate one delegated edit and resolve it against the workspace root.
 * Returns the absolute path, or an error string. Rejects absolute paths,
 * drive letters, ".." escapes, and non-string payloads — the sidecar
 * already validated, this is defense in depth on the applying side.
 */
export function resolveDelegatedPath(
  workspaceRoot: string,
  edit: DelegatedEditWire
): { absPath: string } | { error: string } {
  if (typeof edit.path !== "string" || !edit.path) {
    return { error: `edit ${edit.call_id}: missing path` };
  }
  if (typeof edit.content !== "string") {
    return { error: `edit ${edit.call_id}: missing content` };
  }
  if (edit.tool !== "write_file" && edit.tool !== "patch_file") {
    return { error: `edit ${edit.call_id}: unknown tool ${edit.tool}` };
  }
  const rel = edit.path.replace(/\\/g, "/");
  if (
    path.posix.isAbsolute(rel) ||
    /^[a-zA-Z]:/.test(rel) ||
    rel.split("/").includes("..")
  ) {
    return { error: `edit ${edit.call_id}: path escapes workspace` };
  }
  const absPath = path.resolve(workspaceRoot, rel);
  const root = path.resolve(workspaceRoot);
  if (absPath !== root && !absPath.startsWith(root + path.sep)) {
    return { error: `edit ${edit.call_id}: path escapes workspace` };
  }
  return { absPath };
}
