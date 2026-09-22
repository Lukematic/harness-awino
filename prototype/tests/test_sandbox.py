"""Phase B: sandbox manifest — write tracking and tamper detection."""
import unittest

from tools import Sandbox
from tests.common import make_loop, T
from backends import ScriptedBackend


class TestSandboxManifest(unittest.TestCase):
    def test_manifest_tracks_writes(self):
        import tempfile
        sb = Sandbox(tempfile.mkdtemp(prefix="awino-sb-"))
        sb.write_file("a.txt", "hello")
        sb.write_file("sub/b.txt", "world")
        m = sb.manifest()
        self.assertEqual(set(m), {"a.txt", "sub/b.txt"})
        # hashes are full sha256
        import hashlib
        self.assertEqual(m["a.txt"], hashlib.sha256(b"hello").hexdigest())

    def test_verify_manifest_ok(self):
        import tempfile
        sb = Sandbox(tempfile.mkdtemp(prefix="awino-sb-"))
        sb.write_file("a.txt", "hello")
        ok, problems = sb.verify_manifest()
        self.assertTrue(ok, problems)

    def test_verify_manifest_detects_tampering(self):
        import tempfile
        from pathlib import Path
        root = tempfile.mkdtemp(prefix="awino-sb-")
        sb = Sandbox(root)
        sb.write_file("a.txt", "hello")
        # external modification (outside the sandbox API)
        Path(root, "a.txt").write_text("tampered")
        ok, problems = sb.verify_manifest()
        self.assertFalse(ok)
        self.assertTrue(any("a.txt" in p and "mismatch" in p for p in problems))

    def test_verify_manifest_detects_deletion(self):
        import tempfile
        from pathlib import Path
        root = tempfile.mkdtemp(prefix="awino-sb-")
        sb = Sandbox(root)
        sb.write_file("a.txt", "hello")
        Path(root, "a.txt").unlink()
        ok, problems = sb.verify_manifest()
        self.assertFalse(ok)
        self.assertTrue(any("missing" in p for p in problems))

    def test_traversal_refused(self):
        import tempfile
        sb = Sandbox(tempfile.mkdtemp(prefix="awino-sb-"))
        with self.assertRaises(ValueError):
            sb.write_file("../escape.txt", "x")
        with self.assertRaises(ValueError):
            sb.read_file("../../etc/passwd")

    def test_journal_digest_matches_manifest(self):
        """The effect journal's digest for a write matches the sandbox manifest."""
        import hashlib
        backend = ScriptedBackend([
            T(tool_calls=[{"name": "write_file",
                           "args": {"path": "out.txt", "content": "data"}}],
              progress_delta="wrote",
              assumptions=["Cause: the test needs a file written."]),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Write", ["manual"])
        loop.state.record("plan_updated", {"plan": ["Write out.txt"]})
        loop.approve_contract()
        loop.approve_contract(["out.txt"])
        # approve the consequential call
        r = loop.run_user_turn("write it")
        if r["status"] == "awaiting_approval":
            loop.approve(r["approvals"][0])
        j = loop.effect_journal()
        writes = [e for e in j if e["tool"] == "write_file"]
        self.assertTrue(writes)
        manifest = loop.sandbox.manifest()
        self.assertIn("out.txt", manifest)
        # journal digest (16-char prefix) matches manifest hash prefix
        self.assertTrue(manifest["out.txt"].startswith(writes[0]["digest"]))


if __name__ == "__main__":
    unittest.main()
