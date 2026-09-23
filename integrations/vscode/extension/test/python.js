"use strict";
// test/python.js — unit tests for src/python.ts (interpreter resolution).
// The probe is mocked: no real processes are spawned.

const { resolvePythonInterpreter, describeSpawnFailure } = require("../out/python.js");

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

  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
