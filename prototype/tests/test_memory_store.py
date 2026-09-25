"""Durable memory store (MemPalace cherry-pick): store/recall/search with
chunking + dedup, local-first JSONL, stdlib only. Honda scope: no
compression, no background daemons, no vector search, no server."""
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memory_store import MemoryStore, CHUNK_CHARS


def fresh_store():
    tmp = Path(tempfile.mkdtemp(prefix="awino-mem-"))
    awino = tmp / ".awino"
    return MemoryStore(awino), tmp


class TestDurableMemory(unittest.TestCase):
    def test_store_recall_roundtrip(self):
        ms, _ = fresh_store()
        res = ms.store("Decision: use SQLite for the MVP.", key="arch-1")
        self.assertFalse(res["deduped"])
        self.assertEqual(res["chunks"], 1)
        self.assertEqual(ms.recall(res["id"]), "Decision: use SQLite for the MVP.")
        # recall by key works too
        self.assertEqual(ms.recall("arch-1"), "Decision: use SQLite for the MVP.")

    def test_memory_file_is_jsonl_under_awino_dir(self):
        ms, tmp = fresh_store()
        ms.store("hello", key="k1")
        p = tmp / ".awino" / "memory.jsonl"
        self.assertTrue(p.is_file())
        lines = p.read_text().strip().split("\n")
        for line in lines:
            json.loads(line)  # every line parses as JSON

    def test_chunking_long_entry_reassembles(self):
        ms, tmp = fresh_store()
        long_text = ("Chunking matters because long decisions must survive "
                     "session restarts intact. " * 60)  # >> CHUNK_CHARS
        self.assertGreater(len(long_text), CHUNK_CHARS)
        res = ms.store(long_text, key="long-1")
        self.assertGreater(res["chunks"], 1)
        # each chunk recorded as its own JSONL line
        lines = (tmp / ".awino" / "memory.jsonl").read_text().strip().split("\n")
        self.assertEqual(len(lines), res["chunks"])
        # recall reassembles the full entry byte-identically
        self.assertEqual(ms.recall(res["id"]), long_text)

    def test_dedup_identical_content_stored_once(self):
        ms, tmp = fresh_store()
        content = "Learning: verify before claiming; evidence beats prose."
        first = ms.store(content, key="learn-1")
        second = ms.store(content, key="learn-1")
        self.assertFalse(first["deduped"])
        self.assertTrue(second["deduped"])
        self.assertEqual(first["id"], second["id"])
        # one record in the file, not two
        p = tmp / ".awino" / "memory.jsonl"
        self.assertEqual(len(p.read_text().strip().split("\n")), 1)
        # same content under a different key is still deduped (content hash)
        third = ms.store(content, key="other-key")
        self.assertTrue(third["deduped"])
        self.assertEqual(len(p.read_text().strip().split("\n")), 1)

    def test_dedup_survives_reload(self):
        ms, tmp = fresh_store()
        content = "Persistent dedup across sessions."
        first = ms.store(content, key="p1")
        ms2 = MemoryStore(tmp / ".awino")
        second = ms2.store(content, key="p1")
        self.assertTrue(second["deduped"])
        self.assertEqual(first["id"], second["id"])

    def test_recall_unknown_raises(self):
        ms, _ = fresh_store()
        with self.assertRaises(KeyError):
            ms.recall("no-such-id")

    def test_search_finds_terms_across_chunks(self):
        ms, _ = fresh_store()
        # term planted only in a late chunk
        body = ("Filler sentence about architecture. " * 200
                + "The unicorn clause was approved by the board.")
        self.assertGreater(len(body), CHUNK_CHARS * 2)
        rec = ms.store(body, key="board-minutes")
        hits = ms.search("unicorn clause")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], rec["id"])
        # full content recoverable from the hit
        self.assertIn("unicorn clause", ms.recall(hits[0]["id"]))

    def test_search_ranks_more_matches_higher(self):
        ms, _ = fresh_store()
        a = ms.store("deployment rollback deployment", key="a")
        b = ms.store("deployment", key="b")
        hits = ms.search("deployment rollback")
        ids = [h["id"] for h in hits]
        self.assertEqual(ids[0], a["id"])
        self.assertEqual(ids[1], b["id"])

    def test_search_no_match_returns_empty(self):
        ms, _ = fresh_store()
        ms.store("something about databases", key="d")
        self.assertEqual(ms.search("xylophone"), [])

    def test_all_operations_are_local_no_network(self):
        # The store must work with networking entirely disabled.
        real_socket = socket.socket

        def no_net(*args, **kwargs):
            raise AssertionError("network access attempted")

        socket.socket = no_net
        try:
            ms, tmp = fresh_store()
            rec = ms.store("Local-first indexing works offline.", key="net")
            self.assertEqual(ms.recall(rec["id"]),
                             "Local-first indexing works offline.")
            hits = ms.search("local-first")
            self.assertEqual(len(hits), 1)
            MemoryStore(tmp / ".awino")  # reload reads from disk, not network
        finally:
            socket.socket = real_socket

    def test_manifest_pin_verifies(self):
        from skills import SkillStore
        store = SkillStore.default()
        self.assertIn("durable-memory", store.names())
        body = store.get_verified("durable-memory")
        self.assertTrue(body.strip())
        self.assertIn("PROCEDURE durable-memory", body)


class TestDurableMemoryRouting(unittest.TestCase):
    def test_new_task_routes_durable_memory_skill(self):
        from stances import route_triple
        snap = {"mission": None, "phase": "DEFINE"}
        _, _, _, skills, _ = route_triple(snap, "Rebuild the sync engine",
                                          "new_objective")
        self.assertIn("durable-memory", skills)

    def test_define_floor_routes_durable_memory_skill(self):
        from stances import route_triple
        snap = {"mission": {"id": "m-1"}, "phase": "DEFINE"}
        _, _, _, skills, trigger = route_triple(snap, "go", "info")
        self.assertIn("floor default", trigger)
        self.assertIn("durable-memory", skills)

    def test_skill_counts_stay_green(self):
        from skills import SkillStore
        store = SkillStore.default()
        # 52: 49 base + durable-memory + debug + rpi (see
        # test_rigor.py::TestRigorSkills::test_pins_load, the canonical
        # count assertion — this one guards the routing surface only)
        self.assertEqual(len(store.names()), 52)
        self.assertIn("durable-memory", store.names())


if __name__ == "__main__":
    unittest.main()
