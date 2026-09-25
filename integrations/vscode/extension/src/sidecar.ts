/**
 * sidecar.ts — vscode-free client for prototype/awino_sidecar.py.
 *
 * Speaks the §4 JSON protocol: commands on stdin (one object per line),
 * events on stdout (one object per line, flushed). All events are
 * re-emitted on this EventEmitter as `event:<name>` plus a generic
 * `event` emission. Keeping this module free of the `vscode` API lets a
 * plain node harness exercise the spawn path end to end.
 */

import { spawn, ChildProcess } from "child_process";
import { EventEmitter } from "events";
import * as fs from "fs";
import * as path from "path";

export interface SidecarEvent {
  event: string;
  [key: string]: unknown;
}

export interface StartOptions {
  /** python interpreter, e.g. "python3" */
  python: string;
  /** absolute path to prototype/awino_sidecar.py */
  sidecarPath: string;
  /** workspace folder the harness is rooted at */
  workspace: string;
  provider?: string;
  model?: string;
  endpoint?: string;
  project?: string;
  timeout?: number;
  /** extra env for the sidecar (API keys live here, never logged) */
  env?: Record<string, string>;
  /** MCP server specs forwarded in hello */
  mcpServers?: unknown[];
  contextWindow?: number;
  /**
   * Scripted turns for the test-only `scripted` provider. Each turn goes
   * through the identical pipeline (no bypass); it only feeds candidate
   * turns instead of calling a model.
   */
  script?: unknown[];
  /** AWINO_HOME override (used by tests to isolate state) */
  home?: string;
  /**
   * Native tool application capability advertisement. The extension
   * passes { delegated_apply: true, terminal_stream: true } so the
   * sidecar delegates file writes (WorkspaceEdit) and shell commands
   * (integrated terminal) to the extension. Omitted by tests that
   * exercise the in-process sandbox path.
   */
  capabilities?: Record<string, unknown>;
  /**
   * AWS profile name for provider "bedrock" (SigV4 mode). Forwarded to the
   * sidecar as `aws_profile` in hello; the Python side resolves it from
   * the user's AWS credential chain (env / ~/.aws / SSO cache).
   */
  awsProfile?: string;
}

const DEFAULT_TIMEOUT_MS = 120_000;

export class SidecarClient extends EventEmitter {
  private proc: ChildProcess | null = null;
  private buf = "";
  private exited = false;
  private closing = false; // set while close() intentionally shuts the proc down
  private readySeen = false; // set once the sidecar emits `ready`
  private stderrTail: string[] = [];

  get running(): boolean {
    return this.proc !== null && !this.exited;
  }

  get stderrPreview(): string {
    return this.stderrTail.slice(-20).join("");
  }

