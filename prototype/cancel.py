"""Cooperative cancellation for the v0.6 agent loop.

One CancelToken is created per user turn (by the sidecar) and threaded
sidecar -> Loop.run_user_turn -> _run_turn -> _run_agent_loop ->
_execute_calls -> Sandbox.run_command / backend.generate.

Every layer checks the token at cheap, explicit checkpoints; nothing is
force-killed except a runaway subprocess (terminate, 2s grace, kill).
An in-flight stdlib HTTP call cannot be mid-flight aborted — backends
check the token before the call and between stream chunks, and raise
Cancelled so the loop halts honestly at the next checkpoint instead of
pretending the call never happened.
"""

import threading
import time


class Cancelled(Exception):
    """Raised when a cooperative checkpoint observes a set CancelToken.

    Caught only by the round driver (Loop._run_agent_loop), which turns it
    into _halt_turn("cancelled"). Never leaks to the operator as a crash.
    """


class CancelToken:
    """A per-turn cancellation flag with a reason and a set timestamp.

    Thread-safe: the sidecar's stdin pump thread sets it while the worker
    thread's loop polls it. `set_ts` lets the loop order "cancel arrived
    after consequential effects executed" (too-late-with-effects) honestly.
    """

    def __init__(self):
        self._ev = threading.Event()
        self.reason: str | None = None
        self.set_ts: float | None = None

    def set(self, reason: str = "operator stop") -> None:
        if not self._ev.is_set():
            self.reason = reason
            self.set_ts = time.time()
        self._ev.set()

    def is_set(self) -> bool:
        return self._ev.is_set()

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.is_set()
