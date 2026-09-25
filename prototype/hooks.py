"""User-definable hooks on loop events (v0.6 back half).

Lifecycle: register -> enable/disable -> fire(event, payload) -> unregister.

A hook is a named callable ``fn(event, payload)`` attached to one loop
event. The loop fires hooks at fixed points (turn start/end, tool result,
approval requested/decided, mission set/complete, context compacted).

Error isolation is the whole point: a failing hook must never break the
loop. ``fire()`` catches every exception, records it on the registry, and
returns normally. Hook code runs in-process, synchronously, in
registration order.
"""

from __future__ import annotations

import time
from typing import Callable

# Every event the Loop fires. Registering for anything else is a typo
# guard failure (ValueError), not a silent no-op.
HOOK_EVENTS = (
    "turn_start",
    "turn_end",
    "tool_result",
    "approval_requested",
    "approval_decided",
    "mission_set",
    "mission_complete",
    "context_compacted",
)

HookFn = Callable[[str, dict], None]


class HookRegistry:
    """Named, enable/disable-able hooks with error isolation."""

    def __init__(self) -> None:
        # name -> {"event", "fn", "enabled"}
        self._hooks: dict[str, dict] = {}
        # [{name, event, error, ts}] — a failing hook is recorded here,
        # never raised into the loop.
        self._errors: list[dict] = []

    # ------------------------------------------------------------ lifecycle
    def register(self, name: str, event: str, fn: HookFn,
                 enabled: bool = True) -> dict:
        """Attach ``fn`` to ``event`` under ``name``.

        Raises ValueError on an unknown event or a duplicate name —
        explicit beats silent replace.
        """
        if event not in HOOK_EVENTS:
            raise ValueError(
                f"unknown hook event {event!r}; known: {', '.join(HOOK_EVENTS)}")
        if not name or not isinstance(name, str):
            raise ValueError("hook name must be a non-empty string")
        if name in self._hooks:
            raise ValueError(
                f"hook {name!r} is already registered; unregister it first")
        if not callable(fn):
            raise ValueError("hook fn must be callable")
        entry = {"name": name, "event": event, "fn": fn,
                 "enabled": bool(enabled)}
        self._hooks[name] = entry
        return {"name": name, "event": event, "enabled": entry["enabled"]}

    def unregister(self, name: str) -> bool:
        """Remove a hook. Returns True when one was removed."""
        return self._hooks.pop(name, None) is not None

    def enable(self, name: str) -> bool:
        """Enable a hook. Returns False for an unknown name."""
        entry = self._hooks.get(name)
        if entry is None:
            return False
        entry["enabled"] = True
        return True

    def disable(self, name: str) -> bool:
        """Disable a hook (it stays registered, it just never fires)."""
        entry = self._hooks.get(name)
        if entry is None:
            return False
        entry["enabled"] = False
        return True

    def is_enabled(self, name: str) -> bool:
        entry = self._hooks.get(name)
        return entry is not None and entry["enabled"]

    def listeners(self, event: str | None = None) -> list[dict]:
        """Registered hooks (name/event/enabled), optionally filtered."""
        return [{"name": e["name"], "event": e["event"],
                 "enabled": e["enabled"]}
                for e in self._hooks.values()
                if event is None or e["event"] == event]

    # ----------------------------------------------------------------- fire
    def fire(self, event: str, payload: dict | None = None) -> list[dict]:
        """Fire every enabled hook for ``event``.

        Never raises: a hook that throws is recorded in ``errors`` and the
        loop continues. Returns one result dict per fired hook:
        {"name", "ok": True} or {"name", "ok": False, "error": ...}.
        """
        payload = dict(payload or {})
        results: list[dict] = []
        for entry in list(self._hooks.values()):
            if entry["event"] != event or not entry["enabled"]:
                continue
            try:
                entry["fn"](event, payload)
            except Exception as ex:  # noqa: BLE001 - error isolation
                err = f"{type(ex).__name__}: {ex}"
                self._errors.append({"name": entry["name"], "event": event,
                                     "error": err, "ts": time.time()})
                results.append({"name": entry["name"], "ok": False,
                                "error": err})
            else:
                results.append({"name": entry["name"], "ok": True})
        return results

    @property
    def errors(self) -> list[dict]:
        """Hook failures, oldest first. Read-only view."""
        return list(self._errors)

    def clear_errors(self) -> int:
        """Drop recorded hook errors. Returns how many were cleared."""
        n = len(self._errors)
        del self._errors[:]
        return n
