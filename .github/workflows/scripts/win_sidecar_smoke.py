"""Windows CI diagnostic: hello the sidecar directly and show where it stalls.

Sends {"cmd": "hello", ...} to prototype/awino_sidecar.py via stdin, waits
up to 30s for the {"event": "ready"} line, then prints every stdout/stderr
line captured so far. Exits 0 on ready, 1 otherwise — with the evidence
needed to fix the stall instead of guessing.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

# scripts/ -> workflows/ -> .github/ -> repo root
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SIDECAR = os.path.join(REPO, "prototype", "awino_sidecar.py")
WRAPPER = os.path.join(REPO, ".github", "workflows", "scripts",
                       "win_sidecar_wrap.py")

ws = tempfile.mkdtemp(prefix="awino-smoke-ws-")
home = tempfile.mkdtemp(prefix="awino-smoke-home-")
hello = {"cmd": "hello", "workspace": ws, "provider": "echo"}

env = dict(os.environ)
env["AWINO_HOME"] = home
env["PYTHONUNBUFFERED"] = "1"

t0 = time.time()
# Wrap with faulthandler: if the sidecar stalls silently, the wrapper dumps
# every thread's traceback to stderr after 25s (before our 30s deadline).
proc = subprocess.Popen(
    [sys.executable, "-u", WRAPPER, "25", SIDECAR],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    cwd=ws,
    env=env,
)
proc.stdin.write(json.dumps(hello) + "\n")
proc.stdin.flush()

ready = None
out_lines: list[str] = []
deadline = t0 + 30
if sys.platform == "win32":
    # Windows select() does not support pipes — use a drain thread.
    import queue
    import threading

    q: queue.Queue = queue.Queue()

    def _drain():
        try:
            for line in proc.stdout:
                q.put(line.rstrip("\n"))
        finally:
            q.put(None)

    threading.Thread(target=_drain, daemon=True).start()
    while time.time() < deadline and ready is None:
        try:
            item = q.get(timeout=1.0)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if item is None:
            break
        out_lines.append(item)
        try:
            obj = json.loads(item)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "ready":
            ready = obj
            break
else:
    import select

    while time.time() < deadline and ready is None:
        r, _, _ = select.select([proc.stdout], [], [], 1.0)
        if r:
            line = proc.stdout.readline()
            if not line:
                break
            out_lines.append(line.rstrip("\n"))
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "ready":
                ready = obj
                break
        if proc.poll() is not None:
            rest = proc.stdout.read() or ""
            out_lines.extend(rest.splitlines())
            break

elapsed = time.time() - t0
print(f"--- sidecar stdout ({len(out_lines)} lines, {elapsed:.1f}s) ---")
for ln in out_lines[-40:]:
    print(ln[:300])
try:
    _, err = proc.communicate(timeout=3)
except subprocess.TimeoutExpired:
    proc.kill()
    _, err = proc.communicate()
print("--- sidecar stderr (tail) ---")
print((err or "")[-2000:])
print(f"--- exit code: {proc.returncode} ---")
if ready:
    print(f"SMOKE OK: ready in {elapsed:.1f}s (protocol {ready.get('protocol')})")
    sys.exit(0)
print("SMOKE FAILED: no ready event")
sys.exit(1)
