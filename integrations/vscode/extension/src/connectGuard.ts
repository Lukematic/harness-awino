/**
 * connectGuard.ts — serializes sidecar connect() runs.
 *
 * connect() is reachable from several paths (auto-connect on startup,
 * the Reconnect command, the Locate-Python recovery retry). Without a
 * guard, two overlapping runs each spawn a sidecar: the losing process
 * leaks, and when the first run's start() finally rejects its catch sets
 * `session = null`, destroying the newer live session.
 *
 * The guard gives every run a generation number. Concurrent callers join
 * the in-flight run instead of spawning a second sidecar, and a run that
 * is no longer current must not mutate shared session state.
 *
 * Kept free of the `vscode` API so plain node tests can exercise the
 * concurrency semantics with fake timers/deps.
 */
export class ConnectGuard {
  private inflight: Promise<void> | null = null;
  private generation = 0;

  /** Generation of the latest run started (0 when none has run yet). */
  get currentGeneration(): number {
    return this.generation;
  }

  /**
   * Run `fn` exclusively. A call made while another run is in flight joins
   * that run and receives its promise — `fn` executes exactly once.
   *
   * `fn` receives its generation number and an `isCurrent()` predicate. A
   * run whose generation has been superseded by a newer run must not touch
   * shared session state (notably: it must not set `session = null`).
   */
  run(
    fn: (generation: number, isCurrent: () => boolean) => Promise<void>
  ): Promise<void> {
    if (this.inflight) {
      return this.inflight;
    }
    const generation = ++this.generation;
    const isCurrent = () => generation === this.generation;
    // Defer fn to a microtask so the guard slot is claimed before anything
    // inside fn's synchronous prefix could observe it empty.
    const mine: Promise<void> = Promise.resolve().then(() =>
      fn(generation, isCurrent)
    );
    this.inflight = mine;
    // Settlement cleanup, identity-checked: release() (below) lets a newer
    // run claim the slot while this run is parked — the older run's
    // cleanup must never clear the newer run's slot.
    const clear = () => {
      if (this.inflight === mine) {
        this.inflight = null;
      }
    };
    mine.then(clear, clear);
    return mine;
  }

  /**
   * Release the guard early, from inside the in-flight run. connect()'s
   * catch calls this before showing the Locate-Python dialog: the dialog's
   * retry must start a genuinely new run — re-awaiting the in-flight
   * promise from inside itself would deadlock.
   */
  release(): void {
    this.inflight = null;
  }
}
