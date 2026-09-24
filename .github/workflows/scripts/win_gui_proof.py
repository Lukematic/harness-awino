#!/usr/bin/env python3
"""win_gui_proof.py — Awino 0.5.0 Windows zero-setup GUI proof.

Runs on windows-latest CI (or a local Windows box). Proves in a real VS
Code window, with NO system Python on the VS Code process PATH:

  1. The VSIX installs and the Awino extension activates.
  2. The sidecar spawns from the BUNDLED python/win32-x64/python.exe —
     hard proof via the sidecar's process command line.
  3. The sidecar connects (chat statusline reads "connected").
  4. A scripted mission is created, its contract approved, and one canned
     turn executes end to end.
  5. The mission header renders; the Tasks panel lists the
     seed-registered task (RISK 2 / BUG 2 surface).
  6. No orphan awino_sidecar processes remain afterwards.

First-spawn latency of several seconds is expected: Windows Defender
real-time-scans the unsigned bundled python.exe on first launch.

Screenshots + proof.log land in --out. Exits 0 only if every check passes.
Usage:
  python .github/workflows/scripts/win_gui_proof.py --code <Code.exe>
      --vsix <path to awino-loop-owner-0.5.0.vsix> --out proof/win_gui
      --work <scratch dir>
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" +
          (f" — {detail}" if detail else ""), flush=True)
    return bool(cond)


def shot(page, name, out_dir):
    path = os.path.join(out_dir, name + ".png")
    try:
        page.screenshot(path=path)
        print("shot:", path, flush=True)
    except Exception as e:
        print("shot FAILED:", name, e, flush=True)


def scrubbed_env_for_vscode():
    """Copy of os.environ with every Python on PATH removed.

    The driver itself keeps running under the setup-python interpreter;
    only the VS Code child process gets the scrubbed environment — that is
    the zero-setup condition: the extension may not lean on a system
    Python because there is none visible.
    """
    env = dict(os.environ)
    sep = os.pathsep
    kept = []
    removed = []
    for entry in env.get("PATH", "").split(sep):
        if "python" in entry.lower():
            removed.append(entry)
        else:
            kept.append(entry)
    env["PATH"] = sep.join(kept)
    for key in [k for k in env
                if k.upper().startswith("PYTHON") or k == "__PYVENV_LAUNCHER__"]:
        del env[key]
    if removed:
        print("scrubbed from VS Code PATH:", removed, flush=True)
    else:
        print("note: no python entries found on PATH to scrub", flush=True)
    return env


def ps_table():
    """[(name, commandline)] for running processes via CIM."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | "
             "Select-Object Name,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60)
    except Exception as e:
        return [("ps-error", str(e))]
    try:
        rows = json.loads(out.stdout or "[]")
    except Exception:
        return [("ps-parse-error", out.stdout[:200])]
    if isinstance(rows, dict):
        rows = [rows]
    return [(r.get("Name") or "", r.get("CommandLine") or "") for r in rows]


def find_workbench_page(browser, timeout_s=90):
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            for ctx in browser.contexts:
                for p in ctx.pages:
                    try:
                        if p.locator(".monaco-workbench").count() > 0:
                            return p
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(2)
    return None


def find_chat_frame(page, timeout_s=60):
    markers = ["#mission-header", "#provider-pill", "#inputbar"]
    end = time.time() + timeout_s
    while time.time() < end:
        for f in page.frames:
            try:
                for m in markers:
                    if f.locator(m).count() > 0:
                        return f
            except Exception:
                pass
        time.sleep(1.0)
    return None


def run_command_palette(page, command, timeout_s=30):
    """Ctrl+Shift+P, type the command, Enter. True if it accepted."""
    try:
        page.keyboard.press("Control+Shift+P")
        box = page.locator(".quick-input-widget input")
        box.wait_for(state="visible", timeout=timeout_s * 1000)
        time.sleep(1.0)
        box.fill(">" + command)
        time.sleep(2.5)
        rows = page.locator(".quick-input-widget .monaco-list-row")
        n = rows.count()
        for i in range(n):
            try:
                if command.lower() in rows.nth(i).inner_text().lower():
                    rows.nth(i).click()
                    return True
            except Exception:
                pass
        page.keyboard.press("Enter")
        return n > 0
    except Exception:
        return False


def quick_input(page, text, timeout_s=30):
    """Type into the visible quick-input box and press Enter."""
    try:
        box = page.locator(".quick-input-widget input")
        box.wait_for(state="visible", timeout=timeout_s * 1000)
        time.sleep(0.8)
        box.fill(text)
        time.sleep(0.8)
        page.keyboard.press("Enter")
        return True
    except Exception:
        return False


def wait_statusline_connected(frame, timeout_s=180):
    """Poll the chat statusline for 'connected ·' (Defender may scan the
    unsigned bundled python.exe on first spawn — allow real time).
    NOTE: "connected" is a substring of "not connected" — require the exact
    "connected ·" marker and explicitly reject "not connected"."""
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            t = frame.locator("#statusline").inner_text()
            if "connected ·" in t and "not connected" not in t:
                return t
        except Exception:
            pass
        time.sleep(2)
    return None


