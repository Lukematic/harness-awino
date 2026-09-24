"use strict";
// test/pythonRecovery.js — unit tests for src/pythonRecovery.ts
// (the "Locate Python..." recovery flow).
//
// pythonRecovery.ts imports "vscode" for types only, so the module is
// stubbed with an empty object and the entire vscode API surface the flow
// touches is injected as plain fakes through PythonRecoveryDeps. Run after
// `npm run compile`:
//   node ./test/pythonRecovery.js

const Module = require("module");
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "vscode") return {}; // types-only import
  return origLoad.call(this, request, parent, isMain);
};

const { offerPythonRecovery, pickPythonPathWriteLevel } = require("../out/pythonRecovery.js");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

/** Fake deps with scripted user behavior; records every call. */
function fakeDeps(opts) {
  const calls = { errorMessages: [], dialogs: 0, saves: [], retries: 0, logs: [] };
  return {
    calls,
    deps: {
      platform: opts.platform ?? "linux",
      showErrorMessage: async (message, ...items) => {
        calls.errorMessages.push({ message, items });
        return opts.choice; // e.g. "Locate Python...", "Dismiss", undefined
      },
      showOpenDialog: async (dialogOpts) => {
        calls.dialogs++;
        calls.dialogOpts = dialogOpts;
        return opts.picked; // e.g. [{ fsPath: "/usr/bin/python3" }] or []
      },
      savePythonPath: async (exe) => { calls.saves.push(exe); },
      retryConnect: async () => { calls.retries++; },
      // Injected by tests that need a deterministic verdict; when omitted
      // the real --version check runs (see test 8).
      validatePython: opts.validatePython,
      log: (m) => { calls.logs.push(m); },
    },
  };
}

