"use strict";
// test/bundledPython.js — unit tests for src/bundledPython.ts.
// Uses fixture directories; no vscode API, no real runtimes needed.
const fs = require("fs");
const os = require("os");
const path = require("path");

const { bundledRid, bundledRuntimePath, prepareBundledRuntime } = require("../out/bundledPython.js");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

/** Fixture extension dir with python/<rid>/<bin> present for every rid. */
function makeFixture() {
  const fx = fs.mkdtempSync(path.join(os.tmpdir(), "awino-bundled-"));
  const trees = {
    "win32-x64": "python.exe",
    "linux-x64": path.join("bin", "python3"),
    "darwin-arm64": path.join("bin", "python3"),
    "darwin-x64": path.join("bin", "python3"),
  };
  for (const [rid, bin] of Object.entries(trees)) {
    const p = path.join(fx, "python", rid, bin);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, "fake");
  }
  return fx;
}

async function main() {
  const fx = makeFixture();
  try {
    // 1. all four platform/arch mappings resolve to the right binary
    const cases = [
      ["win32", "x64", path.join("python", "win32-x64", "python.exe")],
      ["linux", "x64", path.join("python", "linux-x64", "bin", "python3")],
      ["darwin", "arm64", path.join("python", "darwin-arm64", "bin", "python3")],
      ["darwin", "x64", path.join("python", "darwin-x64", "bin", "python3")],
    ];
    for (const [plat, arch, suffix] of cases) {
      const got = bundledRuntimePath(plat, arch, fx);
      ok(got === path.join(fx, suffix), `bundledRuntimePath(${plat}, ${arch}) -> ${suffix}`);
    }

    // 2. unknown platform / arch -> null
    ok(bundledRuntimePath("sunos", "x64", fx) === null, "unknown platform -> null");
    ok(bundledRuntimePath("linux", "arm", fx) === null, "linux/arm (unshipped) -> null");
    ok(bundledRuntimePath("win32", "arm64", fx) === null, "win32/arm64 (unshipped) -> null");
    ok(bundledRuntimePath("", "", fx) === null, "empty platform/arch -> null");

    // 3. missing runtime dir -> null
    const empty = fs.mkdtempSync(path.join(os.tmpdir(), "awino-bundled-empty-"));
    ok(bundledRuntimePath("linux", "x64", empty) === null, "missing python/ dir -> null");

    // 4. dir present but binary missing -> null
    const nobin = fs.mkdtempSync(path.join(os.tmpdir(), "awino-bundled-nobin-"));
    fs.mkdirSync(path.join(nobin, "python", "linux-x64", "bin"), { recursive: true });
    ok(bundledRuntimePath("linux", "x64", nobin) === null, "dir without binary -> null");

    // 5. bundledRid mapping
    ok(bundledRid("win32", "x64") === "win32-x64", "bundledRid win32/x64");
    ok(bundledRid("linux", "x64") === "linux-x64", "bundledRid linux/x64");
    ok(bundledRid("darwin", "arm64") === "darwin-arm64", "bundledRid darwin/arm64");
    ok(bundledRid("darwin", "x64") === "darwin-x64", "bundledRid darwin/x64");
    ok(bundledRid("freebsd", "x64") === null, "bundledRid unknown platform -> null");

    // 6. prepareBundledRuntime: chmods on unix, no-op on win32, never throws
    const tmpf = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "awino-chmod-")), "py");
    fs.writeFileSync(tmpf, "x");
    fs.chmodSync(tmpf, 0o644);
    if (process.platform === "win32") {
      prepareBundledRuntime(tmpf, "win32");
      ok(true, "prepareBundledRuntime win32 no-op (skipped chmod assertion on win32)");
    } else {
      prepareBundledRuntime(tmpf, "linux");
      const mode = fs.statSync(tmpf).mode & 0o777;
      ok(mode === 0o755, `prepareBundledRuntime ensures 0755 on unix (got ${mode.toString(8)})`);
    }
    prepareBundledRuntime(tmpf, "win32"); // must not throw on any platform
    ok(true, "prepareBundledRuntime win32 arg never throws");
    prepareBundledRuntime(path.join(fx, "nope", "missing"), "linux"); // missing file
    ok(true, "prepareBundledRuntime on missing file never throws");
  } finally {
    fs.rmSync(fx, { recursive: true, force: true });
  }

  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
