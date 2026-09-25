"""Journal hash chaining: tamper-evident event log.

Spec (v06-loop-design.md): ProjectState.record() computes prev_hash =
sha256 of the canonical JSON of the previous event (genesis: "GENESIS")
and ev["hash"] = sha256 of the canonical JSON of the event including
prev_hash. verify_chain() walks events.jsonl and returns (ok,
first_bad_seq). Torn-tail repair keeps the chain verifiable from the last
good event. The snapshot carries journal_tip_hash.
"""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from state import ProjectState, _canonical_bytes, GENESIS_PREV_HASH


def fresh_state(project="p1"):
    home = tempfile.mkdtemp(prefix="awino-chain-test-")
    return ProjectState(home, project), home


class TestChainBasics(unittest.TestCase):
    def tearDown(self):
        pass  # temp dirs are cheap; the suite uses TMPDIR

    def test_first_event_uses_genesis(self):
        st, _ = fresh_state()
        ev = st.record("mission_defined", {"text": "x"})
        self.assertEqual(ev["prev_hash"], GENESIS_PREV_HASH)
        self.assertIn("hash", ev)

    def test_chain_links_across_appends(self):
        st, _ = fresh_state()
        e0 = st.record("a", {"n": 0})
        e1 = st.record("b", {"n": 1})
        e2 = st.record("c", {"n": 2})
        # Each prev_hash commits to the canonical bytes of the previous
        # event (including that event's own hash).
        self.assertEqual(
            e1["prev_hash"],
            hashlib.sha256(_canonical_bytes(e0)).hexdigest())
        self.assertEqual(
            e2["prev_hash"],
            hashlib.sha256(_canonical_bytes(e1)).hexdigest())
        # Each hash is the digest of the event including prev_hash.
        for ev in (e0, e1, e2):
            body = {k: v for k, v in ev.items() if k != "hash"}
            self.assertEqual(
                ev["hash"],
                hashlib.sha256(_canonical_bytes(body)).hexdigest())

    def test_verify_clean_chain(self):
        st, _ = fresh_state()
        for i in range(5):
            st.record("test_event", {"i": i})
        ok, bad = st.verify_chain()
        self.assertTrue(ok)
        self.assertIsNone(bad)

    def test_verify_survives_reload(self):
        st, home = fresh_state()
        for i in range(3):
            st.record("test_event", {"i": i})
        reloaded = ProjectState(home, "p1")
        ok, bad = reloaded.verify_chain()
        self.assertTrue(ok)
        self.assertIsNone(bad)

    def test_empty_journal_verifies(self):
        st, _ = fresh_state()
        ok, bad = st.verify_chain()
        self.assertTrue(ok)
        self.assertIsNone(bad)


class TestTamperDetection(unittest.TestCase):
    def _tamper_file(self, st, line_no, mutate):
        lines = st.events_path.read_bytes().split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        ev = json.loads(lines[line_no])
        mutate(ev)
        lines[line_no] = json.dumps(ev).encode()
        st.events_path.write_bytes(b"\n".join(lines) + b"\n")

    def test_modified_payload_breaks_verification(self):
        st, _ = fresh_state()
        st.record("a", {"x": 1})
        st.record("b", {"x": 2})
        st.record("c", {"x": 3})
        self._tamper_file(st, 1, lambda ev: ev["data"].update({"x": 999}))
        ok, bad = st.verify_chain()
        self.assertFalse(ok)
        self.assertEqual(bad, 1)

    def test_reordered_events_break_verification(self):
        st, _ = fresh_state()
        st.record("a", {"x": 1})
        st.record("b", {"x": 2})
        lines = st.events_path.read_bytes().split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        lines[0], lines[1] = lines[1], lines[0]
        st.events_path.write_bytes(b"\n".join(lines) + b"\n")
        ok, bad = st.verify_chain()
        self.assertFalse(ok)
        # The event now first in the file is not the genesis event.
        self.assertEqual(bad, 1)

    def test_truncated_middle_breaks_verification(self):
        st, _ = fresh_state()
        st.record("a", {"x": 1})
        st.record("b", {"x": 2})
        st.record("c", {"x": 3})
        lines = st.events_path.read_bytes().split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        del lines[1]  # remove the middle event
        st.events_path.write_bytes(b"\n".join(lines) + b"\n")
        ok, bad = st.verify_chain()
        self.assertFalse(ok)
        # Event "c" (seq 2) now follows "a": its prev_hash no longer matches.
        self.assertEqual(bad, 2)

    def test_legacy_prefix_still_links(self):
        st, home = fresh_state()
        # Simulate a pre-chain journal: hash-less events on disk.
        legacy = [
            {"seq": 0, "id": "l0", "ts": 1.0, "type": "a", "data": {}},
            {"seq": 1, "id": "l1", "ts": 2.0, "type": "b", "data": {}},
        ]
        st.events_path.write_bytes(
            b"\n".join(json.dumps(e).encode() for e in legacy) + b"\n")
        st2 = ProjectState(home, "p1")
        st2.record("c", {"x": 3})
        ok, bad = st2.verify_chain()
        self.assertTrue(ok)
        self.assertIsNone(bad)
        # Tampering with the immediate pre-chain predecessor breaks the
        # first hashed link (it commits to that event's exact bytes).
        # Earlier legacy history is NOT covered — documented limitation.
        lines = st2.events_path.read_bytes().split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        ev = json.loads(lines[1])
        ev["data"]["evil"] = True
        lines[1] = json.dumps(ev).encode()
        st2.events_path.write_bytes(b"\n".join(lines) + b"\n")
        ok, bad = st2.verify_chain()
        self.assertFalse(ok)
        self.assertEqual(bad, 2)


class TestTornTail(unittest.TestCase):
    def test_torn_tail_repair_keeps_chain_verifiable(self):
        st, home = fresh_state()
        st.record("a", {"x": 1})
        st.record("b", {"x": 2})
        # Simulate a torn final write from a crash.
        with open(st.events_path, "ab") as f:
            f.write(b'{"seq": 99, "id": "abc", "ts": 1.0, "type": "user_mess')
        reloaded = ProjectState(home, "p1")
        self.assertTrue(reloaded.repaired_tail)
        # The chain verifies from the last good event.
        ok, bad = reloaded.verify_chain()
        self.assertTrue(ok)
        self.assertIsNone(bad)
        # And the log is still appendable with an intact chain.
        reloaded.record("c", {"x": 3})
        ok, bad = reloaded.verify_chain()
        self.assertTrue(ok)
        self.assertIsNone(bad)


class TestSnapshotTip(unittest.TestCase):
    def test_snapshot_carries_tip_hash(self):
        st, _ = fresh_state()
        st.record("a", {"x": 1})
        st.record("b", {"x": 2})
        st.persist_snapshot()
        snap = json.loads(st.snapshot_path.read_text())
        self.assertEqual(snap["journal_tip_hash"], st.events[-1]["hash"])

    def test_empty_snapshot_tip_is_none(self):
        st, _ = fresh_state()
        st.persist_snapshot()
        snap = json.loads(st.snapshot_path.read_text())
        self.assertIsNone(snap["journal_tip_hash"])


if __name__ == "__main__":
    unittest.main()
