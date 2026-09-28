"""Model tiers (user request 09-28): best plans and reviews, medium
builds, basic runs single worker steps; a tier that keeps failing
verification hands the work up. Optional: no tiers = one model."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from common import make_loop  # noqa: E402

SIDECAR = os.path.join(os.path.dirname(__file__), "..", "awino_sidecar.py")


class _B:
    def __init__(self, model):
        self.model = model


class TierSelectionTest(unittest.TestCase):
    def _loop(self):
        loop, _ = make_loop(project="tiers")
        loop.tier_backends = {t: _B(t + "-model")
                              for t in ("best", "medium", "basic")}
        loop.set_mission("x", ["manual"])
        return loop

    def _phase(self, loop, phase):
        loop.state.record("phase_changed", {"phase": phase, "reason": "t"})

    def test_phase_picks_the_tier(self):
        loop = self._loop()
        for phase, model in (("PLAN", "best-model"), ("BUILD", "medium-model"),
                             ("VERIFY", "medium-model"), ("REVIEW", "best-model")):
            self._phase(loop, phase)
            loop._apply_tier("t1")
            self.assertEqual(loop.backend.model, model, phase)
        tiers = [e["data"]["tier"] for e in loop.state.events
                 if e["type"] == "model_tier"]
        self.assertEqual(tiers, ["best", "medium", "best"])  # only on change

    def test_repeated_failures_escalate_build_to_best(self):
        loop = self._loop()
        self._phase(loop, "BUILD")
        for _ in range(2):
            loop.state.record("verify_failed", {"source": "tests",
                                                "reason": "x", "verdict": []})
        loop._apply_tier("t1")
        self.assertEqual(loop.backend.model, "best-model")
        loop.state.record("verify_passed", {"worker_id": "w", "verdict": "pass"})
        loop._apply_tier("t2")
        self.assertEqual(loop.backend.model, "medium-model")

    def test_no_tiers_means_one_model(self):
        loop, _ = make_loop(project="one")
        before = loop.backend
        loop._apply_tier("t1")
        self.assertIs(loop.backend, before)
        self.assertFalse(any(e["type"] == "model_tier" for e in loop.state.events))


class SidecarTiersTest(unittest.TestCase):
    def test_hello_builds_tier_backends(self):
        ws = tempfile.mkdtemp(prefix="awino-tiers-")
        env = dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(ws))
        msgs = [{"cmd": "hello", "workspace": ws, "provider": "openai",
                 "endpoint": "http://127.0.0.1:9/v1", "model": "main-m",
                 "model_tiers": {"best": "big-m", "medium": "",
                                 "basic": "small-m"}},
                {"cmd": "command", "name": "status", "args": {}, "id": "1"},
                {"cmd": "bye"}]
        out = subprocess.run([sys.executable, "-c", f"""
import sys, json
sys.path.insert(0, {os.path.dirname(SIDECAR)!r})
import awino_sidecar as S
sc = S.Sidecar()
S._emit = lambda o: None
sc._dispatch(json.loads({json.dumps(msgs[0])!r}))
t = sc.loop.tier_backends
print(json.dumps({{k: getattr(v, 'model', None) for k, v in t.items()}}))
"""], capture_output=True, text=True, env=env, timeout=60)
        models = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(models, {"best": "big-m", "medium": "main-m",
                                  "basic": "small-m"}, out.stderr[-600:])


if __name__ == "__main__":
    unittest.main()
