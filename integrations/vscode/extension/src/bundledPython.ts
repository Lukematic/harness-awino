/**
 * bundledPython.ts — resolve the CPython interpreter bundled inside the VSIX.
 *
 * 0.5.0 ships a portable python-build-standalone runtime per platform under
 * `python/<rid>/` (see scripts/fetch-python-runtimes.js). When present it is
 * used in preference to any system interpreter, so the extension works with
 * zero setup — no Python install, no PATH fiddling.
 *
 * Kept free of the `vscode` API so plain node tests can exercise the
 * platform/arch mapping with fixture directories.
 */

import * as fs from "fs";
import * as path from "path";

export type BundledRid = "win32-x64" | "linux-x64" | "darwin-arm64" | "darwin-x64";

const RID_BY_PLATFORM: Record<string, Record<string, BundledRid>> = {
  win32: { x64: "win32-x64" },
  linux: { x64: "linux-x64" },
  darwin: { arm64: "darwin-arm64", x64: "darwin-x64" },
};

const BINARY_BY_RID: Record<BundledRid, string> = {
  "win32-x64": "python.exe",
  "linux-x64": path.join("bin", "python3"),
  "darwin-arm64": path.join("bin", "python3"),
  "darwin-x64": path.join("bin", "python3"),
};

/** The runtime id for a platform/arch pair, or null when we ship none. */
export function bundledRid(platform: string, arch: string): BundledRid | null {
  return RID_BY_PLATFORM[platform]?.[arch] ?? null;
}

/**
 * Absolute path of the bundled interpreter for this platform/arch, or null
 * when the runtime directory or binary is absent (e.g. a hand-built
 * install, or an unexpected arch). `extensionPath` is the installed
 * extension directory (context.extensionPath).
 */
export function bundledRuntimePath(
  platform: string,
  arch: string,
  extensionPath: string
): string | null {
  const rid = bundledRid(platform, arch);
  if (!rid) {
    return null;
  }
  const dir = path.join(extensionPath, "python", rid);
  const bin = path.join(dir, BINARY_BY_RID[rid]);
  try {
    if (!fs.statSync(dir).isDirectory()) {
      return null;
    }
    // statSync follows the bin/python3 -> python3.12 symlink on unix.
    if (!fs.statSync(bin).isFile()) {
      return null;
    }
  } catch {
    return null;
  }
  return bin;
}

/**
 * Best-effort preparation before spawning the bundled interpreter: the
 * VSIX zip may not preserve unix executable bits, so ensure +x. No-op on
 * win32. Never throws.
 */
export function prepareBundledRuntime(absPath: string, platform: string): void {
  if (platform === "win32") {
    return;
  }
  try {
    fs.chmodSync(absPath, 0o755);
  } catch {
    // best effort — the spawn error path will surface a real failure
  }
}
