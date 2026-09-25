PROCEDURE durable-memory:
1. RECALL FIRST, before planning. When a DEFINE turn opens (or any turn that
   drafts a contract, plan, or decision), search durable memory for prior
   decisions, learnings, and outcomes on the same topic BEFORE asking the
   user or proposing anything new. Memory is cheaper than a second interview.
2. PERSIST AT CLOSURE. When a mission ships (or a turn produces a durable
   decision, learning, or falsified assumption), store it: one entry per
   decision, in the user's words, with enough context that a future session
   can act on it without re-asking.
3. SEARCH, don't scroll. Memory is chunked; use `search` with the topic's
   terms (not the whole question) and `recall` the record id of a hit.
   Never read the raw JSONL by hand for retrieval.
4. NEVER STORE: credentials, tokens, keys, PII beyond what's needed for the
   mission, or anything the user said was private to this session. Memory is
   a file cabinet in `.awino/`, not a vault.
5. Dedup is automatic: storing identical content twice returns the same
   record; don't pre-check with search to "avoid duplicates".

HOW (mechanism, not metaphor): the harness's `prototype/memory_store.py`
(`MemoryStore`, stdlib only — json, hashlib, re). Append-only JSONL at
`.awino/memory.jsonl`; long entries are chunked (word-boundary,
byte-identical reassembly); records are deduplicated by SHA-256 content
hash; search is a local inverted index over chunks (no network, no
embeddings, no server).

- `store(content, key=None)` → {"id", "key", "hash", "chunks", "deduped"}
- `recall(id_or_key)` → full entry, chunks reassembled in order
- `search(query, top_k=5)` → ranked records matching the most query terms

The three MemPalace ideas worth keeping are exactly these: local-first
indexing (works offline, nothing to leak), chunking (long entries survive),
dedup (repeat stores are free). Compression was measured at 2.7x with
fidelity loss, not 30x — it is deliberately NOT built.

VERIFICATION STEP: after storing, `recall` the returned id and confirm the
text matches what you meant to persist; after searching, open at least one
hit before citing it in a plan.

Routing: DEFINE floor skill + new-task path (recall before planning); SHIP
floor hands (persist decisions/learnings at closure).
