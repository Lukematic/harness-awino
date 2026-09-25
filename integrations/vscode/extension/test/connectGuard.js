"use strict";
// test/connectGuard.js — unit tests for src/connectGuard.ts (BUG 1: the
// connect() in-flight guard + generation token).
//
// connect() is reachable from auto-connect, the Reconnect command, and the
// Locate-Python recovery retry. The guard proves: concurrent callers join
// the in-flight run (one sidecar spawn, never two), and a superseded run's
// isCurrent() goes false so its catch can't null a newer live session.
// vscode-free; run after `npm run compile`:
//   node ./test/connectGuard.js

const { ConnectGuard } = require("../out/connectGuard.js");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function main() {
  // 1. Concurrent callers join the in-flight run: fn executes exactly once.
  {
    const g = new ConnectGuard();
    let runs = 0;
    const gate = deferred();
    const fn = async () => { runs++; await gate.promise; };
    const p1 = g.run(fn);
    const p2 = g.run(fn);
    ok(p1 === p2, "concurrent run() calls share the in-flight promise");
    await Promise.resolve();
    await Promise.resolve();
    ok(runs === 1, "fn executed exactly once for two concurrent callers");
    gate.resolve();
    await p1;
    await p2;
    ok(runs === 1, "still one execution after both joiners settle");
    ok(g.currentGeneration === 1, "one generation consumed by the shared run");
  }

  // 2. Sequential runs each execute with a fresh generation.
  {
    const g = new ConnectGuard();
    let runs = 0;
    const gens = [];
    await g.run(async (gen) => { runs++; gens.push(gen); });
    await g.run(async (gen) => { runs++; gens.push(gen); });
    ok(runs === 2, "sequential runs each execute");
    ok(gens[0] === 1 && gens[1] === 2, "generation increments per run");
  }

  // 3. Stale-run protection: run A releases the guard (as the connect
  // catch does before the recovery dialog) and parks; run B starts. A's
  // isCurrent() must be false so a stale catch can't null B's session,
  // and A's settlement must not clear B's guard slot.
  {
    const g = new ConnectGuard();
    let aIsCurrent = null;
    let bGen = null;
    const gateA = deferred();
    const pA = g.run(async (gen, isCurrent) => {
      aIsCurrent = isCurrent;
      g.release(); // the catch releases before the Locate-Python dialog
      await gateA.promise; // parked in the dialog while B runs
    });
    await Promise.resolve(); // let A's synchronous prefix run + release
    const pB = g.run(async (gen) => { bGen = gen; });
    await pB;
    ok(bGen === 2, "a new run starts after release (generation 2)");
    ok(aIsCurrent !== null && aIsCurrent() === false,
      "superseded run's isCurrent() is false");
    gateA.resolve();
    await pA;
    let runs = 0;
    await g.run(async () => { runs++; });
    ok(runs === 1, "guard slot free and usable after the stale run settles");
    ok(g.currentGeneration === 3, "generations consumed: A=1, B=2, final=3");
  }

  // 4. A rejecting run propagates to every joiner and frees the guard.
  {
    const g = new ConnectGuard();
    const p1 = g.run(async () => { throw new Error("boom"); });
    const p2 = g.run(async () => { throw new Error("must never run"); });
    ok(p1 === p2, "joiners share the failing run's promise");
    let e1 = null, e2 = null;
    await p1.catch((e) => { e1 = e; });
    await p2.catch((e) => { e2 = e; });
    ok(e1 !== null && e1.message === "boom", "rejection reaches the first caller");
    ok(e2 === e1, "rejection reaches the joiner (same run, no second spawn)");
    let runs = 0;
    await g.run(async () => { runs++; });
    ok(runs === 1, "guard is free after a rejection");
  }

  // 5. A run that never releases: late callers keep joining, never spawning.
  {
    const g = new ConnectGuard();
    let runs = 0;
    const gate = deferred();
    const p1 = g.run(async () => { runs++; await gate.promise; });
    await Promise.resolve();
    const p3 = g.run(async () => { runs++; });
    ok(p3 === p1, "third caller joins the still-running first run");
    gate.resolve();
    await p1;
    ok(runs === 1, "no extra execution for the late joiner");
  }

  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
