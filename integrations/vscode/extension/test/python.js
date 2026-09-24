"use strict";
// test/python.js — unit tests for src/python.ts (interpreter resolution).
// The probe is mocked: no real processes are spawned.

const { resolvePythonInterpreter, describeSpawnFailure, isInterpreterNotFound, commonPythonLocations, defaultProbe, isPython3VersionOutput, interpreterFixHint } = require("../out/python.js");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

/** Build a mock probe from a map cmd -> boolean|"throw"; records call order. */
function mockProbe(results) {
  const calls = [];
  const fn = async (cmd) => {
    calls.push(cmd);
    const r = results[cmd];
    if (r === "throw") throw new Error("probe exploded");
    return r === true;
  };
  fn.calls = calls;
  return fn;
}

async function main() {
  // 1. explicit configuration wins; probe never runs
  {
    const probe = mockProbe({ py: true });
    const r = await resolvePythonInterpreter({ configured: "C:\\Python311\\python.exe", platform: "win32", probe });
    ok(r.python === "C:\\Python311\\python.exe", "configured interpreter used verbatim");
    ok(r.source === "configured", "source is configured");
    ok(probe.calls.length === 0, "probe not called when configured");
    ok(r.tried.length === 0, "tried empty when configured");
  }

  // 2. win32 order: py fails, python wins -> python3 never probed
  {
    const probe = mockProbe({ py: false, python: true, python3: true });
    const r = await resolvePythonInterpreter({ platform: "win32", probe });
    ok(r.python === "python", "win32 resolves to first working candidate (python)");
    ok(r.source === "auto-detected", "source is auto-detected");
    ok(JSON.stringify(probe.calls) === JSON.stringify(["py", "python"]), "probe order py -> python, stops at first win");
    ok(JSON.stringify(r.tried) === JSON.stringify(["py", "python"]), "tried lists probed candidates in order");
  }

  // 3. win32: py wins immediately
  {
    const probe = mockProbe({ py: true });
    const r = await resolvePythonInterpreter({ platform: "win32", probe });
    ok(r.python === "py", "win32 prefers py when it works");
    ok(JSON.stringify(probe.calls) === JSON.stringify(["py"]), "stops after first success");
  }

  // 4. win32: all fail -> python3 default, full order recorded
  {
    const probe = mockProbe({ py: false, python: false, python3: false });
    const r = await resolvePythonInterpreter({ platform: "win32", probe });
    ok(r.python === "python3", "win32 falls back to python3 when nothing probes clean");
    ok(r.source === "default", "source is default when auto-detect finds nothing");
    ok(JSON.stringify(probe.calls) === JSON.stringify(["py", "python", "python3"]), "full candidate order probed");
  }

  // 5. throwing probe counts as failure, next candidate tried
  {
    const probe = mockProbe({ py: "throw", python: true });
    const r = await resolvePythonInterpreter({ platform: "win32", probe });
    ok(r.python === "python", "throwing probe treated as failure, next candidate wins");
  }

  // 6. non-win32: no probing, straight to python3
  {
    const probe = mockProbe({ python3: true });
    for (const platform of ["linux", "darwin"]) {
      const r = await resolvePythonInterpreter({ platform, probe });
      ok(r.python === "python3", platform + " keeps python3 default");
      ok(r.source === "default", platform + " source is default");
    }
    ok(probe.calls.length === 0, "no probing on non-win32");
  }

  // 7. empty/blank configured counts as unset -> win32 auto-detect runs
  {
    const probe = mockProbe({ py: true });
    const r = await resolvePythonInterpreter({ configured: "   ", platform: "win32", probe });
    ok(r.python === "py", "blank configured treated as unset");
    ok(probe.calls.length === 1, "probe runs when configured is blank");
  }

  // 8. custom candidates honored in order
  {
    const probe = mockProbe({ "/usr/bin/python3": true });
    const r = await resolvePythonInterpreter({ platform: "win32", probe, candidates: ["/usr/bin/python3"] });
    ok(r.python === "/usr/bin/python3", "custom candidate list honored");
  }

  // 9. failure detail: OS error + tried interpreters + fix hint
  {
    const detail = describeSpawnFailure(
      { python: "python3", source: "default", tried: ["py", "python", "python3"] },
      "spawn python3 ENOENT"
    );
    ok(detail.indexOf("spawn python3 ENOENT") >= 0, "detail includes the OS error");
    ok(detail.indexOf("py, python, python3") >= 0, "detail names the tried interpreters");
    ok(detail.indexOf("awino.pythonPath") >= 0, "detail includes the fix hint");
  }
  {
    const detail = describeSpawnFailure(
      { python: "C:\\py.exe", source: "configured", tried: [] },
      "spawn C:\\py.exe ENOENT"
    );
    ok(detail.indexOf("C:\\py.exe") >= 0, "detail names the configured interpreter");
    ok(detail.indexOf("awino.pythonPath") >= 0, "configured failure hint points at the setting");
  }

  // 10. isInterpreterNotFound: only missing-interpreter errors trigger recovery
  {
    ok(isInterpreterNotFound("spawn python3 ENOENT") === true, "bare ENOENT counts as not-found");
    ok(isInterpreterNotFound("Error: spawn C:\\Python311\\python.exe ENOENT") === true, "configured-path ENOENT counts as not-found");
    ok(isInterpreterNotFound("spawn python3 EACCES") === false, "EACCES is not not-found");
    ok(isInterpreterNotFound("sidecar exited with code 1") === false, "exit code is not not-found");
    ok(isInterpreterNotFound("") === false, "empty error is not not-found");
  }

  // 11. commonPythonLocations: per-platform install hints for the recovery message
  {
    const win = commonPythonLocations("win32");
    ok(win.some((l) => l.indexOf("py") >= 0), "win32 lists the py launcher");
    ok(win.some((l) => l.indexOf("LOCALAPPDATA") >= 0), "win32 lists %LOCALAPPDATA%\\Programs\\Python");
    ok(win.some((l) => l.indexOf("Microsoft Store") >= 0), "win32 lists Microsoft Store Python");
    const mac = commonPythonLocations("darwin");
    ok(mac.indexOf("/usr/local/bin/python3") >= 0, "macOS lists /usr/local/bin/python3");
    ok(mac.some((l) => l.indexOf("/opt/homebrew/bin/python3") >= 0), "macOS lists Homebrew /opt/homebrew/bin/python3");
    ok(mac.some((l) => l.indexOf("Xcode") >= 0), "macOS lists Xcode CLT");
    const lin = commonPythonLocations("linux");
    ok(lin.indexOf("/usr/bin/python3") >= 0, "linux lists /usr/bin/python3");
  }

  // 12. bundled beats auto-detect; probe never runs when bundled is present
  {
    const probe = mockProbe({ py: true, python: true, python3: true });
    const r = await resolvePythonInterpreter({
      platform: "win32",
      probe,
      bundledPath: "C:\\ext\\python\\win32-x64\\python.exe",
    });
    ok(r.python === "C:\\ext\\python\\win32-x64\\python.exe", "bundled interpreter used when present");
    ok(r.source === "bundled", "source is bundled");
    ok(probe.calls.length === 0, "probe not called when bundled runtime is present");
  }

  // 13. explicit configuration still wins over the bundled runtime
  {
    const probe = mockProbe({ py: true });
    const r = await resolvePythonInterpreter({
      configured: "C:\\Python311\\python.exe",
      platform: "win32",
      probe,
      bundledPath: "C:\\ext\\python\\win32-x64\\python.exe",
    });
    ok(r.python === "C:\\Python311\\python.exe", "configured wins over bundled");
    ok(r.source === "configured", "source is configured when both set");
  }

  // 14. bundled absent -> legacy auto-detect still used
  {
    const probe = mockProbe({ py: false, python: true });
    const r = await resolvePythonInterpreter({ platform: "win32", probe });
    ok(r.python === "python", "auto-detect used when no bundled runtime");
    ok(r.source === "auto-detected", "source is auto-detected");
  }

  // 15. bundled blank/whitespace is treated as absent
  {
    const probe = mockProbe({ py: true });
    const r = await resolvePythonInterpreter({ platform: "win32", probe, bundledPath: "   " });
    ok(r.python === "py", "blank bundledPath falls through to auto-detect");
    ok(r.source === "auto-detected", "source is auto-detected");
  }

  // 16. isPython3VersionOutput: exit code alone is not trusted (LIKELY-BREAK 5)
  ok(isPython3VersionOutput("Python 3.12.14\n", ""), "stdout 'Python 3.12.14' accepted");
  ok(isPython3VersionOutput("", "Python 3.9.1\n"), "stderr 'Python 3.9.1' accepted (2.x prints to stderr)");
  ok(isPython3VersionOutput("warning: foo\nPython 3.11.0\n", ""), "multiline output with a prefix line accepted");
  ok(!isPython3VersionOutput("Python 2.7.18\n", ""), "Python 2.7 in stdout rejected");
  ok(!isPython3VersionOutput("", "Python 2.7.18\n"), "Python 2.7 in stderr rejected");
  ok(!isPython3VersionOutput("", ""), "empty output (Windows Store stub) rejected");
  ok(!isPython3VersionOutput("v20.19.0\n", ""), "non-Python version string rejected");

  // 17. defaultProbe: real --version runs; never throws; version-gated
  {
    const bogus = await defaultProbe("definitely-not-a-real-binary-awino-xyz");
    ok(bogus === false, "defaultProbe false for a nonexistent binary (never throws)");
    // The node binary answers --version with "vNN.N.N" — not Python 3.
    const nodeBin = await defaultProbe(process.execPath);
    ok(nodeBin === false, "defaultProbe rejects a non-Python binary that exits 0");
  }

  // 18. interpreterFixHint: bundled source must not say "install Python 3"
  {
    const hint = interpreterFixHint({ python: "/x/python", source: "bundled", tried: [] });
    ok(hint.indexOf("bundled") >= 0, "bundled hint names the bundled runtime");
    ok(hint.indexOf("install Python 3") < 0, "bundled hint does not tell a zero-setup user to install Python");
    const def = interpreterFixHint({ python: "python3", source: "default", tried: [] });
    ok(def.indexOf("install Python 3") >= 0, "default hint still advises installing Python 3");
    const cfg = interpreterFixHint({ python: "/x", source: "configured", tried: [] });
    ok(cfg.indexOf("awino.pythonPath") >= 0, "configured hint points at awino.pythonPath");
  }

  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