async function main() {
  const DETAIL = "spawn python3 ENOENT — tried interpreter(s): python3.";

  // 1. Dismiss: no dialog, no save, no retry
  {
    const { calls, deps } = fakeDeps({ choice: "Dismiss", platform: "win32" });
    const out = await offerPythonRecovery(deps, DETAIL);
    ok(out === "dismissed", "dismiss returns 'dismissed'");
    ok(calls.dialogs === 0, "no file picker on dismiss");
    ok(calls.saves.length === 0, "no save on dismiss");
    ok(calls.retries === 0, "no retry on dismiss");
    const msg = calls.errorMessages[0];
    ok(msg.items.indexOf("Locate Python...") >= 0, "message offers the Locate Python... button");
    ok(msg.message.indexOf("py") >= 0 && msg.message.indexOf("Microsoft Store") >= 0,
      "win32 message lists the py launcher and Microsoft Store Python");
    ok(msg.message.indexOf(DETAIL) >= 0, "message includes the spawn failure detail");
  }

  // 2. Locate + pick: saves to awino.pythonPath and retries the sidecar.
  // validatePython is injected: the real --version check must not depend
  // on the machine running the tests (Windows CI has no /usr/bin/python3).
  {
    const { calls, deps } = fakeDeps({
      choice: "Locate Python...",
      picked: [{ fsPath: "/usr/bin/python3" }],
      platform: "linux",
      validatePython: async () => true,
    });
    const out = await offerPythonRecovery(deps, DETAIL);
    ok(out === "located", "locate+pick returns 'located'");
    ok(calls.dialogs === 1, "file picker shown once");
    ok(calls.dialogOpts.canSelectFiles === true && calls.dialogOpts.canSelectMany === false,
      "picker selects a single file");
    ok(calls.saves.length === 1 && calls.saves[0] === "/usr/bin/python3",
      "chosen executable saved as the python path");
    ok(calls.retries === 1, "sidecar retried after save");
    ok(calls.logs.length === 1, "save+retry logged");
  }

  // 3. Locate then cancel the picker: no save, no retry
  {
    const { calls, deps } = fakeDeps({ choice: "Locate Python...", picked: [] });
    const out = await offerPythonRecovery(deps, DETAIL);
    ok(out === "cancelled", "cancelled picker returns 'cancelled'");
    ok(calls.saves.length === 0, "no save when picker cancelled");
    ok(calls.retries === 0, "no retry when picker cancelled");
  }

  // 4. Notification dismissed (undefined choice): same as Dismiss
  {
    const { calls, deps } = fakeDeps({ choice: undefined });
    const out = await offerPythonRecovery(deps, DETAIL);
    ok(out === "dismissed", "dismissed notification returns 'dismissed'");
    ok(calls.dialogs === 0 && calls.saves.length === 0 && calls.retries === 0,
      "no dialog, save, or retry when notification dismissed");
  }

  // 5. macOS message names Homebrew and Xcode locations
  {
    const { calls, deps } = fakeDeps({ choice: "Dismiss", platform: "darwin" });
    await offerPythonRecovery(deps, DETAIL);
    const msg = calls.errorMessages[0].message;
    ok(msg.indexOf("/opt/homebrew/bin/python3") >= 0, "macOS message lists Homebrew python3");
    ok(msg.indexOf("Xcode") >= 0, "macOS message lists Xcode CLT");
  }

  // 6. pickPythonPathWriteLevel: a stale workspace-level value wins at
  // resolve time, so the recovery flow must write there — not global —
  // or the picked interpreter would silently not take effect.
  ok(pickPythonPathWriteLevel({ workspaceValue: "/stale/python" }) === "workspace",
    "workspace-level value present -> write to workspace");
  ok(pickPythonPathWriteLevel({ workspaceValue: "" }) === "workspace",
    "workspace-level value present even when empty string -> write to workspace");
  ok(pickPythonPathWriteLevel({ globalValue: "/usr/bin/python3" }) === "global",
    "only a global value present -> write to global");
  ok(pickPythonPathWriteLevel({}) === "global",
    "no value anywhere -> write to global");
  ok(pickPythonPathWriteLevel(undefined) === "global",
    "inspect() undefined (key undeclared) -> write to global");

  // 6b. LIKELY-BREAK 6: a stale workspace-FOLDER value wins at resolve time
  // (configuredPythonPath checks workspaceFolderValue first), so the pick
  // must be written back to the folder level — not workspace, not global.
  ok(pickPythonPathWriteLevel({ workspaceFolderValue: "/stale/folder/python" }) === "workspaceFolder",
    "workspaceFolder-level value present -> write to workspaceFolder");
  ok(pickPythonPathWriteLevel({ workspaceFolderValue: "/f", workspaceValue: "/w" }) === "workspaceFolder",
    "workspaceFolder beats workspace when both hold values");
  ok(pickPythonPathWriteLevel({ workspaceFolderValue: "" }) === "workspaceFolder",
    "workspaceFolder-level value present even when empty string -> write to workspaceFolder");

  // 7. win32 picker is restricted to executables; other platforms are not.
  {
    const { calls, deps } = fakeDeps({
      choice: "Locate Python...",
      picked: [{ fsPath: "C:\\Python312\\python.exe" }],
      platform: "win32",
      validatePython: async () => true,
    });
    const out = await offerPythonRecovery(deps, DETAIL);
    ok(out === "located", "win32 locate+pick returns 'located'");
    ok(calls.dialogOpts.filters && JSON.stringify(calls.dialogOpts.filters) === JSON.stringify({ Executables: ["exe"] }),
      "win32 picker carries the Executables/exe filter");
  }
  {
    const { calls, deps } = fakeDeps({
      choice: "Locate Python...",
      picked: [{ fsPath: "/usr/bin/python3" }],
      platform: "linux",
      validatePython: async () => true,
    });
    await offerPythonRecovery(deps, DETAIL);
    ok(calls.dialogOpts.filters === undefined, "non-Windows picker has no exe filter");
  }

  // 8. Picked-file --version pre-check: a file that isn't Python 3 never
  // reaches savePythonPath — the retry would just fail again at spawn.
  {
    const { calls, deps } = fakeDeps({
      choice: "Locate Python...",
      picked: [{ fsPath: "/tmp/not-python.txt" }],
      platform: "linux",
      validatePython: async (exe) => exe !== "/tmp/not-python.txt",
    });
    const out = await offerPythonRecovery(deps, DETAIL);
    ok(out === "cancelled", "failed --version pre-check returns 'cancelled'");
    ok(calls.saves.length === 0, "bad pick is never saved as awino.pythonPath");
    ok(calls.retries === 0, "no retry after a failed pre-check");
    ok(calls.errorMessages.some((m) => m.message.indexOf("--version") >= 0),
      "user is told the --version check failed");
  }

  // 9. Default validation is real: the node binary answers --version with
  // "vNN.N.N", not "Python 3.x", so it must be rejected without an
  // injected fake (deterministic on every platform).
  {
    const { calls, deps } = fakeDeps({
      choice: "Locate Python...",
      picked: [{ fsPath: process.execPath }],
      platform: "linux",
    });
    const out = await offerPythonRecovery(deps, DETAIL);
    ok(out === "cancelled", "default --version check rejects the node binary");
    ok(calls.saves.length === 0, "node binary never saved as the python path");
  }

  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
