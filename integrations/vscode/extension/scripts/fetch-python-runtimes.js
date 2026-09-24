/**
 * fetch-python-runtimes.js — download, hash-verify, and unpack portable
 * CPython runtimes (astral-sh/python-build-standalone) into ./python/<rid>/
 * so `vsce package` ships a zero-setup interpreter per platform.
 *
 * This is the SINGLE source of truth for runtime versions, URLs, and
 * hashes: both local packaging (`npm run vscode:prepublish`) and the
 * Windows CI workflow call this script. Do not duplicate the URL list
 * anywhere else.
 *
 * Pinned release: 20260901, CPython 3.12.14, `install_only_stripped`
 * archives (stripped debug symbols keep the VSIX under budget).
 *
 * Layout produced:
 *   python/win32-x64/python.exe
 *   python/linux-x64/bin/python3
 *   python/darwin-arm64/bin/python3
 *   python/darwin-x64/bin/python3
 *
 * Cache: $AWINO_PBS_CACHE, else ./.pbs-cache (gitignored). A cached
 * archive is re-verified by SHA-256 before use; a hash mismatch is a
 * HARD failure — we never ship an unverified runtime.
 *
 * Usage: node ./scripts/fetch-python-runtimes.js [--rid=win32-x64]
 */
"use strict";
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const EXT_ROOT = path.resolve(__dirname, "..");
const PYTHON_DIR = path.join(EXT_ROOT, "python");
const CACHE_DIR = process.env.AWINO_PBS_CACHE || path.join(EXT_ROOT, ".pbs-cache");

const PBS_RELEASE = "20260901";
const PYTHON_VERSION = "3.12.14";
const BASE_URL = `https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}`;

// Pinned SHA-256 of each archive (from the release's SHA256SUMS file).
// Mismatch => hard failure, nothing is unpacked.
const TARGETS = [
  {
    triplet: "x86_64-pc-windows-msvc",
    rid: "win32-x64",
    bin: "python.exe",
    sha256: "7c45c9622400d578709a9b2cddbe8124cc21d382409d9f13406d706d28e31b14",
  },
  {
    triplet: "x86_64-unknown-linux-gnu",
    rid: "linux-x64",
    bin: path.join("bin", "python3"),
    sha256: "72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c",
  },
  {
    triplet: "aarch64-apple-darwin",
    rid: "darwin-arm64",
    bin: path.join("bin", "python3"),
    sha256: "81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b",
  },
  {
    triplet: "x86_64-apple-darwin",
    rid: "darwin-x64",
    bin: path.join("bin", "python3"),
    sha256: "65b195c9cedc1fef6767f044f9822069adbd1bd9204d424ece4628776fdc04bb",
  },
];

function archiveName(t) {
  return `cpython-${PYTHON_VERSION}+${PBS_RELEASE}-${t.triplet}-install_only_stripped.tar.gz`;
}

function sha256File(p) {
  const h = crypto.createHash("sha256");
  h.update(fs.readFileSync(p));
  return h.digest("hex");
}

function download(url, dest) {
  console.log(`fetch-python-runtimes: downloading ${path.basename(dest)}`);
  execFileSync("curl", ["-sSL", "--fail", "-o", dest, url], { stdio: "inherit" });
}

function main() {
  const onlyRid = (process.argv.find((a) => a.startsWith("--rid=")) || "").slice(6);
  const targets = onlyRid ? TARGETS.filter((t) => t.rid === onlyRid) : TARGETS;
  if (onlyRid && targets.length === 0) {
    console.error(`fetch-python-runtimes: unknown --rid=${onlyRid}`);
    process.exit(1);
  }
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  fs.mkdirSync(PYTHON_DIR, { recursive: true });

  for (const t of targets) {
    const name = archiveName(t);
    const cached = path.join(CACHE_DIR, name);
    let ok = false;
    if (fs.existsSync(cached)) {
      ok = sha256File(cached) === t.sha256;
      console.log(
        `fetch-python-runtimes: cache ${ok ? "HIT (hash verified)" : "HASH MISMATCH — re-downloading"}: ${name}`
      );
    }
    if (!ok) {
      download(`${BASE_URL}/${name}`, cached);
      const got = sha256File(cached);
      if (got !== t.sha256) {
        fs.rmSync(cached, { force: true });
        console.error(
          `fetch-python-runtimes: SHA-256 MISMATCH for ${name}\n` +
            `  expected ${t.sha256}\n  got      ${got}\n` +
            `  refusing to ship an unverified runtime`
        );
        process.exit(1);
      }
      console.log(`fetch-python-runtimes: hash verified: ${name}`);
    }

    // Unpack straight into the final destination, stripping the archive's
    // top-level `python/` directory. NOTE: do NOT copy the tree with
    // fs.cpSync — Node resolves relative symlink targets to absolute
    // paths (breaking bin/python3 -> python3.12); tar preserves them.
    const dest = path.join(PYTHON_DIR, t.rid);
    fs.rmSync(dest, { recursive: true, force: true });
    fs.mkdirSync(dest, { recursive: true });
    try {
      execFileSync("tar", ["-xzf", cached, "-C", dest, "--strip-components=1"], {
        stdio: "inherit",
      });
      const bin = path.join(dest, t.bin);
      if (!fs.existsSync(bin)) {
        throw new Error(`expected runtime binary missing after unpack: ${path.join(t.rid, t.bin)}`);
      }
      // Prune dead weight: share/ holds man pages and (on linux) the
      // terminfo database. terminfo filenames collide case-insensitively
      // (e.g. 2621A vs 2621a), which the VSIX format rejects outright;
      // the headless sidecar never touches curses/terminfo anyway.
      fs.rmSync(path.join(dest, "share"), { recursive: true, force: true });
      if (t.rid !== "win32-x64") {
        try {
          fs.chmodSync(bin, 0o755);
        } catch {
          /* best effort; prepareBundledRuntime() also chmods at spawn time */
        }
      }
      console.log(`fetch-python-runtimes: staged python/${t.rid}/ (${t.bin} present)`);
    } catch (e) {
      fs.rmSync(dest, { recursive: true, force: true });
      throw e;
    }
  }
  console.log("fetch-python-runtimes: done");
}

main();
