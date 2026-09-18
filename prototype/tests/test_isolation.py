"""Isolation: projects are separate state dirs; missions persist across
a simulated restart."""
import unittest

from tests.common import make_loop, T
from backends import ScriptedBackend, ScriptedJudge
from loop import Loop


class TestIsolation(unittest.TestCase):
    def test_two_projects_isolated(self):
        backend_a = ScriptedBackend([T(progress_delta="A working.")])
        backend_b = ScriptedBackend([T(objective="Research sync",
                                      progress_delta="B researching.")])
        la, home = make_loop(project="A", backend=backend_a)
        la.set_mission("Mission A: fix bug", ["manual"])
        la.run_user_turn("go a")

        lb, _ = make_loop(project="B", backend=backend_b, home=home)
        lb.set_mission("Mission B: research sync", ["manual"])
        lb.run_user_turn("go b")

        self.assertEqual(la.state.snapshot["mission"]["text"], "Mission A: fix bug")
        self.assertEqual(lb.state.snapshot["mission"]["text"], "Mission B: research sync")
        # event logs are separate files with no cross-contamination
        a_text = (la.state.events_path).read_text()
        b_text = (lb.state.events_path).read_text()
        self.assertIn("Mission A", a_text)
        self.assertNotIn("Mission B", a_text)
        self.assertIn("Mission B", b_text)
        self.assertNotIn("Mission A", b_text)
        # sandboxes are separate
        self.assertNotEqual(la.sandbox.root, lb.sandbox.root)

    def test_mission_persists_across_restart(self):
        backend = ScriptedBackend([
            T(plan=["Step one"], progress_delta="Planned."),
            T(plan=["Step one"], progress_delta="Still planning.",
              assumptions=["Cause: the empty-password path rejects valid logins."]),
        ])
        loop, home = make_loop(project="persist", backend=backend)
        loop.set_mission("Mission P: fix bug", ["manual"])
        loop.run_user_turn("go")
        loop.approve_contract()
        loop.run_user_turn("continue")

        # simulated restart: brand-new process, same home+project
        loop2 = Loop(home, "persist", ScriptedBackend([]), ScriptedJudge())
        s = loop2.state.snapshot
        self.assertEqual(s["mission"]["text"], "Mission P: fix bug")
        self.assertEqual(s["turn_count"], 2)
        self.assertEqual(s["plan"], ["Step one"])
        self.assertEqual(s["phase"], "PLAN")
        self.assertEqual(len(s["progress"]), 2)
        # and it can keep working
        loop2.backend = ScriptedBackend([
            T(plan=["Step one"], progress_delta="Resumed.",
              assumptions=["Cause: the empty-password path rejects valid logins."]),
        ])
        r = loop2.run_user_turn("keep going")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(loop2.state.snapshot["turn_count"], 3)


if __name__ == "__main__":
    unittest.main()
