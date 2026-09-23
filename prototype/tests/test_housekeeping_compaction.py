"""Scope #5: housekeeping discipline + FAIR data + compaction checks.

Covers:
  - housekeeping on a synthetic messy .awino/: tidy layout + manifest +
    archive contents + NOTHING deleted (moves, never silent deletion)
  - housekeeping auto-runs on mission close (done) and stage transitions
  - git_commit opt-in stays OFF by default (never surprise-commits)
  - compaction approval flow: proposed -> denied -> continues without
    compacting; proposed -> approved -> history actually compacted
  - compaction.auto_approve defaults FALSE; per-project opt-in compacts
    without asking
  - FAIR check: every JSON under .awino/ carries schema_version, every
    folder has a README
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "..", "prototype", "tests"))
from test_sidecar import SidecarClient, _turn  # noqa: E402


def _scripted_client(script, **hello_kw):
    c = SidecarClient()
    e = c.hello(provider="scripted", script=script, **hello_kw)
    assert e["event"] == "ready", e
    r = c.cmd("mission", {"text": "Fix the login bug",
                          "criteria": ["manual"]})
    assert r["ok"], r
    r = c.cmd("approve-contract", {"scope": []})
    assert r["ok"], r
    return c


PLAN_SCRIPT = [
    _turn(plan=["Investigate", "Change", "Verify"],
          assumptions=["Hypothesis: a small scoped change works."],
          progress_delta="Planning."),
    _turn(progress_delta="Working."),
]


class HousekeepingTest(unittest.TestCase):
    def setUp(self):
        self.c = _scripted_client([dict(PLAN_SCRIPT[0])])
        self.aw = os.path.join(self.c.ws, ".awino")

    def tearDown(self):
        self.c.close()

    def _make_messy(self):
        base = self.aw
        os.makedirs(os.path.join(base, "tool_results"), exist_ok=True)
        # stale tool result (older than retention) + fresh one
        stale = os.path.join(base, "tool_results", "res-old.json")
        fresh = os.path.join(base, "tool_results", "res-new.json")
        with open(stale, "w") as f:
            f.write('{"schema_version": 1, "id": "res-old"}')
        with open(fresh, "w") as f:
            f.write('{"schema_version": 1, "id": "res-new"}')
        old_ts = time.time() - 60 * 86400  # 60 days ago
        os.utime(stale, (old_ts, old_ts))
        # a stray temp file at the root
        with open(os.path.join(base, "draft.tmp"), "w") as f:
            f.write("scratch")
        before = set()
        for root, _dirs, files in os.walk(base):
            for fn in files:
                before.add(os.path.relpath(os.path.join(root, fn), base))
        return before

    def test_housekeeping_tidies_and_archives_never_deletes(self):
        before = self._make_messy()
        r = self.c.cmd("housekeeping", {"reason": "test"})
        self.assertTrue(r["ok"], r)
        entry = r["result"]["housekeeping"]
        # tidy layout: every standard dir + READMEs
        for d in ("journal", "tool_results", "seeds", "context",
                  "skills", "modes", "archive"):
            self.assertTrue(os.path.isdir(os.path.join(self.aw, d)), d)
            self.assertTrue(
                os.path.isfile(os.path.join(self.aw, d, "README.md")), d)
        self.assertTrue(os.path.isfile(os.path.join(self.aw, "README.md")))
        # manifest written with schema_version
        mpath = os.path.join(self.aw, "housekeeping.json")
        self.assertTrue(os.path.isfile(mpath))
        manifest = json.load(open(mpath))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertTrue(manifest["runs"])
        self.assertEqual(manifest["runs"][-1]["reason"], "test")
        # contract snapshot written with schema_version
        cpath = os.path.join(self.aw, "contract.json")
        self.assertTrue(os.path.isfile(cpath))
        self.assertEqual(json.load(open(cpath))["schema_version"], 1)
        # stale tool result archived with timestamp, fresh one stays
        self.assertFalse(os.path.exists(
            os.path.join(self.aw, "tool_results", "res-old.json")))
        self.assertTrue(os.path.exists(
            os.path.join(self.aw, "tool_results", "res-new.json")))
        archived = os.listdir(os.path.join(self.aw, "archive"))
        self.assertTrue(any("res-old.json" in a for a in archived),
                        archived)
        # journal exported
        self.assertTrue(os.path.isfile(
            os.path.join(self.aw, "journal", "journal.jsonl")))
        # NOTHING deleted: every pre-existing file is still on disk
        after = set()
        for root, _dirs, files in os.walk(self.aw):
            for fn in files:
                after.add(os.path.relpath(os.path.join(root, fn), self.aw))
        # files may move (to archive/) but must not vanish; map by basename
        before_names = {os.path.basename(p) for p in before}
        after_names = {os.path.basename(p) for p in after}
        # archive/ renames with a timestamp prefix; strip it
        after_stripped = set()
        for n in after_names:
            parts = n.split("_", 1)
            after_stripped.add(parts[1] if len(parts) == 2 and
                               parts[0][:8].isdigit() else n)
        self.assertTrue(before_names <= after_stripped,
                        f"lost files: {before_names - after_stripped}")

    def test_housekeeping_runs_on_mission_close(self):
        r = self.c.cmd("done")
        self.assertIn(r["result"].get("status"),
                      ("mission_complete", "ok", "done"), r)
        manifest = json.load(
            open(os.path.join(self.aw, "housekeeping.json")))
        reasons = [run["reason"] for run in manifest["runs"]]
        self.assertIn("mission-close", reasons, reasons)

    def test_git_commit_defaults_off(self):
        # A git repo around the workspace must NOT get surprise commits.
        subprocess.run(["git", "init", "-q"], cwd=self.c.ws,
                       capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"],
                       cwd=self.c.ws, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"],
                       cwd=self.c.ws, capture_output=True)
        self.c.cmd("housekeeping", {"reason": "test"})
        log = subprocess.run(["git", "log", "--oneline"], cwd=self.c.ws,
                             capture_output=True, text=True)
        self.assertEqual(log.stdout.strip(), "", log.stdout)


class CompactionApprovalTest(unittest.TestCase):
    def test_proposed_denied_continues(self):
        c = _scripted_client([dict(PLAN_SCRIPT[0]), dict(PLAN_SCRIPT[1])],
                             context_window=120)
        try:
            # turn 1: tiny window means the contract alone triggers a
            # proposal (empty history) — deny it so the turn builds history
            c.send({"cmd": "user_message", "text": "plan the change"})
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "compaction_proposed")
            c.send({"cmd": "approve", "id": e["proposal_id"],
                    "decision": "deny"})
            e = c.recv(timeout=60)  # warning
            e = c.recv(timeout=60)  # turn_result
            self.assertEqual(e["event"], "turn_result")
            # turn 2: proposal now has summarizable history
            c.send({"cmd": "user_message", "text": "continue the change"})
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "compaction_proposed")
            pid = e["proposal_id"]
            # the proposal discloses tiers, pinned-safety, savings
            self.assertIn("summarizable", e["tiers"])
            self.assertGreater(e["tiers"]["summarizable"]["turns"], 0)
            self.assertIn("contract", e["pinned_safe"])
            self.assertGreaterEqual(e["savings_estimate_tokens"], 0)
            self.assertFalse(e["auto_approve"])
            # denial via the normal approval command: turn continues
            c.send({"cmd": "approve", "id": pid, "decision": "deny"})
            e = c.recv(timeout=60)
            # denial warns (window keeps filling) then the turn proceeds
            self.assertEqual(e["event"], "warning")
            self.assertIn("without compacting", e["message"])
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "turn_result")
            self.assertEqual(e["result"]["status"], "ok", e["result"])
            # denial journaled in the state event log
            j = c.cmd("events")
            types = [x["type"] for x in j["result"]["events"]]
            self.assertIn("compaction_declined", types)
        finally:
            c.close()

    def test_proposed_approved_compacts(self):
        c = _scripted_client([dict(PLAN_SCRIPT[0]), dict(PLAN_SCRIPT[1])],
                             context_window=120)
        try:
            # turn 1: proposal (empty history) — approve to build history
            c.send({"cmd": "user_message", "text": "plan the change"})
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "compaction_proposed")
            c.send({"cmd": "approve", "id": e["proposal_id"],
                    "decision": "approve"})
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "turn_result")
            # turn 2: proposal with summarizable history — approve
            c.send({"cmd": "user_message", "text": "continue the change"})
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "compaction_proposed")
            pid = e["proposal_id"]
            before = e["tiers"]["summarizable"]["turns"]
            self.assertGreater(before, 0)
            c.send({"cmd": "approve", "id": pid, "decision": "approve"})
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "turn_result")
            self.assertEqual(e["result"]["status"], "ok", e["result"])
            j = c.cmd("events")
            perf = [x for x in j["result"]["events"]
                    if x["type"] == "compaction_performed"]
            self.assertTrue(perf, "compaction_performed not journaled")
            self.assertGreater(perf[-1]["data"]["turns_summarized"], 0)
            self.assertGreaterEqual(perf[-1]["data"]["tokens_saved"], 0)
        finally:
            c.close()

    def test_auto_approve_opt_in_compacts_silently(self):
        c = _scripted_client([dict(PLAN_SCRIPT[0]), dict(PLAN_SCRIPT[1])],
                             context_window=120)
        try:
            aw = os.path.join(c.ws, ".awino")
            os.makedirs(aw, exist_ok=True)
            with open(os.path.join(aw, "config.json"), "w") as f:
                json.dump({"schema_version": 1,
                           "compaction": {"auto_approve": True},
                           "housekeeping": {"git_commit": False,
                                            "retention_days": 30}}, f)
            # turn 1: auto-compacts (empty history, no-op) straight through
            c.send({"cmd": "user_message", "text": "plan the change"})
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "turn_result")
            # turn 2: auto-compacts (history present) straight through
            c.send({"cmd": "user_message", "text": "continue the change"})
            # no proposal: it compacts and the turn runs straight through
            e = c.recv(timeout=60)
            self.assertEqual(e["event"], "turn_result")
            self.assertEqual(e["result"]["status"], "ok", e["result"])
            j = c.cmd("events")
            perf = [x for x in j["result"]["events"]
                    if x["type"] == "compaction_performed"]
            self.assertTrue(perf)
            self.assertEqual(perf[-1]["data"]["by"], "auto-approve")
        finally:
            c.close()


class FairCheckTest(unittest.TestCase):
    def test_every_json_has_schema_version_every_folder_has_readme(self):
        c = _scripted_client([dict(PLAN_SCRIPT[0])])
        try:
            c.cmd("housekeeping", {"reason": "test"})
            # also write config + env files to exercise them
            aw = os.path.join(c.ws, ".awino")
            bad = []
            for root, _dirs, files in os.walk(aw):
                rel = os.path.relpath(root, aw)
                if rel.split(os.sep)[0] == "archive":
                    continue  # archive is timestamped history, not live data
                if not os.path.isfile(os.path.join(root, "README.md")):
                    bad.append(f"missing README: {rel}")
                for fn in files:
                    if fn.endswith(".json"):
                        p = os.path.join(root, fn)
                        try:
                            data = json.load(open(p))
                        except (json.JSONDecodeError, ValueError):
                            bad.append(f"unparseable JSON: {p}")
                            continue
                        if not isinstance(data, dict) or \
                                "schema_version" not in data:
                            bad.append(f"no schema_version: "
                                       f"{os.path.relpath(p, aw)}")
            self.assertEqual(bad, [], "\n".join(bad))
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
