"""Session-level sweeper for stray sidecar test temp dirs.

Sidecar tests create ``awino-sidecar-test-*`` (workspace) and
``awino-home-test-*`` (AWINO_HOME) temp dirs. ``SidecarClient.close()``
removes the ones it owns, but a crashed / Ctrl-C'd / killed run can still
leave strays behind. This module removes them so they can never
accumulate across runs. Called from run_tests.sh before and after the
suite (via an EXIT trap, so the post-run sweep happens even when the
suite fails).

Concurrency safety: the sweeper only removes STALE dirs — a matching dir
whose mtime is newer than STALE_AFTER_SECONDS is left alone, so a second
suite run sharing the same TMPDIR can never have its ACTIVE dirs deleted
from under it. Active sidecar dirs are written to continuously (workspace
files, AWINO_HOME state), so their mtimes stay fresh; a crashed run's
strays sit untouched.

Stdlib only.
"""
import os
import shutil
import tempfile
import time

LEAK_PREFIXES = ("awino-sidecar-test-", "awino-home-test-")

# A matching dir younger than this is treated as possibly-active and is
# never touched. The full suite runs ~6 minutes; 10 minutes of silence
# means the owning run is gone.
STALE_AFTER_SECONDS = 600


def _is_stale(path: str, now: float | None = None) -> bool:
    """True when the dir's mtime is older than the staleness threshold.

    Fail-safe direction: if the mtime cannot be read, the dir is treated
    as NOT stale (never delete what we cannot inspect).
    """
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return False
    return (now if now is not None else time.time()) - mtime > STALE_AFTER_SECONDS


def sweep(prefixes=LEAK_PREFIXES, stale_after: float = STALE_AFTER_SECONDS):
    """Remove STALE stray dirs matching the leak prefixes.

    Returns count removed. Fresh (possibly active) dirs are never
    touched, so concurrent suite runs sharing one TMPDIR are safe.
    """
    tmpdirs = []
    env_tmp = os.environ.get("TMPDIR")
    if env_tmp:
        tmpdirs.append(env_tmp)
    default_tmp = tempfile.gettempdir()
    if default_tmp not in tmpdirs:
        tmpdirs.append(default_tmp)
    removed = 0
    now = time.time()
    for tmpdir in tmpdirs:
        try:
            names = os.listdir(tmpdir)
        except OSError:
            continue
        for name in names:
            if not name.startswith(prefixes):
                continue
            path = os.path.join(tmpdir, name)
            if not (os.path.isdir(path) and not os.path.islink(path)):
                continue
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                continue  # fail-safe: never delete what we cannot inspect
            if now - mtime <= stale_after:
                continue  # fresh: may belong to a live concurrent run
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


if __name__ == "__main__":
    n = sweep()
    print(f"swept {n} stray sidecar test temp dir(s)")