  /** Spawn the sidecar, send hello, resolve with the `ready` event. */
  start(opts: StartOptions, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<SidecarEvent> {
    return new Promise((resolve, reject) => {
      const env: NodeJS.ProcessEnv = { ...process.env };
      if (opts.env) {
        for (const [k, v] of Object.entries(opts.env)) {
          env[k] = v; // keys pass through env only — never in commands/events
        }
      }
      if (opts.home) {
        env["AWINO_HOME"] = opts.home;
      }
      let proc: ChildProcess;
      try {
        proc = spawn(opts.python, [opts.sidecarPath], {
          stdio: ["pipe", "pipe", "pipe"],
          env,
          cwd: opts.workspace,
          // Windows: without this, every sidecar start pops a visible
          // console window out of GUI VS Code. Ignored on non-Windows.
          windowsHide: true,
        });
      } catch (e) {
        reject(e);
        return;
      }
      this.proc = proc;
      this.closing = false; // a fresh process is not an intentional shutdown
      this.readySeen = false;
      this.stderrTail = [];

      proc.stderr?.on("data", (d: Buffer) => {
        const s = d.toString();
        this.stderrTail.push(s);
        if (this.stderrTail.length > 60) {
          this.stderrTail = this.stderrTail.slice(-60);
        }
        this.emit("log", s); // stderr only; never stdout protocol data
      });

      proc.on("error", (e) => {
        this.exited = true;
        // Name the interpreter: on Windows the default `python3` does not
        // exist (stock installs provide `py`/`python`), and a bare ENOENT
        // otherwise reads as a mysterious "not connected".
        const detail = e instanceof Error ? e.message : String(e);
        const msg = `sidecar spawn failed: ${detail} (interpreter "${opts.python}")`;
        // Fatal: the process never started — the UI must drop the session,
        // never sit falsely "connected".
        this.emit("event", { event: "error", fatal: true, message: msg });
        reject(new Error(msg));
      });
      proc.on("exit", (code, signal) => {
        this.exited = true;
        if (this.closing) {
          return; // intentional close (reconnect) — not an error
        }
        const tail = this.stderrTail.slice(-20).join("").trim().slice(0, 500);
        const when = this.readySeen ? "exited" : "exited immediately";
        const ev = {
          event: "error",
          // Fatal: the process died — the UI must drop the session, never
          // sit falsely "connected".
          fatal: true,
          message:
            `sidecar ${when} (code=${code}, signal=${signal}, interpreter "${opts.python}")` +
            (tail ? `: ${tail}` : ""),
        };
        this.emit(`event:${ev.event}`, ev);
        this.emit("event", ev);
      });

      // Line-buffered stdout. (Python flushes every event line.)
      let acc = "";
      proc.stdout?.on("data", (d: Buffer) => {
        acc += d.toString();
        let nl: number;
        while ((nl = acc.indexOf("\n")) >= 0) {
          const line = acc.slice(0, nl);
          acc = acc.slice(nl + 1);
          if (!line.trim()) {
            continue;
          }
          let obj: SidecarEvent;
          try {
            obj = JSON.parse(line) as SidecarEvent;
          } catch {
            this.emit("event", {
              event: "error",
              message: "sidecar emitted non-JSON on stdout",
            });
            continue;
          }
          this.emit(`event:${obj.event}`, obj);
          this.emit("event", obj);
        }
      });

      const timer = setTimeout(() => {
        reject(new Error("timed out waiting for sidecar ready"));
      }, timeoutMs);
      const onReady = (ev: SidecarEvent) => {
        clearTimeout(timer);
        this.readySeen = true;
        resolve(ev);
      };
      this.once("event:ready", onReady);
      this.once("event:error", (ev: SidecarEvent) => {
        clearTimeout(timer);
        reject(new Error(String(ev.message ?? "sidecar error before ready")));
      });

      const hello: Record<string, unknown> = {
        cmd: "hello",
        workspace: opts.workspace,
        provider: opts.provider ?? "echo",
      };
      // Native tool application: the caller advertises delegated apply /
      // terminal capability here; the sidecar attaches its delegation
      // adapter only when it sees delegated_apply. The extension passes
      // { delegated_apply: true, terminal_stream: true }; tests that
      // exercise the in-process sandbox path omit it.
      if (opts.capabilities) {
        hello["capabilities"] = opts.capabilities;
      }
      if (opts.model) {
        hello["model"] = opts.model;
      }
      if (opts.endpoint) {
        hello["endpoint"] = opts.endpoint;
      }
      if (opts.project) {
        hello["project"] = opts.project;
      }
      if (opts.timeout) {
        hello["timeout"] = opts.timeout;
      }
      if (opts.mcpServers && opts.mcpServers.length) {
        hello["mcp_servers"] = opts.mcpServers;
      }
      if (opts.contextWindow) {
        hello["context_window"] = opts.contextWindow;
      }
      if (opts.script) {
        hello["script"] = opts.script;
      }
      if (opts.awsProfile) {
        hello["aws_profile"] = opts.awsProfile;
      }
      this.send(hello);
    });
  }

  /** Write one JSON command line to the sidecar. */
  send(cmd: Record<string, unknown>): void {
    if (!this.proc || !this.proc.stdin || this.exited) {
      throw new Error("sidecar is not running");
    }
    this.proc.stdin.write(JSON.stringify(cmd) + "\n");
  }

  userMessage(text: string): void {
    this.send({ cmd: "user_message", text });
  }

  approve(id: string, decision: "approve" | "deny"): void {
    this.send({ cmd: "approve", id, decision });
  }

  /**
   * Send a `command` verb. `id` is a client-chosen request id the sidecar
   * echoes in its command_result so concurrent same-name commands route to
   * the right waiter; omit it and routing falls back to the command name.
   */
  command(name: string, args: Record<string, unknown> = {}, id?: string): void {
    this.send(id === undefined ? { cmd: "command", name, args } : { cmd: "command", name, args, id });
  }

  cancel(): void {
    this.send({ cmd: "cancel" });
  }

  /** Graceful shutdown (bye), falling back to SIGKILL. Always reaps the child. */
  async close(): Promise<void> {
    const proc = this.proc;
    this.proc = null;
    if (!proc) {
      return;
    }
    this.closing = true;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        try {
          proc.kill("SIGKILL");
        } catch {
          /* already gone */
        }
        resolve();
      }, 3000);
      proc.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      try {
        // write directly: this.proc is already nulled so send() would refuse
        proc.stdin?.write(JSON.stringify({ cmd: "bye" }) + "\n");
      } catch {
        /* fall through to the SIGKILL timer */
      }
    });
    this.exited = true;
  }

  /**
   * Wait for the next event matching `pred`. Resolves with the event.
   * Used by the node harness tests (and could drive UI waits).
   */
  waitFor(
    pred: (ev: SidecarEvent) => boolean,
    timeoutMs = DEFAULT_TIMEOUT_MS
  ): Promise<SidecarEvent> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.removeListener("event", onEv);
        reject(new Error("timed out waiting for sidecar event"));
      }, timeoutMs);
      const onEv = (ev: SidecarEvent) => {
        try {
          if (pred(ev)) {
            clearTimeout(timer);
            this.removeListener("event", onEv);
            resolve(ev);
          }
        } catch {
          /* predicate threw — keep waiting */
        }
      };
      this.on("event", onEv);
    });
  }
}

/**
 * Default sidecar path, in priority order:
 *  1. the copy bundled inside the installed extension
 *     (<ext>/bundled-sidecar/awino_sidecar.py) — the only layout that
 *     exists for a real .vsix install under ~/.vscode/extensions/;
 *  2. the repo checkout layout (<repo>/prototype/awino_sidecar.py) for
 *     development without packaging.
 */
export function defaultSidecarPath(extensionDir: string): string {
  const bundled = path.join(extensionDir, "bundled-sidecar", "awino_sidecar.py");
  if (fs.existsSync(bundled)) {
    return bundled;
  }
  return path.resolve(extensionDir, "..", "..", "prototype", "awino_sidecar.py");
}
