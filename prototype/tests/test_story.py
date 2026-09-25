"""STORY.md story ledger: render, rituals, planning gate, seed filing.

The registry (.awino/registry/stories.json) is the single source of truth;
STORY.md is rendered from it on every mutation. The planning gate refuses
->BUILD when the active story's spine (problem/approach/done criteria) is
empty — same hard-refuse pattern as the VERIFY gate.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import story
from story import (StoryStore, render_story_md, story_start, story_update,
                   story_close, touch_session, mark_ready_to_close,
                   doing_stories, open_stories_summary, file_seeds,
                   find_or_create_for_seed, plan_story, session_begin,
                   session_end, story_time_spent, format_duration,
                   check_stale_on_mission, expand_bugatti,
                   seed_dag_from_story, parked_due_summary,
                   convert_parked_to_spike)

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from common import make_loop, drive_verification


class StoryTestBase(unittest.TestCase):
    """Hermetic temp dirs.

    Some sandboxes have no usable /tmp, so tempfile falls back to the
    current directory — temp dirs would land inside the harness's own
    repo, and story_start's git branch creation would escape into the
    REAL repo. This base redirects every mkdtemp (including make_loop's)
    under one controlled dir, confines git discovery with
    GIT_CEILING_DIRECTORIES, and removes everything on cleanup.
    """

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="awino-story-base-"))
        self._old_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self._tmp)
        self._env = mock.patch.dict(
            os.environ, {"GIT_CEILING_DIRECTORIES": str(self._tmp)})
        self._env.start()

        def _cleanup():
            self._env.stop()
            tempfile.tempdir = self._old_tempdir
            shutil.rmtree(self._tmp, ignore_errors=True)
        self.addCleanup(_cleanup)

    def mktemp(self, prefix="awino-story-"):
        d = Path(tempfile.mkdtemp(prefix=prefix))
        self.assertTrue(str(d).startswith(str(self._tmp)),
                        f"temp dir escaped the test base: {d}")
        return d


def _awino_dir(test, root=None):
    root = Path(root or test.mktemp(prefix="awino-story-proj-"))
    awd = root / ".awino"
    from registry import Registry
    Registry(awd).ensure()
    return awd


class RenderTest(StoryTestBase):
    def test_render_contains_every_field(self):
        awd = _awino_dir(self)
        store = StoryStore(awd)
        s = store.create("Rate limit the login", type="story", status="doing",
                         problem="Brute-force logins are unthrottled.",
                         description="Add a token bucket.",
                         approach="Middleware + Redis counters.",
                         done_criteria=["100 bad logins blocked",
                                        "p99 latency < 5ms"],
                         blockers=["need Redis in CI"],
                         seeds=["seed:auth-notes"],
                         branch="story/rate-limit-the-login")
        touch_session(awd, s["id"], "planned the approach")
        text = render_story_md(awd)
        for needle in ("Rate limit the login", "st-", "doing",
                       "Brute-force logins", "token bucket",
                       "Middleware + Redis", "100 bad logins blocked",
                       "need Redis in CI", "seed:auth-notes",
                       "story/rate-limit-the-login", "planned the approach",
                       "GENERATED FILE", "stories.json"):
            self.assertIn(needle, text)
        # written to the project root
        self.assertEqual((awd.parent / "STORY.md").read_text(), text)

    def test_render_idempotent(self):
        awd = _awino_dir(self)
        store = StoryStore(awd)
        store.create("A", type="spike", status="open",
                     done_criteria=["learn X"])
        store.create("B", type="chore", status="blocked",
                     blockers=["waiting on Y"])
        first = render_story_md(awd)
        second = render_story_md(awd)
        self.assertEqual(first, second)

    def test_render_empty_registry(self):
        awd = _awino_dir(self)
        text = render_story_md(awd)
        self.assertIn("No open stories.", text)
        self.assertIn("brag board", text)


class SessionReviewTest(StoryTestBase):
    def test_existing_project_with_open_stories_emits_review(self):
        from bootstrap import session_start_auto_init
        root = self.mktemp("awino-review-")
        awd = root / ".awino"
        (root / ".awino").mkdir(parents=True)
        (awd / "project.yaml").write_text("project: demo\n")
        from registry import Registry
        Registry(awd).ensure()
        story_start(awd, "Ship the thing", type="story")
        # second session start in the existing project: no init, but the
        # mandatory stories review fires
        result = session_start_auto_init(root)
        self.assertIsNotNone(result)
        self.assertFalse(result["initialized"])
        review = result["stories_review"]
        self.assertIsNotNone(review)
        self.assertEqual(len(review["stories"]), 1)
        self.assertEqual(review["stories"][0]["title"], "Ship the thing")
        self.assertTrue(any("Ship the thing" in line
                            for line in review["lines"]))
        # the review event is journaled (durable proof it was presented)
        crumbs = Registry(awd).breadcrumbs()
        self.assertTrue(any("Stories review presented" in c["note"]
                            for c in crumbs))

    def test_empty_registry_no_event_no_crash(self):
        from bootstrap import session_start_auto_init
        root = self.mktemp("awino-review-empty-")
        (root / ".awino").mkdir(parents=True)
        (root / ".awino" / "project.yaml").write_text("project: demo\n")
        result = session_start_auto_init(root)
        # silent: nothing to set up, nothing open
        self.assertIsNone(result)


class PlanningGateTest(StoryTestBase):
    def _loop_with_story(self, **story_fields):
        from registry import Registry
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        fields = {"title": "Gate story", "type": "story", "status": "doing"}
        fields.update(story_fields)
        store = StoryStore(awd)
        st = store.create(**fields)
        loop.set_mission("Do the gated thing", ["manual"])
        return loop, st

    def _to_build(self, loop):
        loop.approve_contract()          # DEFINE -> PLAN
        return loop.approve_contract(["out.txt"])  # PLAN -> BUILD (gated)

    def test_refused_when_problem_missing(self):
        loop, _ = self._loop_with_story(approach="the spine",
                                        done_criteria=["done when x"])
        r = self._to_build(loop)
        self.assertEqual(r["status"], "refused")
        self.assertIn("problem", r["said"])
        self.assertIn("Next action", r["said"])
        self.assertEqual(loop.state.snapshot["phase"], "PLAN")

    def test_refused_when_approach_missing(self):
        loop, _ = self._loop_with_story(problem="the problem",
                                        done_criteria=["done when x"])
        r = self._to_build(loop)
        self.assertEqual(r["status"], "refused")
        self.assertIn("approach", r["said"])

    def test_refused_when_done_criteria_missing(self):
        loop, _ = self._loop_with_story(problem="the problem",
                                        approach="the spine")
        r = self._to_build(loop)
        self.assertEqual(r["status"], "refused")
        self.assertIn("done criteria", r["said"])

    def test_allowed_when_spine_complete(self):
        loop, _ = self._loop_with_story(problem="the problem",
                                        approach="the spine",
                                        done_criteria=["done when x"])
        r = self._to_build(loop)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")

    def test_no_gate_without_doing_story(self):
        # a mission with no active story is unaffected (existing flows keep
        # working; the gate only binds story-led work)
        loop, home = make_loop()
        from registry import Registry
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        loop.set_mission("Ungated mission", ["manual"])
        loop.approve_contract()
        r = loop.approve_contract(["out.txt"])
        self.assertEqual(r["status"], "ok")

    def test_refusal_is_journaled(self):
        loop, _ = self._loop_with_story(problem="the problem")
        self._to_build(loop)
        self.assertTrue(any(e["type"] == "transition_refused"
                            for e in loop.state.events))

    def test_gate_lifts_after_spine_filled(self):
        loop, st = self._loop_with_story(problem="the problem",
                                         approach="the spine")
        r = self._to_build(loop)
        self.assertEqual(r["status"], "refused")
        story_update(loop.registry.awino_dir, st["id"],
                     done_criteria=["done when x"])
        r = loop.approve_contract(["out.txt"])
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")


class StoryStartTest(StoryTestBase):
    def test_creates_branch_in_git_repo(self):
        if not shutil_which_git():
            self.skipTest("git not available")
        root = self.mktemp("awino-branch-")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"],
                       cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"],
                       cwd=root, check=True, capture_output=True)
        (root / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=root,
                       check=True, capture_output=True)
        awd = root / ".awino"
        st = story_start(awd, "Add the thing!", type="story")
        self.assertEqual(st["branch"], "story/add-the-thing")
        r = subprocess.run(["git", "branch", "--show-current"], cwd=root,
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "story/add-the-thing")
        self.assertIn("created branch", st["branch_note"])
        # STORY.md rendered at the project root
        self.assertIn("Add the thing!", (root / "STORY.md").read_text())

    def test_missing_git_warns_never_fails(self):
        awd = _awino_dir(self)
        with mock.patch.object(story.shutil, "which", return_value=None):
            st = story_start(awd, "No git here", type="spike")
        self.assertIn("warning only", st["branch_note"])
        self.assertEqual(st["status"], "open")  # story still created
        self.assertIn("No git here", (awd.parent / "STORY.md").read_text())

    def test_not_a_repo_warns_never_fails(self):
        root = self.mktemp("awino-norepo-")
        awd = root / ".awino"
        st = story_start(awd, "No repo", type="chore")
        self.assertIn("warning only", st["branch_note"])


def shutil_which_git():
    import shutil
    return shutil.which("git")


class SeedFilingTest(StoryTestBase):
    def _seeds(self, root: Path):
        seeds = root / ".awino" / "seeds"
        seeds.mkdir(parents=True)
        (seeds / "auth.md").write_text(
            "---\nstory: Login hardening\n---\n"
            "- [ ] throttle logins\n- [x] add audit log\n")
        (seeds / "notes.md").write_text(
            "# random notes\n- [ ] remember the thing\n")
        (seeds / "broken.md").write_text("---\n: bad [\n---\n- [ ] survives\n")
        return seeds

    def test_frontmatter_story_link_and_inbox(self):
        from bootstrap import collect_seed_tasks
        root = self.mktemp("awino-seedfile-")
        self._seeds(root)
        _, tasks = collect_seed_tasks(root / ".awino" / "seeds")
        by_seed = {}
        for t in tasks:
            by_seed.setdefault(t["seed_file"], t["story"])
        self.assertEqual(by_seed["auth"], "Login hardening")
        self.assertIsNone(by_seed["notes"])
        self.assertIsNone(by_seed["broken"])  # malformed fm: ignored, filed
        awd = _awino_dir(self, root)
        from registry import Registry
        Registry(awd).import_seed_tasks(tasks)
        n = file_seeds(awd, tasks)
        self.assertEqual(n, 3)
        store = StoryStore(awd)
        epic = store.get_by_ref("Login hardening")
        self.assertIsNotNone(epic)
        self.assertIn("seed:auth", epic["seeds"])
        inbox = store.get_by_ref("inbox")
        self.assertIsNotNone(inbox)
        self.assertIn("seed:notes", inbox["seeds"])
        self.assertIn("seed:broken", inbox["seeds"])
        # tasks stamped with their story
        reg_tasks = [t for t in Registry(awd).tasks()]
        auth_tasks = [t for t in reg_tasks
                      if t.get("source") == "seed:auth"]
        self.assertTrue(auth_tasks)
        self.assertTrue(all(t.get("story_id") == epic["id"]
                            for t in auth_tasks))

    def test_file_seeds_idempotent(self):
        from bootstrap import collect_seed_tasks
        root = self.mktemp("awino-seedfile2-")
        self._seeds(root)
        _, tasks = collect_seed_tasks(root / ".awino" / "seeds")
        awd = _awino_dir(self, root)
        file_seeds(awd, tasks)
        first = (awd.parent / "STORY.md").read_text()
        file_seeds(awd, tasks)
        second = (awd.parent / "STORY.md").read_text()
        # no duplicate seed links, no duplicate stories
        store = StoryStore(awd)
        self.assertEqual(len(store.list()), 2)  # epic + inbox
        inbox = store.get_by_ref("inbox")
        self.assertEqual(sorted(inbox["seeds"]),
                         ["seed:broken", "seed:notes"])

    def test_full_init_flow_files_seeds(self):
        # the init path wires filing (slow: real venv) — one integration
        # proof that init leaves STORY.md with the inbox story
        from bootstrap import full_init_flow
        root = self.mktemp("awino-initfile-")
        self._seeds(root)
        report = full_init_flow(root)
        self.assertGreater(report.get("seeds_filed", 0), 0)
        text = (root / "STORY.md").read_text()
        self.assertIn("Inbox", text)
        self.assertIn("Login hardening", text)


class CloseRitualTest(StoryTestBase):
    def test_verifier_pass_asks_user_to_close(self):
        from registry import Registry
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        store = StoryStore(awd)
        st = store.create("Gated feature", type="story", status="doing",
                          problem="p", approach="a",
                          done_criteria=["manual (operator sign-off)"])
        loop.set_mission("Ship gated feature", ["manual"])
        loop.approve_contract()
        loop.approve_contract(["out.txt"])
        self.assertEqual(loop.state.snapshot["phase"], "BUILD")
        loop.request_phase("VERIFY", reason="test")
        res = drive_verification(
            loop, evidence_links={"manual (operator sign-off)":
                                  "tests/common.py"})
        self.assertTrue(res["passed"])
        # the harness journaled story_ready_to_close and ASKS — it did not
        # close the story itself
        ev = [e for e in loop.state.events
              if e["type"] == "story_ready_to_close"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["data"]["story_id"], st["id"])
        self.assertIn("ready to close", res["said"])
        self.assertEqual(StoryStore(awd).get(st["id"])["status"], "doing")
        self.assertTrue(StoryStore(awd).get(st["id"])["ready_to_close"])

    def test_story_close_stamps_brag_board(self):
        awd = _awino_dir(self)
        store = StoryStore(awd)
        a = store.create("First win", type="story", status="doing",
                         problem="p1", approach="a1",
                         done_criteria=["c1"])
        b = store.create("Second win", type="spike", status="doing",
                         problem="p2", approach="a2",
                         done_criteria=["c2"])
        story_close(awd, a["id"], "shipped, users happy")
        story_close(awd, b["id"], "spike answered: yes, viable")
        text = (awd.parent / "STORY.md").read_text()
        # brag board accumulates both, with outcomes and closed dates
        self.assertIn("First win", text)
        self.assertIn("shipped, users happy", text)
        self.assertIn("Second win", text)
        self.assertIn("spike answered: yes, viable", text)
        self.assertIn("## Brag board", text)
        closed = store.get(a["id"])
        self.assertEqual(closed["status"], "done")
        self.assertTrue(closed["closed_ts"])
        # closing an unknown story is a clean KeyError, not a crash
        with self.assertRaises(KeyError):
            story_close(awd, "st-nope", "x")


class CliStoriesTest(StoryTestBase):
    def test_awino_stories_lists_plainly(self):
        import subprocess as sp
        root = self.mktemp("awino-cli-stories-")
        awd = _awino_dir(self, root)
        store = StoryStore(awd)
        store.create("Alpha", type="story", status="doing",
                     blockers=["waiting on API key"])
        store.create("Beta", type="chore", status="open")
        render_story_md(awd)
        proto = Path(__file__).resolve().parent.parent
        r = sp.run([sys.executable, str(proto / "cli.py"), "stories",
                    str(root)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 doing, 1 open", r.stdout)
        self.assertIn("Alpha", r.stdout)
        self.assertIn("waiting on API key", r.stdout)
        self.assertIn("STORY.md", r.stdout)

    def test_awino_stories_not_a_project(self):
        import subprocess as sp
        root = self.mktemp("awino-cli-noproj-")
        proto = Path(__file__).resolve().parent.parent
        r = sp.run([sys.executable, str(proto / "cli.py"), "stories",
                    str(root)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("next action", r.stderr)


class StaleStoryTest(StoryTestBase):
    def test_story_start_with_open_story_warns(self):
        from registry import Registry
        root = self.mktemp("awino-stale-start-")
        awd = _awino_dir(self, root)
        first = story_start(awd, "First thing")
        story_update(awd, first["id"], status="doing")
        second = story_start(awd, "Second thing")
        self.assertTrue(second["stale_warning"])
        self.assertIn("still open", second["stale_warning"])
        self.assertIn("First thing", second["stale_warning"])
        crumbs = Registry(awd).breadcrumbs()
        self.assertTrue(any("[stale_stories]" in c["note"]
                            for c in crumbs))
        # blocked stories don't count
        story_update(awd, first["id"], status="blocked")
        story_update(awd, second["id"], status="blocked")
        third = story_start(awd, "Third thing")
        self.assertEqual(third["stale_warning"], "")

    def test_new_mission_with_doing_story_journals_stale(self):
        from registry import Registry
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        st = StoryStore(awd).create("Unfinished business", status="doing")
        mission = loop.set_mission("Write the file", ["manual"])
        self.assertIn("stale_warning", mission)
        self.assertIn("Unfinished business", mission["stale_warning"])
        self.assertIn("stale_stories",
                      [e["type"] for e in loop.state.events])

    def test_stale_check_no_registry_never_raises(self):
        self.assertEqual(check_stale_on_mission("/nonexistent-xyz-123"), "")


class StoriesNudgeTest(StoryTestBase):
    def _loop_with_idle_story(self):
        from registry import Registry
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        st = StoryStore(awd).create("Idle story", status="doing")
        return loop, awd, st

    def test_nudge_fires_at_boundary(self):
        loop, awd, st = self._loop_with_idle_story()
        with mock.patch.object(story, "STORIES_NUDGE_EVERY", 3):
            for _ in range(3):
                loop.run_user_turn("status update please")
        nudges = [e for e in loop.state.events
                  if e["type"] == "stories_nudge"]
        self.assertEqual(len(nudges), 1)
        self.assertEqual(nudges[0]["data"]["turn"], 3)
        self.assertIn(st["id"], nudges[0]["data"]["untouched"])
        # next boundary fires again, exactly once
        with mock.patch.object(story, "STORIES_NUDGE_EVERY", 3):
            for _ in range(3):
                loop.run_user_turn("another update")
        nudges = [e for e in loop.state.events
                  if e["type"] == "stories_nudge"]
        self.assertEqual(len(nudges), 2)
        self.assertEqual(nudges[1]["data"]["turn"], 6)

    def test_touched_story_does_not_nudge(self):
        loop, awd, st = self._loop_with_idle_story()
        touch_session(awd, st["id"], "worked on it this session")
        with mock.patch.object(story, "STORIES_NUDGE_EVERY", 3):
            for _ in range(3):
                loop.run_user_turn("status update please")
        nudges = [e for e in loop.state.events
                  if e["type"] == "stories_nudge"]
        self.assertEqual(nudges, [])


class EffortTest(StoryTestBase):
    def test_two_fake_sessions_accumulate(self):
        root = self.mktemp("awino-effort-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Timed work")
        t0 = 1_700_000_000.0
        session_begin(awd, s["id"], "morning", started_ts=t0)
        session_end(awd, s["id"], ended_ts=t0 + 3600)        # 1h
        session_begin(awd, s["id"], "afternoon", started_ts=t0 + 7200)
        session_end(awd, s["id"], ended_ts=t0 + 7200 + 1800)  # 30m
        self.assertEqual(story_time_spent(awd, s["id"]), 5400.0)
        text = render_story_md(awd)
        self.assertIn("**Time dedicated:** 1h 30m", text)

    def test_cli_shows_time_dedicated(self):
        import subprocess as sp
        root = self.mktemp("awino-effort-cli-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Timed work")
        t0 = 1_700_000_000.0
        session_begin(awd, s["id"], "work", started_ts=t0)
        session_end(awd, s["id"], ended_ts=t0 + 5400)
        proto = Path(__file__).resolve().parent.parent
        r = sp.run([sys.executable, str(proto / "cli.py"), "stories",
                    str(root)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("time dedicated 1h 30m", r.stdout)


class OrderingTest(StoryTestBase):
    def test_newest_open_on_top_and_brag_board(self):
        root = self.mktemp("awino-order-")
        awd = _awino_dir(self, root)
        store = StoryStore(awd)
        store.ensure()
        a = store.create("Older story", status="open")
        b = store.create("Newer story", status="open")
        store.update(a["id"], created_ts=1000.0)
        store.update(b["id"], created_ts=2000.0)
        text = render_story_md(awd)
        self.assertLess(text.index("Newer story"), text.index("Older story"))
        # closing moves it out of the open sections into the Brag board
        story_close(awd, b["id"], "shipped it")
        text = render_story_md(awd)
        open_part = text.split("## Brag board")[0]
        self.assertNotIn("Newer story", open_part)
        self.assertIn("Older story", open_part)
        brag = text.split("## Brag board")[1]
        self.assertIn("Newer story", brag)
        self.assertIn("shipped it", brag)
        self.assertIn("Time dedicated:", brag)


class GitCloseTest(StoryTestBase):
    def test_close_without_remote_pushes_best_effort(self):
        import subprocess as sp
        from registry import Registry
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        root = self.mktemp("awino-gitclose-")
        sp.run(["git", "init", "-q"], cwd=root, check=True,
               timeout=60)
        sp.run(["git", "config", "user.email", "t@t"], cwd=root,
               check=True, timeout=60)
        sp.run(["git", "config", "user.name", "t"], cwd=root,
               check=True, timeout=60)
        (Path(root) / "f.txt").write_text("x")
        sp.run(["git", "add", "."], cwd=root, check=True, timeout=60)
        sp.run(["git", "commit", "-qm", "init"], cwd=root, check=True,
               timeout=60)
        awd = _awino_dir(self, root)
        s = story_start(awd, "Push me")
        closed = story_close(awd, s["id"], "donezo")
        self.assertEqual(closed["status"], "done")
        # no remote -> push fails fast, journaled, mission continues
        self.assertIn("push", closed["push_note"].lower())
        crumbs = Registry(awd).breadcrumbs()
        self.assertTrue(any("[story_close]" in c["note"]
                            and "Git:" in c["note"] for c in crumbs))


PLAN_PARTS = {
    "breakdown": ("The problem reduces to: work items need a visible ledger. "
                  "[Certain] the ledger must be generated from the registry, "
                  "not hand-edited."),
    "surveyed": ("Jira is heavyweight [Certain]; a markdown file is simple "
                 "but drifts [Likely]; generating from the registry keeps "
                 "one source of truth."),
    "user_guidance": "The user asked for a Jira-style ledger that doubles as a brag board.",
    "proposal": ("Option A (Honda): generate STORY.md from stories.json on "
                 "every mutation [Certain]. Option B: hand-edited markdown "
                 "[Likely] drifts. Option C: a separate web UI — overkill "
                 "[Guessing]. Pick A: the registry stays the single source "
                 "of truth [Certain]."),
    "steps": [
        {"title": "Write the story store",
         "success": "stories.json round-trips",
         "failure": "schema unclear after 1h — spike it first"},
        {"title": "Render STORY.md",
         "success": "idempotent render, newest on top",
         "failure": "render exceeds 200 lines — split sections"},
        {"title": "Wire the session review", "depends_on": [0],
         "success": "open stories presented at session start",
         "failure": "review adds >2s to startup — make it lazy"},
    ],
    "bugatti_brief": ("Bugatti: the ledger becomes a year-end awards site — "
                      "each done story a trophy card with stats. What we'd "
                      "get: a brag board worth sharing. Brief only — ask to "
                      "expand."),
}


class PlanShapeTest(StoryTestBase):
    def test_plan_writes_four_subsections_with_calibration(self):
        from registry import Registry
        root = self.mktemp("awino-plan-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Plan me")
        res = plan_story(awd, s["id"], **PLAN_PARTS)
        text = render_story_md(awd)
        for header in ("### (a) First-principles breakdown",
                       "### (b) Existing approaches surveyed",
                       "### (c) User guidance incorporated",
                       "### (d) Proposed approach",
                       "### (e) Ordered steps",
                       "### (f) Bugatti proposal"):
            self.assertIn(header, text)
        for label in ("[Certain]", "[Likely]", "[Guessing]"):
            self.assertIn(label, text)
        # steps render in order with their criteria
        self.assertLess(text.index("1. **Write the story store**"),
                        text.index("2. **Render STORY.md**"))
        self.assertLess(text.index("2. **Render STORY.md**"),
                        text.index("3. **Wire the session review**"))
        self.assertIn("success: stories.json round-trips", text)
        self.assertIn("fail fast: schema unclear after 1h", text)
        # Bugatti brief present with the iron rule
        self.assertIn("Bugatti: the ledger becomes a year-end awards site",
                      text)
        self.assertIn("pitched, never built unasked", text)
        self.assertEqual(res["planned_stances"],
                         ["ai-architect", "ai-researcher", "software-engineer"])
        # each stance contribution journaled — at least two distinct stances
        crumbs = Registry(awd).breadcrumbs()
        names = set()
        for c in crumbs:
            if "[story_plan_stance]" in c["note"]:
                for cand in ("ai-architect", "ai-researcher",
                             "software-engineer"):
                    if cand in c["note"]:
                        names.add(cand)
        self.assertGreaterEqual(len(names), 2)

    def test_plan_missing_subsection_refused_at_write_time(self):
        root = self.mktemp("awino-plan-missing-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Plan me")
        parts = dict(PLAN_PARTS, surveyed="   ")
        with self.assertRaises(ValueError) as ctx:
            plan_story(awd, s["id"], **parts)
        self.assertIn("surveyed", str(ctx.exception))
        # nothing was written
        self.assertFalse(StoryStore(awd).get(s["id"])["approach"])

    def test_plan_stance_override_and_validation(self):
        root = self.mktemp("awino-plan-stance-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Plan me")
        res = plan_story(awd, s["id"], stances=["software-engineer"],
                         **PLAN_PARTS)
        self.assertEqual(res["planned_stances"], ["software-engineer"])
        s2 = story_start(awd, "Plan me too")
        with self.assertRaises(ValueError):
            plan_story(awd, s2["id"], stances=["not-a-role"], **PLAN_PARTS)

    def test_loop_plan_story_journals_mission_events(self):
        from registry import Registry
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        s = story_start(awd, "Plan me")
        res = loop.plan_story(s["id"], **PLAN_PARTS)
        self.assertEqual(res["status"], "ok")
        events = [e for e in loop.state.events
                  if e["type"] == "story_plan_stance"]
        names = {e["data"]["stance"] for e in events}
        self.assertGreaterEqual(len(names), 2)
        self.assertIn("ai-architect", names)


class StepsDagTest(StoryTestBase):
    def test_steps_seed_dag_preserving_chain(self):
        from registry import Registry
        root = self.mktemp("awino-steps-dag-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Stepped work")
        res = plan_story(awd, s["id"], **PLAN_PARTS)
        self.assertEqual(res["dag_seeded"], 3)
        reg = Registry(awd)
        tasks = [t for t in reg.tasks()
                 if t.get("source") == f"story:{s['id']}"]
        self.assertEqual(len(tasks), 3)
        by_index = {t["step_index"]: t for t in tasks}
        # chain preserved: step 2 after step 1 (default), step 3 after
        # step 1 (explicit depends_on=[0])
        self.assertEqual(by_index[1]["depends_on"], [by_index[0]["id"]])
        self.assertEqual(by_index[2]["depends_on"], [by_index[0]["id"]])
        # the step's success criterion becomes the task's done criteria
        self.assertIn("round-trips", by_index[0]["done_criteria"])
        # the step's failure criterion rides along on the task too
        self.assertIn("schema unclear",
                      by_index[0].get("failure_criteria", ""))
        # idempotent: re-planning seeds nothing new
        res2 = plan_story(awd, s["id"], **PLAN_PARTS)
        self.assertEqual(res2["dag_seeded"], 0)
        tasks2 = [t for t in reg.tasks()
                  if t.get("source") == f"story:{s['id']}"]
        self.assertEqual(len(tasks2), 3)

    def test_steps_require_success_and_failure(self):
        root = self.mktemp("awino-steps-failfast-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Stepped work")
        bad = dict(PLAN_PARTS, steps=[{"title": "No criteria",
                                       "success": "x"}])
        with self.assertRaises(ValueError) as ctx:
            plan_story(awd, s["id"], **bad)
        self.assertIn("failure", str(ctx.exception))
        with self.assertRaises(ValueError):
            plan_story(awd, s["id"], **dict(PLAN_PARTS, steps=[]))
        bad_deps = dict(PLAN_PARTS, steps=[
            {"title": "A", "success": "s", "failure": "f"},
            {"title": "B", "success": "s", "failure": "f",
             "depends_on": [5]}])
        with self.assertRaises(ValueError) as ctx3:
            plan_story(awd, s["id"], **bad_deps)
        self.assertIn("dependency", str(ctx3.exception))

    def test_plan_requires_bugatti_brief(self):
        root = self.mktemp("awino-plan-bugatti-req-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Plan me")
        bad = dict(PLAN_PARTS, bugatti_brief="  ")
        with self.assertRaises(ValueError) as ctx:
            plan_story(awd, s["id"], **bad)
        self.assertIn("bugatti_brief", str(ctx.exception))

    def test_plan_requires_calibration_labels(self):
        root = self.mktemp("awino-plan-calib-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Plan me")
        bad = dict(PLAN_PARTS, proposal="We should generate the markdown. "
                                       "It will work great, trust me.")
        with self.assertRaises(ValueError) as ctx:
            plan_story(awd, s["id"], **bad)
        self.assertIn("[Certain]", str(ctx.exception))


class BugattiTest(StoryTestBase):
    def test_expand_bugatti_on_request_only(self):
        from registry import Registry
        root = self.mktemp("awino-bugatti-")
        awd = _awino_dir(self, root)
        s = story_start(awd, "Plan me")
        plan_story(awd, s["id"], **PLAN_PARTS)
        text = render_story_md(awd)
        # brief present, full breakdown not expanded yet
        self.assertIn("not expanded yet", text)
        full = ("Step 1: trophy cards. Step 2: stats engine. "
                "What we'd get: a shareable year-end review.")
        res = expand_bugatti(awd, s["id"], full)
        self.assertIn("trophy cards", res["approach"])
        text = render_story_md(awd)
        self.assertIn("Full breakdown (expanded on request)", text)
        self.assertIn("trophy cards", text)
        self.assertIn("pitched, never built unasked", text)
        crumbs = Registry(awd).breadcrumbs()
        self.assertTrue(any("[story_bugatti_expanded]" in c["note"]
                            for c in crumbs))
        # expanding nothing is refused
        with self.assertRaises(ValueError):
            expand_bugatti(awd, s["id"], "   ")

    def test_brief_expand_documented(self):
        doc = (Path(__file__).resolve().parent.parent
               / "docs" / "STORY_PLANNING.md")
        self.assertTrue(doc.is_file())
        text = doc.read_text()
        self.assertIn("Brief-first", text)
        self.assertIn("expand_bugatti", text)
        self.assertIn("pitched,\nnever built unasked", text)


class GitSafetyTest(StoryTestBase):
    def test_push_failure_never_echoes_raw_stderr(self):
        from story import _classify_git_failure
        evil = (b"fatal: Authentication failed for "
                b"'https://user:ghp_supersecrettoken123@github.com/x/y.git/'")
        label = _classify_git_failure(evil)
        self.assertNotIn("ghp_supersecrettoken123", label)
        self.assertNotIn("user:ghp", label)
        self.assertIn("auth", label)
        self.assertIn("offline",
                      _classify_git_failure(b"Could not resolve host"))
        self.assertIn("no remote",
                      _classify_git_failure(
                          b"'origin' does not appear to be a git repo"))
        self.assertNotIn(
            "https",
            _classify_git_failure(b"weird https://token@host/x output"))


class ParkedTest(StoryTestBase):
    def test_parked_due_appears_in_session_review(self):
        from datetime import datetime, timedelta
        from bootstrap import session_start_auto_init
        from registry import Registry
        root = self.mktemp("awino-parked-review-")
        (root / ".awino").mkdir(parents=True)
        (root / ".awino" / "project.yaml").write_text("project: demo\n")
        awd = root / ".awino"
        Registry(awd).ensure()
        store = StoryStore(awd)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        p = store.create("Someday idea", status="parked",
                         revisit_on=yesterday)
        # a parked idea with a future date stays quiet
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        store.create("Later idea", status="parked", revisit_on=future)
        result = session_start_auto_init(root)
        self.assertIsNotNone(result)
        review = result["stories_review"]
        self.assertIsNotNone(review)
        self.assertTrue(any("you tabled 'Someday idea'" in line
                            for line in review["lines"]))
        self.assertTrue(any("revisit, discard, or keep parked" in line
                            for line in review["lines"]))
        self.assertFalse(any("Later idea" in line
                             for line in review["lines"]))
        self.assertEqual([s["id"] for s in review["parked_due"]],
                         [p["id"]])

    def test_parked_defaults_to_30_days(self):
        from datetime import datetime, timedelta
        root = self.mktemp("awino-parked-default-")
        awd = _awino_dir(self, root)
        store = StoryStore(awd)
        p = store.create("Table it", status="parked")
        expected = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        self.assertEqual(p["revisit_on"], expected)
        # parking via update defaults too; explicit date is respected
        q = store.create("Table it too")
        story_update(awd, q["id"], status="parked")
        self.assertEqual(StoryStore(awd).get(q["id"])["revisit_on"],
                         expected)
        story_update(awd, q["id"], revisit_on="2026-10-23")
        self.assertEqual(StoryStore(awd).get(q["id"])["revisit_on"],
                         "2026-10-23")

    def test_parked_excluded_from_stale_and_attention(self):
        root = self.mktemp("awino-parked-stale-")
        awd = _awino_dir(self, root)
        store = StoryStore(awd)
        store.create("Parked thing", status="parked")
        # parked ideas don't trip the stale-story rule
        second = story_start(awd, "New thing")
        self.assertEqual(second["stale_warning"], "")
        # ... and doesn't show up in the needs-attention list
        # (only the newly started story does)
        self.assertEqual([s["title"] for s in store.open_stories()],
                         ["New thing"])

    def test_convert_parked_to_spike_preserves_link(self):
        from registry import Registry
        root = self.mktemp("awino-parked-spike-")
        awd = _awino_dir(self, root)
        store = StoryStore(awd)
        p = store.create("White paper idea", status="parked",
                         problem="nobody wrote it down")
        spike = convert_parked_to_spike(
            awd, p["id"], title="Write the white paper",
            done_criteria=["draft exists", "reviewed"])
        self.assertEqual(spike["type"], "spike")
        self.assertEqual(spike["status"], "open")
        self.assertEqual(spike["converted_from"], p["id"])
        self.assertEqual(spike["done_criteria"],
                         ["draft exists", "reviewed"])
        parked = store.get(p["id"])
        self.assertEqual(parked["converted_to"], spike["id"])
        # parked idea itself stays parked
        self.assertEqual(parked["status"], "parked")
        crumbs = Registry(awd).breadcrumbs()
        self.assertTrue(any("[story_parked_converted]" in c["note"]
                            for c in crumbs))
        # converting a non-parked story is refused
        with self.assertRaises(ValueError):
            convert_parked_to_spike(awd, spike["id"])


if __name__ == "__main__":
    unittest.main()


class StoryPlanToolTest(StoryTestBase):
    """Model-callable story_plan: plan with the user (Honda first, Bugatti
    pitched), pass the BUILD gate, close onto the brag board."""

    ARGS = {
        "title": "Brag board in VS Code",
        "problem": "Stories live only in the CLI.",
        "done_criteria": "Stories view lists done stories; close records outcome",
        "breakdown": "Ledger exists; the gap is the surface.",
        "surveyed": "CLI `awino stories` renders it; the extension has nothing.",
        "user_guidance": "User wants the brag board visible where they work.",
        "proposal": ("A (Honda): sidecar command + tree view [Likely]. "
                     "B: webview panel. Pick A: smallest working path."),
        "steps": ("1. Sidecar stories command | returns counts + brag | "
                  "command errors\n"
                  "2. Tree view | done stories render | view empty"),
        "bugatti_brief": "Animated brag wall with time-dedicated charts.",
    }

    def _loop(self):
        from registry import Registry
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        loop.set_mission("Show the brag board", ["manual"])
        return loop, awd

    def test_plan_passes_build_gate_and_closes_to_brag_board(self):
        from story import story_close
        loop, awd = self._loop()
        r = loop._resolve_tool_fn("story_plan")(**self.ARGS)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["dag_seeded"], 2)
        st = StoryStore(awd).get(r["story_id"])
        self.assertEqual(st["status"], "doing")
        self.assertIn("Bugatti", st["approach"])
        loop.approve_contract()
        self.assertEqual(loop.approve_contract(["out.txt"])["status"], "ok")
        story_close(awd, r["story_id"], "shipped the tree view")
        md = (awd.parent / "STORY.md").read_text()
        self.assertIn("Brag board in VS Code", md)
        self.assertIn("shipped the tree view", md)

    def test_uncalibrated_pitch_is_refused(self):
        loop, _ = self._loop()
        args = dict(self.ARGS, proposal="A is best.")
        r = loop._resolve_tool_fn("story_plan")(**args)
        self.assertIn("calibrate", r.get("error", ""))

    def test_malformed_step_is_refused(self):
        loop, _ = self._loop()
        r = loop._resolve_tool_fn("story_plan")(
            **dict(self.ARGS, steps="just do it"))
        self.assertIn("title | success | failure", r.get("error", ""))

    def test_offered_in_plan_modes_only_and_never_to_workers(self):
        from contract import MODES
        self.assertIn("story_plan", MODES["plan"]["tools"])
        self.assertIn("story_plan", MODES["observe"]["tools"])
        self.assertNotIn("story_plan", MODES["build"]["tools"])
        loop, _ = self._loop()
        loop.state.snapshot["worker_id"] = "w-1"
        r = loop._resolve_tool_fn("story_plan")(**self.ARGS)
        self.assertIn("refused", r.get("error", ""))


class StretchGoalToolTest(StoryTestBase):
    """T17: the harness pitches a stretch goal in NABC form; it is parked
    with a revisit date and never built unasked."""

    ARGS = {
        "title": "Deep health checks",
        "need": "A liveness check says nothing about the database.",
        "approach": "Probe each dependency with a latency budget.",
        "benefits": "Load balancers drop sick instances before users notice.",
        "competition": "Vendor APM agents: heavier, paid, and opaque.",
        "steps": ("Probe the database | returns within 50 ms | timeout\n"
                  "Probe the cache | returns within 10 ms | timeout\n"
                  "Aggregate status | one JSON verdict | partial output"),
    }

    def _loop(self):
        from registry import Registry
        loop, home = make_loop()
        awd = Path(home) / ".awino"
        Registry(awd).ensure()
        loop.registry = Registry(awd)
        loop.set_mission("Health endpoint", ["manual"])
        return loop, awd

    def test_pitch_is_parked_with_revisit_date(self):
        loop, awd = self._loop()
        r = loop._resolve_tool_fn("stretch_goal")(**self.ARGS)
        self.assertTrue(r.get("ok"), r)
        st = StoryStore(awd).get(r["story_id"])
        self.assertEqual((st["status"], st["type"]), ("parked", "spike"))
        self.assertTrue(st["revisit_on"])
        for part in ("**Need.**", "**Approach.**", "**Benefits.**",
                     "**Competition.**"):
            self.assertIn(part, st["approach"])
        self.assertEqual(len(st["steps"]), 3)
        self.assertIn("Deep health checks",
                      (awd.parent / "STORY.md").read_text())
        self.assertTrue([e for e in loop.state.events
                         if e["type"] == "stretch_goal_pitched"])

    def test_every_nabc_part_and_3_to_5_steps_required(self):
        loop, _ = self._loop()
        fn = loop._resolve_tool_fn("stretch_goal")
        r = fn(**dict(self.ARGS, competition=""))
        self.assertIn("competition", r.get("error", ""))
        r = fn(**dict(self.ARGS, steps="Only one | ok | bad"))
        self.assertIn("3 to 5 steps", r.get("error", ""))
        r = fn(**dict(self.ARGS, steps="a | b\nc | d | e\nf | g | h"))
        self.assertIn("title | success | failure", r.get("error", ""))

    def test_custom_revisit_date_and_offered_modes(self):
        from contract import MODES
        loop, awd = self._loop()
        r = loop._resolve_tool_fn("stretch_goal")(
            **dict(self.ARGS, revisit_on="2027-01-15"))
        self.assertEqual(r["revisit_on"], "2027-01-15")
        for mode in ("observe", "plan", "ship"):
            self.assertIn("stretch_goal", MODES[mode]["tools"])
        for mode in ("build", "verify"):
            self.assertNotIn("stretch_goal", MODES[mode]["tools"])
