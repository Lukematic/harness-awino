"use strict";
// test/bundleSidecar.js — the bundler must never ship test/scratch trees.
// Regression guard: a dev checkout accumulates prototype/awino-* dirs and
// .pytest_cache; the 0.4.1-vs-bloated packaging bug shipped 1,165 files.
const fs = require("fs");
const path = require("path");

const DEST = path.resolve(__dirname, "..", "bundled-sidecar");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

function walk(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else out.push(path.relative(DEST, p));
  }
  return out;
}

async function main() {
  // Requiring the script runs its main() (it copies prototype/ -> DEST).
  require("../scripts/bundle-sidecar.js");
  ok(fs.existsSync(path.join(DEST, "awino_sidecar.py")),
     "bundled-sidecar contains awino_sidecar.py");
  const files = walk(DEST, []);
  ok(!files.some(f => f.split(path.sep)[0].startsWith("awino-")),
     "no generated awino-* directories ship (found " +
     files.filter(f => f.split(path.sep)[0].startsWith("awino-")).length + ")");
  ok(!files.some(f => f.includes(".pytest_cache")),
     "no .pytest_cache ships");
  ok(!files.some(f => f.split(path.sep)[0] === "tests"),
     "no prototype/tests tree ships");
  ok(!files.some(f => f.endsWith(".pyc")),
     "no compiled .pyc files ship");
  ok(files.length < 150,
     "bundle stays small (" + files.length + " files, was 1165 when bloated)");
  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main();
