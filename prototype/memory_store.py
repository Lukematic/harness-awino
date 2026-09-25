"""Durable memory store: local-first JSONL persistence for the loop.

Cherry-picked from the MemPalace head-to-head: local-first indexing,
chunking, and content-hash deduplication. Deliberately NOT built:
compression (MemPalace's 30x claim measured 2.7x with fidelity loss),
background indexing daemons, vector search, or a server.

Storage layout: ``<awino_dir>/memory.jsonl`` — one JSON object per chunk
line, append-only. A record is identified by its content hash; storing
identical content twice appends nothing.

Line shape::
    {"id": "m-<hash12>", "key": "<optional key>", "chunk": 0, "of": 3,
     "hash": "<sha256 of full content>", "text": "<chunk text>",
     "created": "<iso timestamp>"}

stdlib only: json, hashlib, re, datetime.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

CHUNK_CHARS = 2000  # max characters per chunk
MEMORY_FILE = "memory.jsonl"

_TERM_RE = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> set[str]:
    """Local-first tokenization: lowercase alphanumerics. No network,
    no embeddings — a plain inverted index over chunks."""
    return set(_TERM_RE.findall(text.lower()))


class MemoryStore:
    """Append-only durable memory. Chunking keeps long entries whole;
    dedup keeps repeated stores cheap; search stays on this machine."""

    def __init__(self, awino_dir: str | Path):
        self.awino_dir = Path(awino_dir)
        self.path = self.awino_dir / MEMORY_FILE
        # hash -> {"id", "key", "chunks": [(idx, text)], "terms": set}
        self._by_hash: dict[str, dict] = {}
        # id -> hash, key -> hash
        self._by_id: dict[str, str] = {}
        self._by_key: dict[str, str] = {}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # never let a bad line break recall
                h = row["hash"]
                rec = self._by_hash.get(h)
                if rec is None:
                    rec = {"id": row["id"], "key": row.get("key"),
                           "chunks": [], "terms": set()}
                    self._by_hash[h] = rec
                    self._by_id[row["id"]] = h
                    if row.get("key"):
                        self._by_key[row["key"]] = h
                rec["chunks"].append((row["chunk"], row["text"]))
                rec["terms"] |= _terms(row["text"])
        for rec in self._by_hash.values():
            rec["chunks"].sort(key=lambda c: c[0])

    def _append(self, row: dict) -> None:
        self.awino_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # -- chunking ----------------------------------------------------------
    @staticmethod
    def chunk(text: str, size: int = CHUNK_CHARS) -> list[str]:
        """Split text into <=size chunks at whitespace boundaries so terms
        are never cut in half. Lossless: chunks are contiguous slices, so
        joining them with "" reproduces the original byte-identically."""
        if not text:
            return [""]
        chunks: list[str] = []
        start = 0
        while len(text) - start > size:
            last_ws = None
            for m in re.finditer(r"\s", text[start:start + size]):
                last_ws = m
            cut = start + last_ws.end() if last_ws else start + size
            chunks.append(text[start:cut])
            start = cut
        chunks.append(text[start:])
        return chunks

    # -- operations ---------------------------------------------------------
    def store(self, content: str, key: str | None = None) -> dict:
        """Persist an entry. Returns {"id", "key", "hash", "chunks",
        "deduped"}; identical content is never stored twice."""
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if h in self._by_hash:  # dedup by content hash
            rec = self._by_hash[h]
            return {"id": rec["id"], "key": rec["key"], "hash": h,
                    "chunks": len(rec["chunks"]), "deduped": True}
        rid = f"m-{h[:12]}"
        texts = self.chunk(content)
        created = datetime.now(timezone.utc).isoformat()
        for i, text in enumerate(texts):
            self._append({"id": rid, "key": key, "chunk": i, "of": len(texts),
                          "hash": h, "text": text, "created": created})
        rec = {"id": rid, "key": key,
               "chunks": list(enumerate(texts)),
               "terms": _terms(content)}
        self._by_hash[h] = rec
        self._by_id[rid] = h
        if key:
            self._by_key[key] = h
        return {"id": rid, "key": key, "hash": h,
                "chunks": len(texts), "deduped": False}

    def recall(self, ref: str) -> str:
        """Reassemble and return the full entry by record id or key."""
        h = self._by_id.get(ref) or self._by_key.get(ref)
        if h is None:
            raise KeyError(f"no memory record for {ref!r}")
        return "".join(t for _, t in self._by_hash[h]["chunks"])

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Local keyword search over chunks: records matching the most
        distinct query terms rank first. Never touches the network."""
        qterms = _terms(query)
        if not qterms:
            return []
        scored = []
        for h, rec in self._by_hash.items():
            hits = qterms & rec["terms"]
            if hits:
                scored.append((len(hits), h))
        scored.sort(key=lambda s: (-s[0], s[1]))
        out = []
        for score, h in scored[:top_k]:
            rec = self._by_hash[h]
            snippet = rec["chunks"][0][1][:160]
            out.append({"id": rec["id"], "key": rec["key"],
                        "score": score, "chunks": len(rec["chunks"]),
                        "snippet": snippet})
        return out

    def record_count(self) -> int:
        return len(self._by_hash)
