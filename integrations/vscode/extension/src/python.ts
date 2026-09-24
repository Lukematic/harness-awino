/**
 * python.ts — Python interpreter resolution for the sidecar spawn.
 *
 * Stock Windows Python installs provide `py` / `python`, not `python3`,
 * so spawning the sidecar with a hard-coded `python3` fails there with a
 * bare ENOENT ("not connected"). On win32 we probe candidates in order
 * and use the first interpreter that runs `--version` successfully.
 *
 * Kept free of the `vscode` API so plain node tests can exercise the
 * resolution order with a mocked probe.
 */

import { execFile } from "child_process";

export type ProbeFn = (cmd: string) => Promise<boolean>;

/**
 * True when `--version` output identifies a Python 3 interpreter.
 * Both streams are checked: `python --version` prints to stdout on 3.4+
 * but to stderr on 2.x, and either stream alone can mislead.
 */
export function isPython3VersionOutput(stdout: string, stderr: string): boolean {
  return /^Python 3\.\d+/m.test(`${stdout}\n${stderr}`);
}

/**
 * Real probe: `cmd --version` must exit 0 within 5s AND identify as
 * Python 3. Never throws. The version check rejects Windows Store stubs
 * (which can pop the Store GUI with unreliable exit codes) and Python 2
 * interpreters — exit code alone would accept both.
 */
export function defaultProbe(cmd: string): Promise<boolean> {
  return new Promise((resolve) => {
    execFile(cmd, ["--version"], { timeout: 5_000 }, (err, stdout, stderr) => {
      if (err) {
        resolve(false);
        return;
      }
      resolve(isPython3VersionOutput(String(stdout), String(stderr)));
    });
  });
}

export interface ResolveOptions {
  /** Explicitly configured interpreter (awino.pythonPath, only when the user set it). */
  configured?: string;
  /**
   * Bundled VSIX interpreter (0.5.0+). Pass the absolute path returned by
   * bundledRuntimePath(), or omit when no bundled runtime ships for this
   * platform. A deliberate user configuration still wins over it.
   */
  bundledPath?: string;
  /** Override for tests; defaults to process.platform. */
  platform?: NodeJS.Platform;
  /** Candidate interpreters tried on win32, in order. */
  candidates?: string[];
  /** Override for tests; defaults to defaultProbe. */
  probe?: ProbeFn;
}

export type InterpreterSource = "configured" | "bundled" | "auto-detected" | "default";

export interface ResolvedInterpreter {
  python: string;
  source: InterpreterSource;
  /** Candidates probed during auto-detection (win32 only), in order. */
  tried: string[];
}

/**
 * Resolve the interpreter to spawn:
 * 1. explicit user configuration wins (a deliberate choice beats everything);
 * 2. the interpreter bundled inside the VSIX (0.5.0+), when present;
 * 3. on win32, probe `py` -> `python` -> `python3`, first `--version` win;
 * 4. otherwise the built-in `python3` default (spawn errors then surface
 *    through the normal failure path with the interpreter named).
 */
export async function resolvePythonInterpreter(opts: ResolveOptions = {}): Promise<ResolvedInterpreter> {
  const configured = (opts.configured ?? "").trim();
  if (configured) {
    return { python: configured, source: "configured", tried: [] };
  }
  const bundled = (opts.bundledPath ?? "").trim();
  if (bundled) {
    return { python: bundled, source: "bundled", tried: [] };
  }
  const tried: string[] = [];
  if ((opts.platform ?? process.platform) === "win32") {
    const candidates = opts.candidates ?? ["py", "python", "python3"];
    const probe = opts.probe ?? defaultProbe;
    for (const c of candidates) {
      tried.push(c);
      let ok = false;
      try {
        ok = await probe(c);
      } catch {
        ok = false; // a throwing probe counts as "not usable", try the next
      }
      if (ok) {
        return { python: c, source: "auto-detected", tried };
      }
    }
  }
  return { python: "python3", source: "default", tried };
}

/** One-line fix hint for a sidecar start failure. */
export function interpreterFixHint(resolved: ResolvedInterpreter): string {
  if (resolved.source === "configured") {
    return 'Fix: check the "awino.pythonPath" setting — it must be a Python 3 interpreter on PATH or an absolute path.';
  }
  if (resolved.source === "bundled") {
    // Zero-setup promise: the user never installed Python, so "install
    // Python 3" is the wrong advice — the bundled runtime itself failed.
    return 'Fix: the Python bundled with Awino failed to start — please report this; as a workaround, set "awino.pythonPath" to a system Python 3.';
  }
  return 'Fix: install Python 3 and make sure it is on PATH, or set "awino.pythonPath" to your interpreter.';
}

/**
 * Compose the user-facing failure detail: the actual OS error, which
 * interpreter(s) were tried, and the one-line fix hint.
 */
export function describeSpawnFailure(resolved: ResolvedInterpreter, osError: string): string {
  const tried = resolved.tried.length > 0 ? resolved.tried.join(", ") : resolved.python;
  return `${osError} — tried interpreter(s): ${tried}. ${interpreterFixHint(resolved)}`;
}

/**
 * True when a spawn failure means "no such interpreter" — the case the
 * Locate-Python recovery flow handles. Matches the OS ENOENT text that
 * child_process surfaces (e.g. "spawn python3 ENOENT").
 */
export function isInterpreterNotFound(osError: string): boolean {
  return /\bENOENT\b/i.test(osError);
}

/**
 * Common install locations per platform, shown in the recovery message so
 * the user knows where to look before picking "Locate Python...".
 */
export function commonPythonLocations(platform?: NodeJS.Platform): string[] {
  switch (platform ?? process.platform) {
    case "win32":
      return [
        "the `py` launcher (run `py --version` in a terminal)",
        "%LOCALAPPDATA%\\Programs\\Python",
        "Microsoft Store Python",
      ];
    case "darwin":
      return [
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3 (Homebrew)",
        "Xcode Command Line Tools",
      ];
    default:
      return ["/usr/bin/python3"];
  }
}
