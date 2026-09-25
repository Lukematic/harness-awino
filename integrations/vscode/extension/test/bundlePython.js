"use strict";
// test/bundlePython.js — the 0.5.0 zero-setup guarantee: the staged
// python/<rid>/ runtime trees must be present for vsce to ship.
// The fetch script (scripts/fetch-python-runtimes.js) builds them; on a
// fresh checkout without it this test skips with a clear message.
const fs = require("fs");
const path = require("path");

const EXT_ROOT = path.resolve(__dirname, "..");
const PYTHON_DIR = path.join(EXT_ROOT, "python");

const EXPECTED = {
  "win32-x64": "python.exe",
  "linux-x64": path.join("bin", "python3"),
  "darwin-arm64": path.join("bin", "python3"),
  "darwin-x64": path.join("bin", "python3"),
};

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

async function main() {
  if (!fs.existsSync(PYTHON_DIR)) {
    console.log("SKIP - python/ not staged (run `node ./scripts/fetch-python-runtimes.js` first)");
    console.log("\n" + pass + " passed, " + fail + " failed");
    process.exit(0);
  }
  // Every shipped rid must have its runtime binary.
  for (const [rid, bin] of Object.entries(EXPECTED)) {
    const p = path.join(PYTHON_DIR, rid, bin);
    ok(fs.existsSync(p), `staged runtime binary present: python/${rid}/${bin.replace(/\\/g, "/")}`);
  }
  // The current platform's runtime must be resolvable through the real
  // mapping (mirrors src/bundledPython.ts without importing vscode code).
  const ridByPlatform = {
    "win32:x64": "win32-x64",
    "linux:x64": "linux-x64",
    "darwin:arm64": "darwin-arm64",
    "darwin:x64": "darwin-x64",
  };
  const rid = ridByPlatform[process.platform + ":" + process.arch];
  if (rid) {
    ok(fs.existsSync(path.join(PYTHON_DIR, rid, EXPECTED[rid])),
       `current platform (${process.platform}/${process.arch}) runtime staged: python/${rid}/`);
  } else {
    console.log(`note - no bundled runtime ships for ${process.platform}/${process.arch}; skipping platform check`);
  }
  // The win32 tree is the one Windows CI asserts inside the VSIX.
  ok(fs.existsSync(path.join(PYTHON_DIR, "win32-x64", "python.exe")),
     "win32-x64/python.exe staged (Windows CI asserts this inside the VSIX)");
  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
