/**
 * bundle-sidecar.js — copies the harness's prototype/ tree into
 * ./bundled-sidecar/ so `vsce package` ships the Python sidecar INSIDE the
 * .vsix. Without this, an installed extension cannot spawn its sidecar:
 * defaultSidecarPath()'s repo-relative fallback only exists in a dev
 * checkout, never under ~/.vscode/extensions/.
 *
 * The sidecar is stdlib-only; the whole directory rides along so sibling
 * imports (backends, loop, contract, judges, skills, synthesis, tools)
 * resolve when the script's own directory is on sys.path.
 *
 * Run via `npm run vscode:prepublish` (vsce invokes it automatically).
 */
"use strict";
const fs = require("fs");
const path = require("path");

const REPO_PROTOTYPE = path.resolve(__dirname, "..", "..", "..", "..", "prototype");
const DEST = path.resolve(__dirname, "..", "bundled-sidecar");
// Build/test artifacts that must never ship in the vsix.
const SKIP_DIRS = new Set([
  "tests",
  "build",
  "__pycache__",
  ".venv",
  "venv",
  ".pytest_cache",
  "awino_loop.egg-info",
]);

// Generated test/scratch directories (e.g. awino-sidecar-test-*, awino-bind-*)
// must never ship either, even when they linger in a dev checkout.
function isGeneratedDir(name) {
  return name.startsWith("awino-");
}

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, e.name);
    const d = path.join(dst, e.name);
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name) || isGeneratedDir(e.name)) {
        continue;
      }
      copyDir(s, d);
    } else if (e.isFile()) {
      if (e.name.endsWith(".pyc") || e.name.endsWith(".pyo")) {
        continue;
      }
      fs.copyFileSync(s, d);
    }
  }
}

function main() {
  if (!fs.existsSync(path.join(REPO_PROTOTYPE, "awino_sidecar.py"))) {
    console.error(
      "bundle-sidecar: prototype/awino_sidecar.py not found at " + REPO_PROTOTYPE
    );
    process.exit(1);
  }
  fs.rmSync(DEST, { recursive: true, force: true });
  copyDir(REPO_PROTOTYPE, DEST);
  const main = path.join(DEST, "awino_sidecar.py");
  if (!fs.existsSync(main)) {
    console.error("bundle-sidecar: copy failed, missing " + main);
    process.exit(1);
  }
  console.log("bundle-sidecar: prototype/ -> " + DEST);
}

main();
