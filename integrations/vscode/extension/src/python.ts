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

/** Real probe: does `cmd --version` exit 0 within 10s? Never throws. */
export function defaultProbe(cmd: string): Promise<boolean> {
  return new Promise((resolve) => {
    execFile(cmd, ["--version"], { timeout: 10_000 }, (err) => resolve(!err));
  });
}

export interface ResolveOptions {
  /** Explicitly configured interpreter (awino.pythonPath, only when the user set it). */
  configured?: string;
  /** Override for tests; defaults to process.platform. */
  platform?: NodeJS.Platform;
  /** Candidate interpreters tried on win32, in order. */
  candidates?: string[];
  /** Override for tests; defaults to defaultProbe. */
  probe?: ProbeFn;
}

export type InterpreterSource = "configured" | "auto-detected" | "default";

export interface ResolvedInterpreter {
  python: string;
  source: InterpreterSource;
  /** Candidates probed during auto-detection (win32 only), in order. */
  tried: string[];
}

/**
 * Resolve the interpreter to spawn:
 * 1. explicit user configuration wins;
 * 2. on win32, probe `py` -> `python` -> `python3`, first `--version` win;
 * 3. otherwise the built-in `python3` default (spawn errors then surface
 *    through the normal failure path with the interpreter named).
 */
export async function resolvePythonInterpreter(opts: ResolveOptions = {}): Promise<ResolvedInterpreter> {
  const configured = (opts.configured ?? "").trim();
  if (configured) {
    return { python: configured, source: "configured", tried: [] };
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
