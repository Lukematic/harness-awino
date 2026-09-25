/**
 * Integrated terminal execution for delegated run_command.
 *
 * Replaces the sidecar's blocking 30s subprocess.run with VS Code's
 * terminal shell integration: the command runs in a real terminal, the
 * extension streams output chunks live to the sidecar, and long-running
 * processes are supported (no arbitrary kill at 30s).
 *
 * Design notes (studied from Cline's VscodeTerminalProcess):
 * - terminal.shellIntegration.executeCommand(cmd) returns an execution
 *   whose read() is an AsyncIterable<string> of output chunks.
 * - onDidEndTerminalShellExecution gives the exit code; onDidCloseTerminal
 *   guards against a dead terminal hanging the wait.
 * - This implementation is intentionally smaller than Cline's: one
 *   command at a time, no command queue, no xterm parser — the sidecar
 *   serializes tool calls.
 *
 * Needs the vscode API: compiled-not-run in Node tests, proven live by
 * the Windows GUI workflow (NATIVE_APPLY_GUI_ASSERTIONS.md).
 */
import * as vscode from "vscode";
import { stripAnsi } from "./delegatedPure";

export interface TerminalRunResult {
  exitCode: number | undefined;
  timedOut: boolean;
  killed: boolean;
  /** Machine-readable completion reason: "completed" | "timed_out" |
   *  "killed" | "terminal_closed" | "shell_integration_unavailable" |
   *  "execute_failed". */
  reason: string;
}

const SHELL_INTEGRATION_WAIT_MS = 10_000;

/** Wait for the terminal's shellIntegration to become available. */
async function waitForShellIntegration(
  term: vscode.Terminal
): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < SHELL_INTEGRATION_WAIT_MS) {
    if (term.shellIntegration) {
      return true;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  return !!term.shellIntegration;
}

export class AwinoTerminalRunner {
  private term: vscode.Terminal | null = null;
  private termCwd: string | null = null;
  private killed = false;

  /** Kill the running command's terminal (user cancel). */
  kill(): void {
    this.killed = true;
    const t = this.term;
    this.term = null;
    this.termCwd = null;
    try {
      t?.dispose();
    } catch {
      // already gone
    }
  }

  /**
   * Run cmd in the integrated terminal, streaming cleaned output chunks
   * to onData as they arrive. Resolves with the exit code when the shell
   * reports completion, or timedOut when timeoutMs elapses (the terminal
   * is then disposed).
   */
  async run(
    cmd: string,
    cwd: string,
    timeoutMs: number,
    onData: (chunk: string) => void
  ): Promise<TerminalRunResult> {
    this.killed = false;
    // Reuse one terminal per runner so consecutive commands share shell
    // history; recreate if the user closed it or the cwd changed.
    if (!this.term || this.termCwd !== (cwd || null)) {
      this.kill();
      this.killed = false;
      this.term = vscode.window.createTerminal({
        name: "Awino",
        cwd: cwd || undefined,
      });
      this.termCwd = cwd || null;
    }
    const term = this.term;
    term.show(true);

    if (!(await waitForShellIntegration(term))) {
      return {
        exitCode: undefined,
        timedOut: false,
        killed: this.killed,
        reason: "shell_integration_unavailable",
      };
    }
    const shell = term.shellIntegration;
    if (!shell) {
      return {
        exitCode: undefined,
        timedOut: false,
        killed: this.killed,
        reason: "shell_integration_unavailable",
      };
    }

    let execution: vscode.TerminalShellExecution;
    try {
      execution = shell.executeCommand(cmd);
    } catch (e) {
      return {
        exitCode: undefined,
        timedOut: false,
        killed: this.killed,
        reason: "execute_failed",
      };
    }

    // Stream output chunks live.
    const streaming = (async () => {
      try {
        for await (const chunk of execution.read()) {
          const clean = stripAnsi(chunk);
          if (clean) {
            onData(clean);
          }
          if (this.killed) {
            break;
          }
        }
      } catch {
        // The stream can end abruptly when the terminal is disposed.
      }
    })();

    // Exit code via the shell-integration end event; guard against the
    // terminal being closed without the event firing, and against a
    // command that never returns (timeout). The reason distinguishes
    // completed / timed_out / killed / terminal_closed.
    let timedOutFlag = false;
    let closedFlag = false;
    const { exitCode, reason } = await new Promise<{
      exitCode: number | undefined;
      reason: string;
    }>((resolve) => {
      let done = false;
      const finish = (code: number | undefined, why: string) => {
        if (!done) {
          done = true;
          dispose();
          resolve({ exitCode: code, reason: why });
        }
      };
      const d1 = vscode.window.onDidEndTerminalShellExecution((e) => {
        if (e.execution === execution) {
          finish(
            e.exitCode,
            this.killed ? "killed" : "completed"
          );
        }
      });
      const d2 = vscode.window.onDidCloseTerminal((t) => {
        if (t === term) {
          closedFlag = true;
          finish(undefined, this.killed ? "killed" : "terminal_closed");
        }
      });
      const timer = setTimeout(() => {
        timedOutFlag = true;
        finish(undefined, "timed_out");
      }, timeoutMs);
      const dispose = () => {
        clearTimeout(timer);
        d1.dispose();
        d2.dispose();
      };
    });

    // The stream should end once the execution does; bound the wait so a
    // wedged stream can never hang the runner.
    await Promise.race([
      streaming,
      new Promise((r) => setTimeout(r, 2000)),
    ]);
    const timedOut = timedOutFlag;
    if (timedOut) {
      // The shell never reported back: dispose so a hung process can't
      // hold the runner hostage.
      this.kill();
    }
    // If killed after completion was already recorded, report killed.
    const finalReason = this.killed && reason === "completed" ? "killed" : reason;
    return { exitCode, timedOut, killed: this.killed, reason: finalReason };
  }
}
