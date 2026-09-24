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

  // 2. Locate + pick: saves to awino.pythonPath and retries the sidecar
  {
    const { calls, deps } = fakeDeps({
      choice: "Locate Python...",
      picked: [{ fsPath: "/usr/bin/python3" }],
      platform: "linux",
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

  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
