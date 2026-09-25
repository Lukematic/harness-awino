"""Session-level sweeper for stray sidecar test temp dirs.

Sidecar tests create ``awino-sidecar-test-*`` (workspace) and
``awino-home-test-*`` (AWINO_HOME) temp dirs. ``SidecarClient.close()``
removes the ones it owns, but a crashed / Ctrl-C'd / killed run can still
leave strays behind. This module removes them so they can never
accumulate across runs. Called from run_tests.sh before and after the
suite (via an EXIT trap, so the post-run sweep happens even when the
suite fails).

Stdlib only.
"""
import os
import shutil
import tempfile

LEAK_PREFIXES = ("awino-sidecar-test-", "awino-home-test-")


def sweep(prefixes=LEAK_PREFIXES):
    """Remove stray dirs matching the leak prefixes. Returns count removed."""
    tmpdirs = []
    env_tmp = os.environ.get("TMPDIR")
    if env_tmp:
        tmpdirs.append(env_tmp)
    default_tmp = tempfile.gettempdir()
    if default_tmp not in tmpdirs:
        tmpdirs.append(default_tmp)
    removed = 0
    for tmpdir in tmpdirs:
        try:
            names = os.listdir(tmpdir)
        except OSError:
            continue
        for name in names:
            if not name.startswith(prefixes):
                continue
            path = os.path.join(tmpdir, name)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    return removed


if __name__ == "__main__":
    n = sweep()
    print(f"swept {n} stray sidecar test temp dir(s)")
