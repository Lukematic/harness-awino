"use strict";
// test/vsixContents.js — WILL-BREAK 1: the .pbs-cache/ tarball cache must
// never ship inside the VSIX.
//
// vsce globs with dot:true and filters ONLY by .vscodeignore (it never
// reads .gitignore), and `vscode:prepublish` runs the runtime fetch during
// `vsce package` — so the cache always exists at pack time. Without a
// `.pbs-cache/**` line in .vscodeignore, all four runtime tarballs (~200MB
// of duplicates) get packed alongside the unpacked python/ trees.
//
// This test lists the built VSIX's central directory with a minimal
// dependency-free ZIP reader (no zip lib in node_modules) and asserts:
//   - no entry lives under .pbs-cache/
//   - the Windows runtime binary IS present (Windows CI asserts this too)
// Skips with a clear message when no .vsix has been built yet.

const fs = require("fs");
const path = require("path");

const EXT_ROOT = path.resolve(__dirname, "..");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

/**
 * List entry names from a ZIP's central directory. Dependency-free:
 * finds the End Of Central Directory record, then walks the central
 * headers (signatures 0x06054b50 / 0x02014b50).
 */
function listZipEntries(zipPath) {
  const buf = fs.readFileSync(zipPath);
  const EOCD_SIG = 0x06054b50;
  const CDH_SIG = 0x02014b50;
  let eocd = -1;
  // EOCD is within the last 64KB + 22 bytes (max comment length).
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 66000); i--) {
    if (buf.readUInt32LE(i) === EOCD_SIG) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("EOCD record not found — not a ZIP file?");
  const count = buf.readUInt16LE(eocd + 10);
  let off = buf.readUInt32LE(eocd + 16);
  const names = [];
  for (let n = 0; n < count; n++) {
    if (buf.readUInt32LE(off) !== CDH_SIG) {
      throw new Error(`bad central-directory signature at entry ${n}`);
    }
    const nameLen = buf.readUInt16LE(off + 28);
    const extraLen = buf.readUInt16LE(off + 30);
    const commentLen = buf.readUInt16LE(off + 32);
    names.push(buf.toString("utf8", off + 46, off + 46 + nameLen));
    off += 46 + nameLen + extraLen + commentLen;
  }
  return names;
}

async function main() {
  const vsix = fs.readdirSync(EXT_ROOT)
    .filter((f) => f.startsWith("awino-loop-owner-") && f.endsWith(".vsix"))
    .sort()
    .pop();
  if (!vsix) {
    console.log("SKIP - no awino-loop-owner-*.vsix built yet (run `npx @vscode/vsce package` first)");
    console.log("\n" + pass + " passed, " + fail + " failed");
    process.exit(0);
  }
  const vsixPath = path.join(EXT_ROOT, vsix);
  const names = listZipEntries(vsixPath);
  console.log(`info - ${vsix}: ${names.length} entries`);
  const cached = names.filter((n) => n.includes(".pbs-cache"));
  ok(cached.length === 0,
    `no .pbs-cache entries in the VSIX${cached.length ? " (found: " + cached.slice(0, 3).join(", ") + ")" : ""}`);
  // Sanity: the reader actually saw the archive (guard against a parser
  // that silently returns nothing).
  ok(names.length > 100, `central directory parsed (${names.length} entries)`);
  // The Windows runtime must ship: extension/vsix root maps python/...
  const winBin = names.find((n) => /(^|\/)python\/win32-x64\/python\.exe$/.test(n));
  ok(!!winBin, "python/win32-x64/python.exe present in the VSIX");
  // darwin-x64 (Intel Mac) is intentionally omitted from the VSIX (see
  // .vscodeignore): with all four runtimes the package exceeds ~170MB;
  // dropping the legacy Intel tree keeps it under the ~120MB guidance.
  // Apple Silicon Macs use darwin-arm64.
  const rids = ["win32-x64", "linux-x64", "darwin-arm64"];
  for (const rid of rids) {
    ok(names.some((n) => n.includes(`python/${rid}/`)), `python/${rid}/ tree present in the VSIX`);
  }
  ok(!names.some((n) => n.includes("python/darwin-x64/")),
    "python/darwin-x64/ tree absent from the VSIX (intentionally omitted)");
  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
