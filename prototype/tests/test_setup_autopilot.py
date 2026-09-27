"""Setup autopilot (team review 09-27): deterministic chores proposed per
language, applied only on consent, never overwriting, remembered."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import setup_autopilot as A  # noqa: E402

SIDECAR = os.path.join(os.path.dirname(__file__), "..", "awino_sidecar.py")


def tmp():
    return Path(tempfile.mkdtemp(prefix="awino-setup-"))


class DetectAndPlanTest(unittest.TestCase):
    def test_python_with_pytest(self):
        r = tmp()
        (r / "pyproject.toml").write_text('[project]\ndependencies=["pytest"]\n')
        text = A.justfile_text(r)
        self.assertIn("test:\n    python -m pytest -q", text)
        self.assertIn("lint:\n    ruff check .", text)

    def test_python_without_pytest_uses_unittest(self):
        r = tmp()
        (r / "app.py").write_text("x = 1\n")
        self.assertIn("python -m unittest discover", A.justfile_text(r))

    def test_node_uses_its_own_scripts(self):
        r = tmp()
        (r / "package.json").write_text(json.dumps(
            {"scripts": {"test": "vitest"}, "devDependencies": {"eslint": "9"}}))
        text = A.justfile_text(r)
        self.assertIn("test:\n    npm test", text)
        self.assertIn("lint:\n    npx eslint .", text)
        self.assertNotIn("pytest", text)
        self.assertNotIn("ruff", text)

    def test_nothing_detected_proposes_no_justfile(self):
        self.assertIsNone(A.justfile_text(tmp()))
        self.assertEqual([a["id"] for a in A.plan(tmp())], ["gitignore"])

    def test_existing_makefile_is_respected(self):
        r = tmp()
        (r / "app.py").write_text("")
        (r / "Makefile").write_text("test:\n\ttrue\n")
        self.assertNotIn("justfile", [a["id"] for a in A.plan(r)])

    def test_env_example_has_keys_not_values(self):
        r = tmp()
        (r / ".env").write_text("API_KEY=sk-secret\nexport DB_URL=postgres://x\n")
        a = next(a for a in A.plan(r) if a["id"] == "env_example")
        self.assertEqual(a["content"], "API_KEY=\nDB_URL=\n")
        self.assertNotIn("sk-secret", json.dumps(A.plan(r)))


class ApplyTest(unittest.TestCase):
    def test_apply_only_what_was_chosen_and_never_overwrite(self):
        r = tmp()
        (r / "app.py").write_text("")
        (r / ".gitignore").write_text("dist/")
        res = A.apply(r, ["gitignore", "justfile"])
        self.assertEqual(sorted(res["applied"]), [".gitignore", "justfile"])
        gi = (r / ".gitignore").read_text()
        self.assertTrue(gi.startswith("dist/\n.env\n"))
        self.assertIn(".venv/", gi)
        self.assertFalse((r / ".editorconfig").exists())
        # applied actions are not proposed again
        self.assertEqual([a["id"] for a in A.plan(r)], ["editorconfig"])

    def test_never_is_remembered_skip_is_not(self):
        r = tmp()
        (r / "app.py").write_text("")
        A.decline(r, ["editorconfig"], never=True)
        A.decline(r, ["justfile"])
        ids = [a["id"] for a in A.plan(r)]
        self.assertNotIn("editorconfig", ids)
        self.assertIn("justfile", ids)


class SidecarConsentTest(unittest.TestCase):
    def _run(self, ws, cmds):
        env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(ws.parent))
        env.pop("AWINO_HOME", None)
        msgs = [{"cmd": "hello", "workspace": str(ws), "provider": "echo"}]
        msgs += [{"cmd": "command", "name": n, "args": a, "id": str(i)}
                 for i, (n, a) in enumerate(cmds)]
        msgs.append({"cmd": "bye"})
        out = subprocess.run([sys.executable, SIDECAR],
                             input="\n".join(json.dumps(m) for m in msgs) + "\n",
                             capture_output=True, text=True, env=env, timeout=60)
        return [json.loads(l) for l in out.stdout.splitlines() if l.strip()]

    def test_session_start_writes_nothing_outside_awino(self):
        ws = tmp() / "proj"
        ws.mkdir()
        (ws / "app.py").write_text("x = 1\n")
        evs = self._run(ws, [])
        import time
        time.sleep(0.5)  # auto-init runs in a background thread
        extra = sorted(p.name for p in ws.iterdir()
                       if p.name not in ("app.py", ".awino"))
        self.assertEqual(extra, [])
        ready = next(e for e in evs if e.get("event") == "ready")
        self.assertIn("justfile", " ".join(ready.get("notices") or []))

    def test_apply_via_sidecar(self):
        ws = tmp() / "proj"
        ws.mkdir()
        (ws / "app.py").write_text("x = 1\n")
        evs = self._run(ws, [("setup_plan", {}),
                             ("setup_apply", {"ids": ["justfile"]})])
        res = [e for e in evs if e.get("event") == "command_result"]
        plan = res[0].get("result", res[0])
        self.assertIn("justfile", [a["id"] for a in plan["actions"]])
        self.assertTrue((ws / "justfile").is_file())
        self.assertFalse((ws / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()


class BootstrapConsentTest(unittest.TestCase):
    def test_extension_auto_init_writes_only_awino(self):
        from unittest import mock
        import bootstrap
        r = tmp()
        (r / "app.py").write_text("x = 1\n")
        with mock.patch.dict(os.environ, {"AWINO_SIDECAR": "1"}):
            bootstrap.full_init_flow(r)
        self.assertEqual(sorted(p.name for p in r.iterdir()),
                         [".awino", "app.py"])