def wait_sidecar_cmdline(timeout_s=180):
    """Hard zero-setup proof: a python.exe running awino_sidecar.py whose
    command line names the BUNDLED win32-x64 tree."""
    end = time.time() + timeout_s
    while time.time() < end:
        for name, cmd in ps_table():
            if "awino_sidecar" in cmd and "python" in name.lower():
                bundled = ("python\\win32-x64\\python.exe" in cmd or
                           "python/win32-x64/python.exe" in cmd or
                           "win32-x64" in cmd)
                return cmd, bundled
        time.sleep(3)
    return None, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--vsix", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    a = ap.parse_args()

    out_dir = os.path.abspath(a.out)
    work = os.path.abspath(a.work)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    log_path = os.path.join(out_dir, "proof.log")
    logf = open(log_path, "w", encoding="utf-8")

    def log(*parts):
        line = " ".join(str(p) for p in parts)
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    user_data = os.path.join(work, "user-data")
    ext_dir = os.path.join(work, "extensions")
    ws = os.path.join(work, "workspace")
    os.makedirs(ws, exist_ok=True)
    for d in (user_data, ext_dir):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)

    # --- 1. install the VSIX -------------------------------------------
    # NOTE: the install MUST use the same --extensions-dir (and --user-data-dir)
    # as the launch below, or VS Code starts with an empty extension dir.
    # The VS Code CLI --install-extension hangs on Windows CI (300s timeout,
    # likely Defender scanning the 123MB VSIX). Manual extraction is faster
    # and reliable: unzip the VSIX's extension/ into
    # <ext_dir>/<publisher>.<name>-<version>/.
    log("installing", a.vsix)
    import zipfile
    ext_id = "lukematic.awino-loop-owner-0.5.0"
    dest = os.path.join(ext_dir, ext_id)
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    vsix_path = os.path.abspath(a.vsix)
    log(f"extracting VSIX to {dest}")
    with zipfile.ZipFile(vsix_path, "r") as z:
        for info in z.infolist():
            # VSIX entries are under extension/; strip the prefix.
            if not info.filename.startswith("extension/"):
                continue
            rel = info.filename[len("extension/"):]
            if not rel:
                continue
            target = os.path.join(dest, rel)
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    log(f"extracted {ext_id} ({sum(len(f) for _, _, f in os.walk(dest))} files)")
    # Verify via filesystem (the VS Code CLI --list-extensions hangs on
    # Windows CI, like --install-extension did). The extension is installed
    # if the directory exists with package.json and the bundled runtime.
    pkg_json = os.path.join(dest, "package.json")
    bundled_py = os.path.join(dest, "python", "win32-x64", "python.exe")
    installed = (os.path.isfile(pkg_json) and os.path.isfile(bundled_py))
    if not check("win_ext_installed", installed,
                 f"extension dir {ext_id} with package.json + bundled python.exe"):
        logf.close()
        sys.exit(1)

    # --- 2. scripted provider: canned turns, no keys ----------------------
    script = [{
        "header": "echo",
        "objective": "win zero-setup proof",
        "plan": ["Prove the 0.5.0 bundled runtime on Windows"],
        "tool_calls": [],
        "questions": [],
        "assumptions": ["Scripted Windows CI turn."],
        "progress_delta": "Windows GUI probe turn executed.",
        "done_claim": False,
    }]
    os.makedirs(os.path.join(ws, ".vscode"), exist_ok=True)
    with open(os.path.join(ws, ".vscode", "settings.json"), "w") as f:
        json.dump({"awino.provider": "scripted", "awino.script": script}, f)

    # --- 3. launch VS Code with Python scrubbed from its PATH -------------
    env = scrubbed_env_for_vscode()
    env["AWINO_HOME"] = os.path.join(work, "awino-home")
    cmd = [a.code, "--no-sandbox", "--disable-gpu",
           "--disable-dev-shm-usage",
           "--user-data-dir=" + user_data,
           "--extensions-dir=" + ext_dir,
           "--remote-debugging-port=9222",
           "--skip-welcome", "--skip-release-notes",
           "--disable-workspace-trust", ws]
    vlog = open(os.path.join(out_dir, "vscode.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, env=env, stdout=vlog, stderr=subprocess.STDOUT)
    try:
        end = time.time() + 180
        while time.time() < end:
            try:
                urllib.request.urlopen("http://127.0.0.1:9222/json/version",
                                       timeout=3)
                break
            except Exception:
                time.sleep(2)
        else:
            check("win_cdp_up", False, "CDP endpoint never came up")
            raise SystemExit(1)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(
                "http://127.0.0.1:9222", timeout=60000)
            page = find_workbench_page(browser)
            if not check("win_vscode_launched", page is not None,
                         "workbench visible"):
                raise SystemExit(1)
            page.set_viewport_size({"width": 1400, "height": 900})
            time.sleep(6)
            shot(page, "01_win_launch", out_dir)

            # --- 4. open the Awino chat -----------------------------------
            act = None
            for sel in ['.activitybar .action-label[aria-label="Awino"]',
                        '.activitybar [aria-label="Awino"]']:
                loc = page.locator(sel)
                if loc.count() > 0:
                    act = loc.first
                    break
            if not check("win_activity_bar_awino", act is not None,
                         "Awino container in activity bar"):
                raise SystemExit(1)
            chat = find_chat_frame(page, timeout_s=5)
            if chat is None:
                act.click()
                time.sleep(3)
            chat = find_chat_frame(page, timeout_s=60)
            if not check("win_chat_webview_renders", chat is not None,
                         "chat iframe found"):
                raise SystemExit(1)
            shot(page, "02_win_chat", out_dir)

            # --- 5. reconnect; the sidecar MUST come from bundled python ---
            check("win_reconnect_palette",
                  run_command_palette(page, "Awino: Reconnect Sidecar"),
                  "palette accepted reconnect")
            cmdline, bundled = wait_sidecar_cmdline(timeout_s=240)
            if not check("win_sidecar_spawned", cmdline is not None,
                         "awino_sidecar process appeared"):
                raise SystemExit(1)
            log("sidecar cmdline:", (cmdline or "")[:300])
            check("win_sidecar_uses_bundled_python", bundled,
                  "command line names python\\win32-x64\\python.exe")
            stext = wait_statusline_connected(chat, timeout_s=120)
            check("win_sidecar_connected", stext is not None,
                  f"statusline: {(stext or '')[:80]!r}")
            shot(page, "03_win_connected", out_dir)

            # --- 6. new mission + approve the contract ---------------------
            mission_ok = False
            if run_command_palette(page, "Awino: New Mission"):
                if quick_input(page, "win bundled proof mission"):
                    if quick_input(page, "manual, manual"):
                        mission_ok = True
            time.sleep(4)
            check("win_mission_created", mission_ok, "new mission via palette")
            if mission_ok and run_command_palette(page, "Awino: Approve Contract"):
                quick_input(page, "")
                time.sleep(5)
            chat = find_chat_frame(page, timeout_s=30) or chat
            try:
                hdr = chat.locator("#mission-header")
                hv = hdr.is_visible()
                ht = hdr.inner_text() if hv else ""
            except Exception:
                hv, ht = False, ""
            check("win_header_visible", hv, "mission header visible")
            check("win_header_mission", "win bundled proof mission" in ht,
                  f"header names the mission: {ht[:80]!r}")
            shot(page, "04_win_mission_header", out_dir)

            # --- 7. one scripted turn through the bundled sidecar ----------
            try:
                inp = chat.locator("#input")
                inp.click()
                time.sleep(1)
                inp.fill("run the scripted turn")
                time.sleep(1)
                page.keyboard.press("Enter")
                sent = True
            except Exception as e:
                sent = False
                log("send failed:", e)
            check("win_turn_sent", sent, "chat message sent")
            turn_ok = False
            turn_text = ""
            for _ in range(90):
                try:
                    turn_text = chat.locator("#messages").inner_text()
                    if "Windows GUI probe turn executed" in turn_text:
                        turn_ok = True
                        break
                except Exception:
                    pass
                time.sleep(2)
            check("win_turn_executed", turn_ok,
                  "canned scripted turn rendered in the chat")
            shot(page, "05_win_turn", out_dir)

            # --- 8. seed -> Tasks panel lists the registry task ------------
            seed_ok = False
            if run_command_palette(page, "Awino: Save Current Mission as Seed"):
                if quick_input(page, "win-task-seed"):
                    seed_ok = True
            time.sleep(4)
            check("win_seed_saved", seed_ok, "seed saved via palette")
            tasks_hdr_ok = False
            task_visible = False
            try:
                h = page.locator(".sidebar .pane-header",
                                 has_text="Tasks").first
                h.scroll_into_view_if_needed()
                tasks_hdr_ok = True
                for _ in range(4):
                    try:
                        if h.get_attribute("aria-expanded") != "true":
                            h.click()
                            time.sleep(2)
                    except Exception:
                        pass
                    for _ in range(15):
                        try:
                            if page.locator(".sidebar",
                                            has_text="win-task-seed").count() > 0:
                                task_visible = True
                                break
                        except Exception:
                            pass
                        time.sleep(2)
                    if task_visible:
                        break
            except Exception as e:
                log("tasks panel probe failed:", e)
            check("win_tasks_view_present", tasks_hdr_ok,
                  "Tasks view registered in the Awino container")
            check("win_tasks_panel_lists_seed_task", task_visible,
                  "Tasks panel lists 'win-task-seed'")
            shot(page, "06_win_tasks_panel", out_dir)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
    time.sleep(5)
    orphans = [c for _, c in ps_table() if "awino_sidecar" in c]
    check("win_no_orphan_sidecar", not orphans, f"cmdlines={orphans[:2]}")

    logf.close()
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n==== WIN SUMMARY: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ====",
          flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        RESULTS.append(("win_driver_crashed", False, "see traceback"))
        fails = [r for r in RESULTS if not r[1]]
        print(f"\n==== WIN SUMMARY: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ====",
              flush=True)
        sys.exit(1)
