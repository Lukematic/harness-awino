"""Codebase indexer: symbol extraction, SQLite storage, change detection.

Honda first: no embeddings, no vector search, no cloud. The `chunks` table
carries a nullable `embedding` column so semantic search can be bolted on
later without a schema migration.

Extraction:
  - Python: the stdlib `ast` module (accurate — real parse, not regex).
  - TypeScript / JavaScript: regex heuristics (no tree-sitter in the
    bundled runtime; regex misses some exotic syntax — see gaps).
  - Everything else: indexed by path only (no symbols), so related_files
    still sees the file.

Change detection (Cursor's Merkle-tree idea, simplified): each file's
sha256 is stored in the DB. A build walks the workspace and re-extracts
only files whose hash changed (mtime+size is a fast-path skip); files
gone from disk have their rows deleted.

The index lives at <workspace>/.awino/index.db. It is written ONLY by
this module (build / rebuild_file / remove_file). Query methods are
read-only — the sidecar's `index_query` tool calls them and can never
mutate the index.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY,
  mtime REAL NOT NULL,
  size INTEGER NOT NULL,
  hash TEXT NOT NULL,
  language TEXT NOT NULL,
  indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS symbols(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  file TEXT NOT NULL,
  line INTEGER NOT NULL,
  signature TEXT NOT NULL,
  snippet TEXT NOT NULL,
  parent TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE TABLE IF NOT EXISTS refs(
  id INTEGER PRIMARY KEY,
  symbol TEXT NOT NULL,
  file TEXT NOT NULL,
  line INTEGER NOT NULL,
  context TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refs_symbol ON refs(symbol);
CREATE INDEX IF NOT EXISTS idx_refs_file ON refs(file);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY,
  file TEXT NOT NULL,
  chunk_id INTEGER NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file);
CREATE TABLE IF NOT EXISTS index_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

PY_EXTS = {".py"}
TS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
INDEXABLE_EXTS = PY_EXTS | TS_EXTS | {
    ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".html", ".css",
    ".sh", ".rs", ".go", ".java", ".rb", ".c", ".h", ".cpp",
}

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".awino",
    "dist", "build", "out", ".venv", "venv", ".tox", ".mypy_cache",
    ".pytest_cache", "target", ".next", ".nuxt", "coverage",
    ".idea", ".vscode",
}

CHUNK_LINES = 50
CHUNK_OVERLAP = 5
MAX_SNIPPET_LINES = 6
MAX_REFS_PER_SYMBOL_PER_FILE = 20


def _language(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PY_EXTS:
        return "python"
    if ext in TS_EXTS:
        return "typescript"
    return "text"


# ------------------------------------------------------------- extraction

class _PyVisitor(ast.NodeVisitor):
    """Extract (qualname, kind, lineno, signature) from a Python AST."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.stack: list[str] = []
        self.symbols: list[dict] = []
        self.imports: list[dict] = []

    def _sig(self, node) -> str:
        try:
            line = self.lines[node.lineno - 1].strip()
        except IndexError:
            line = ""
        # Trim multi-line defs to the first line; cap length.
        return line[:160]

    def visit_ClassDef(self, node):
        self._add(node.name, "class", node)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._add(node.name, "method" if self.stack else "function", node)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _add(self, name, kind, node):
        parent = ".".join(self.stack) if self.stack else None
        qual = f"{parent}.{name}" if parent else name
        self.symbols.append({
            "name": qual, "kind": kind, "line": node.lineno,
            "signature": self._sig(node), "parent": parent,
        })

    def visit_Import(self, node):
        for a in node.names:
            self.imports.append({"module": a.name,
                                "line": node.lineno})

    def visit_ImportFrom(self, node):
        mod = ("." * (node.level or 0)) + (node.module or "")
        for a in node.names:
            self.imports.append({"module": f"{mod}.{a.name}" if mod else a.name,
                                "line": node.lineno})


def extract_python(path: Path, text: str) -> dict:
    """Parse with ast; on SyntaxError fall back to regex so a broken file
    still yields something instead of nothing."""
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _extract_python_regex(lines)
    v = _PyVisitor(lines)
    v.visit(tree)
    symbols = [{
        **s, "snippet": _snippet(lines, s["line"]),
    } for s in v.symbols]
    return {"symbols": symbols, "imports": v.imports}


def _extract_python_regex(lines: list[str]) -> dict:
    """Fallback when ast fails: top-level def/class only."""
    symbols, imports = [], []
    for i, line in enumerate(lines, 1):
        m = re.match(r"^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\(", line)
        if m:
            symbols.append({"name": m.group(1), "kind": "function",
                            "line": i, "signature": line.strip()[:160],
                            "snippet": _snippet(lines, i), "parent": None})
            continue
        m = re.match(r"^\s*class\s+([A-Za-z_][\w]*)", line)
        if m:
            symbols.append({"name": m.group(1), "kind": "class",
                            "line": i, "signature": line.strip()[:160],
                            "snippet": _snippet(lines, i), "parent": None})
            continue
        m = re.match(r"^\s*(?:import|from)\s+([\w.]+)", line)
        if m:
            imports.append({"module": m.group(1), "line": i})
    return {"symbols": symbols, "imports": imports}


# TypeScript/JavaScript heuristics. One regex per construct; each records
# the name, kind, and line. Deliberately conservative: a missed exotic
# form is better than a wrong symbol.
_TS_PATTERNS = [
    # export class Foo / class Foo
    (re.compile(r"^\s*(?:export\s+(?:default\s+)?)?class\s+([A-Za-z_$][\w$]*)"),
     "class"),
    # export interface Foo / interface Foo
    (re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"),
     "interface"),
    # export type Foo = / type Foo =
    (re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*="),
     "type"),
    # export function foo( / function foo(
    (re.compile(r"^\s*(?:export\s+(?:default\s+|async\s+)?)?(?:async\s+)?"
               r"function\s+([A-Za-z_$][\w$]*)\s*\("),
     "function"),
    # export const foo = / const foo = (covers arrows too)
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
     "variable"),
    # methodName(...) {  (indented, inside class/object — cheap method guess;
    # allows an optional `: ReturnType` annotation before the brace)
    (re.compile(r"^\s{2,}(?:async\s+|static\s+|get\s+|set\s+)*"
               r"([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?::\s*[^{};]+)?\s*\{"),
     "method"),
]

_TS_IMPORT = re.compile(
    r"^\s*import\s+(?:[^'\"]*?\s+from\s+)?['\"]([^'\"]+)['\"]")
_TS_IMPORT2 = re.compile(
    r"^\s*import\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
_TS_REQUIRE = re.compile(
    r"(?:const|let|var)\s+[\w${}\s,]+\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)")
_TS_EXPORT_FROM = re.compile(
    r"^\s*export\s+(?:\*\s+from|\{[^}]*\}\s+from)\s+['\"]([^'\"]+)['\"]")


def extract_typescript(path: Path, text: str) -> dict:
    lines = text.splitlines()
    symbols, imports = [], []
    for i, line in enumerate(lines, 1):
        for pat, kind in _TS_PATTERNS:
            m = pat.match(line)
            if m:
                symbols.append({
                    "name": m.group(1), "kind": kind, "line": i,
                    "signature": line.strip()[:160],
                    "snippet": _snippet(lines, i), "parent": None,
                })
                break
        for pat in (_TS_IMPORT, _TS_IMPORT2, _TS_REQUIRE, _TS_EXPORT_FROM):
            m = pat.search(line)
            if m:
                imports.append({"module": m.group(1), "line": i})
                break
    return {"symbols": symbols, "imports": imports}


def _snippet(lines: list[str], lineno: int,
             n: int = MAX_SNIPPET_LINES) -> str:
    return "\n".join(lines[lineno - 1:lineno - 1 + n])[:800]


def extract_file(path: Path) -> dict | None:
    """Extract symbols/imports from one file. None = skipped (binary)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None  # binary
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            return None
    lang = _language(path)
    if lang == "python":
        data = extract_python(path, text)
    elif lang == "typescript":
        data = extract_typescript(path, text)
    else:
        data = {"symbols": [], "imports": []}
    data["text"] = text
    data["language"] = lang
    return data


# ------------------------------------------------------------------ index

class CodeIndex:
    """SQLite-backed codebase index for one workspace."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / ".awino" / "index.db"
        self._gitignore = self._load_gitignore()

    # -- setup -----------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db_path))
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        return con

    def _load_gitignore(self) -> list[str]:
        pats = []
        gi = self.workspace / ".gitignore"
        if gi.is_file():
            try:
                for line in gi.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("!"):
                        continue
                    pats.append(line)
            except OSError:
                pass
        return pats

    def _ignored(self, rel: str, is_dir: bool = False) -> bool:
        name = rel.rsplit("/", 1)[-1]
        for pat in self._gitignore:
            p = pat.rstrip("/")
            if "/" not in p:
                # bare name: matches any file/dir with that name
                if fnmatch.fnmatch(name, p):
                    return True
            else:
                if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p + "/*"):
                    return True
        return False

    def _walk(self) -> list[Path]:
        out = []
        for root, dirs, files in os.walk(self.workspace):
            # prune skip dirs (mutate in place) and gitignored dirs
            rel_root = os.path.relpath(root, self.workspace)
            dirs[:] = [d for d in dirs
                       if d not in SKIP_DIRS
                       and not self._ignored(
                           (rel_root + "/" + d).lstrip("./"), is_dir=True)]
            for f in files:
                p = Path(root) / f
                rel = p.relative_to(self.workspace).as_posix()
                if self._ignored(rel):
                    continue
                if p.suffix.lower() not in INDEXABLE_EXTS:
                    continue
                out.append(p)
        return sorted(out)

    # -- build -----------------------------------------------------------
    def build(self, force: bool = False) -> dict:
        """Full incremental build: only changed/added/removed files are
        processed. force=True re-extracts everything."""
        started = time.time()
        con = self._connect()
        try:
            known = {r[0]: (r[1], r[2], r[3])
                     for r in con.execute(
                         "SELECT path, mtime, size, hash FROM files")}
            disk = self._walk()
            disk_set = {p.relative_to(self.workspace).as_posix() for p in disk}
            added, changed, removed, skipped = 0, 0, 0, 0
            touched: list[str] = []
            for p in disk:
                rel = p.relative_to(self.workspace).as_posix()
                st = p.stat()
                rec = known.get(rel)
                if (not force and rec
                        and rec[0] == st.st_mtime and rec[1] == st.st_size):
                    skipped += 1  # fast path: mtime+size unchanged
                    continue
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                if not force and rec and rec[2] == digest:
                    skipped += 1  # content identical despite stat change
                    con.execute(
                        "UPDATE files SET mtime=?, size=? WHERE path=?",
                        (st.st_mtime, st.st_size, rel))
                    continue
                existed = rec is not None
                self._index_one(con, p, rel, st, digest)
                touched.append(rel)
                if existed:
                    changed += 1
                else:
                    added += 1
            for rel in known:
                if rel not in disk_set:
                    self._delete_file(con, rel)
                    removed += 1
            # Pass 2: cross-file call references. The symbol table is now
            # complete in the DB. Rescan refs for touched files; if the
            # global symbol set itself changed (a symbol added/removed),
            # rescan every file so new symbols pick up existing mentions.
            sym_hash = self._symbols_hash(con)
            old_hash = self._meta_get(con, "symbols_hash")
            if sym_hash != old_hash:
                ref_targets = [r[0] for r in con.execute(
                    "SELECT path FROM files")]
                self._meta_set(con, "symbols_hash", sym_hash)
            else:
                ref_targets = touched
            self._index_refs(con, ref_targets)
            con.commit()
            n = self._counts(con)
        finally:
            con.close()
        n.update({"added": added, "changed": changed, "removed": removed,
                  "skipped": skipped, "seconds": round(time.time() - started, 2),
                  "indexed": True, "db_path": str(self.db_path)})
        return n

    def _index_one(self, con: sqlite3.Connection, p: Path, rel: str,
                   st: os.stat_result, digest: str) -> None:
        data = extract_file(p)
        self._delete_file(con, rel)
        if data is None:
            return  # binary/unreadable: not indexed, not recorded
        con.execute(
            "INSERT OR REPLACE INTO files(path, mtime, size, hash, language,"
            " indexed_at) VALUES(?,?,?,?,?,?)",
            (rel, st.st_mtime, st.st_size, digest, data["language"],
             time.time()))
        lines = data["text"].splitlines()
        for s in data["symbols"]:
            con.execute(
                "INSERT INTO symbols(name, kind, file, line, signature,"
                " snippet, parent) VALUES(?,?,?,?,?,?,?)",
                (s["name"], s["kind"], rel, s["line"], s["signature"],
                 s["snippet"], s.get("parent")))
        # imports recorded as refs so related_files() sees shared modules
        for imp in data["imports"]:
            con.execute(
                "INSERT INTO refs(symbol, file, line, context) VALUES(?,?,?,?)",
                (f"import:{imp['module']}", rel, imp["line"],
                 lines[imp["line"] - 1].strip()[:200]
                 if imp["line"] - 1 < len(lines) else ""))
        # chunks for future embeddings (nullable embedding column)
        cid = 0
        for start in range(0, len(lines), CHUNK_LINES - CHUNK_OVERLAP):
            end = min(start + CHUNK_LINES, len(lines))
            con.execute(
                "INSERT INTO chunks(file, chunk_id, start_line, end_line,"
                " text, embedding) VALUES(?,?,?,?,?,NULL)",
                (rel, cid, start + 1, end,
                 "\n".join(lines[start:end])[:4000]))
            cid += 1
            if end >= len(lines):
                break
        return data["text"]

    def _delete_file(self, con: sqlite3.Connection, rel: str) -> None:
        con.execute("DELETE FROM files WHERE path=?", (rel,))
        con.execute("DELETE FROM symbols WHERE file=?", (rel,))
        con.execute("DELETE FROM refs WHERE file=?", (rel,))
        con.execute("DELETE FROM chunks WHERE file=?", (rel,))

    # -- pass 2: cross-file call references --------------------------------
    def _meta_get(self, con: sqlite3.Connection, key: str) -> str | None:
        row = con.execute("SELECT value FROM index_meta WHERE key=?",
                          (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, con: sqlite3.Connection, key: str, value: str) -> None:
        con.execute("INSERT OR REPLACE INTO index_meta(key, value)"
                    " VALUES(?,?)", (key, value))

    def _symbols_hash(self, con: sqlite3.Connection) -> str:
        h = hashlib.sha256()
        for r in con.execute("SELECT name FROM symbols ORDER BY name"):
            h.update(r[0].encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def _global_names(self, con: sqlite3.Connection) -> dict[str, list[str]]:
        """short name -> [qualnames]."""
        out: dict[str, list[str]] = {}
        for r in con.execute("SELECT name FROM symbols"):
            short = r[0].rsplit(".", 1)[-1]
            out.setdefault(short, []).append(r[0])
        return out

    def _index_refs(self, con: sqlite3.Connection,
                    rels: list[str]) -> int:
        """(Re)scan call references for `rels` against the global symbol
        table. Only non-import refs are touched — import refs are per-file
        and already correct from pass 1. Returns the ref count written."""
        if not rels:
            return 0
        names = self._global_names(con)
        if not names:
            return 0
        pat = re.compile(r"\b(" + "|".join(
            re.escape(n) for n in sorted(names, key=len, reverse=True)
        ) + r")\b")
        # unambiguous short name -> its qualname; ambiguous -> short name
        resolve = {s: (qs[0] if len(qs) == 1 else s)
                   for s, qs in names.items()}
        written = 0
        for rel in rels:
            con.execute("DELETE FROM refs WHERE file=? AND symbol NOT LIKE"
                        " 'import:%'", (rel,))
            import_lines = {r[0] for r in con.execute(
                "SELECT line FROM refs WHERE file=? AND symbol LIKE"
                " 'import:%'", (rel,))}
            p = self.workspace / rel
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines = text.splitlines()
            def_lines = {r[0] for r in con.execute(
                "SELECT line FROM symbols WHERE file=?", (rel,))}
            counts: dict[str, int] = {}
            for i, line in enumerate(lines, 1):
                if i in def_lines or i in import_lines:
                    continue
                for m in pat.finditer(line):
                    key = resolve[m.group(1)]
                    counts[key] = counts.get(key, 0) + 1
                    if counts[key] > MAX_REFS_PER_SYMBOL_PER_FILE:
                        continue
                    con.execute(
                        "INSERT INTO refs(symbol, file, line, context)"
                        " VALUES(?,?,?,?)",
                        (key, rel, i, line.strip()[:200]))
                    written += 1
        return written

    def rebuild_file(self, rel_path: str) -> dict:
        """Incremental: re-index one workspace-relative path (or delete its
        rows if it no longer exists). If the global symbol set changed,
        refs are rescanned workspace-wide so new symbols pick up
        existing mentions."""
        # The VS Code extension may send backslash separators on Windows;
        # the DB stores posix-style relative paths.
        rel_path = rel_path.replace("\\", "/")
        con = self._connect()
        try:
            old_hash = self._meta_get(con, "symbols_hash")
            p = (self.workspace / rel_path)
            if not p.is_file():
                self._delete_file(con, rel_path)
                self._meta_set(con, "symbols_hash", self._symbols_hash(con))
                self._index_refs(con, [r[0] for r in con.execute(
                    "SELECT path FROM files")])
                con.commit()
                return {"path": rel_path, "removed": True}
            st = p.stat()
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            self._index_one(con, p, rel_path, st, digest)
            new_hash = self._symbols_hash(con)
            self._meta_set(con, "symbols_hash", new_hash)
            if new_hash != old_hash:
                self._index_refs(con, [r[0] for r in con.execute(
                    "SELECT path FROM files")])
            else:
                self._index_refs(con, [rel_path])
            con.commit()
            return {"path": rel_path, "indexed": True}
        finally:
            con.close()

    def _counts(self, con: sqlite3.Connection) -> dict:
        return {
            "files": con.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "symbols": con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
            "refs": con.execute("SELECT COUNT(*) FROM refs").fetchone()[0],
            "chunks": con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        }

    # -- status ----------------------------------------------------------
    def status(self) -> dict:
        if not self.db_path.is_file():
            return {"indexed": False, "files": 0, "symbols": 0, "refs": 0,
                    "chunks": 0, "db_path": str(self.db_path)}
        con = self._connect()
        try:
            n = self._counts(con)
            row = con.execute(
                "SELECT MAX(indexed_at) FROM files").fetchone()
        finally:
            con.close()
        n.update({"indexed": True, "db_path": str(self.db_path),
                  "last_build": row[0]})
        return n

    # -- queries (read-only) ---------------------------------------------
    def _q(self, sql: str, args: tuple = ()) -> list[dict]:
        con = self._connect()
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def where_defined(self, symbol: str, limit: int = 20) -> list[dict]:
        """Exact qualname match first, then short-name, then substring."""
        short = symbol.rsplit(".", 1)[-1]
        rows = self._q(
            "SELECT name, kind, file, line, signature, parent FROM symbols"
            " WHERE name = ? OR name LIKE ? ORDER BY"
            " CASE WHEN name = ? THEN 0 WHEN name = ? THEN 1 ELSE 2 END,"
            " file, line LIMIT ?",
            (symbol, f"%{short}", symbol, short, limit))
        return rows

    def who_calls(self, symbol: str, limit: int = 30) -> list[dict]:
        """Files/lines referencing `symbol` (definition lines excluded —
        they were never recorded as refs)."""
        short = symbol.rsplit(".", 1)[-1]
        return self._q(
            "SELECT symbol, file, line, context FROM refs"
            " WHERE symbol = ? OR symbol = ? OR symbol LIKE ?"
            " ORDER BY file, line LIMIT ?",
            (symbol, short, f"%.{short}", limit))

    def search_symbols(self, query: str, limit: int = 25) -> list[dict]:
        """Keyword-ranked symbol search: exact > prefix > word-boundary >
        substring. Multi-token queries score per token."""
        tokens = [t.lower() for t in re.findall(r"[A-Za-z_][\w]*", query)]
        if not tokens:
            return []
        likes = " OR ".join(["LOWER(name) LIKE ?"] * len(tokens))
        args = [f"%{t}%" for t in tokens]
        rows = self._q(
            f"SELECT name, kind, file, line, signature FROM symbols"
            f" WHERE {likes} LIMIT 500", tuple(args))
        q = query.lower()

        def score(r: dict) -> tuple:
            name = r["name"].lower()
            short = name.rsplit(".", 1)[-1]
            if short == q or name == q:
                return (0, len(name))
            if short.startswith(q):
                return (1, len(name))
            tok_hits = sum(1 for t in tokens if t in short)
            if re.search(r"\b" + re.escape(q) + r"\b", short):
                return (2, -tok_hits, len(name))
            return (3, -tok_hits, len(name))

        rows.sort(key=score)
        return rows[:limit]

    def related_files(self, path: str, limit: int = 15) -> list[dict]:
        """Files related to `path`: shared imports first, then files it
        imports from / is imported by (module -> file resolution), then
        shared symbol names. Returns [{file, reasons}]."""
        path = path.replace("\\", "/")
        my_imports = {r["symbol"] for r in self._q(
            "SELECT DISTINCT symbol FROM refs WHERE file = ?"
            " AND symbol LIKE 'import:%'", (path,))}
        my_symbols = {r["name"].rsplit(".", 1)[-1] for r in self._q(
            "SELECT name FROM symbols WHERE file = ?", (path,))}
        if not my_imports and not my_symbols:
            return []
        scores: dict[str, set[str]] = {}

        def link(f: str, reason: str) -> None:
            if f != path:
                scores.setdefault(f, set()).add(reason)

        def module_stem(mod: str) -> str:
            # TS relative: './util' -> 'util'. Python dotted: 'util.helper'
            # -> 'util' (top-level package). '@/x' -> 'x'.
            m = mod.strip().lstrip(".")
            if "/" in m:
                m = m.split("/")[-1]
            else:
                m = m.split(".")[0]
            return m

        all_files = {r["path"] for r in self._q("SELECT path FROM files")}
        stems: dict[str, list[str]] = {}
        for f in all_files:
            stems.setdefault(Path(f).stem, []).append(f)

        if my_imports:
            ph = ",".join("?" * len(my_imports))
            for r in self._q(
                    f"SELECT DISTINCT file, symbol FROM refs WHERE symbol IN"
                    f" ({ph}) AND file != ?", (*my_imports, path)):
                link(r["file"], f"shared import {r['symbol'][7:]}")
            # resolve my imports to files: import:util.helper -> util.py
            for imp in my_imports:
                for f in stems.get(module_stem(imp[7:]), []):
                    link(f, f"imports {imp[7:]}")
        # reverse direction: files that import from me
        my_stem = Path(path).stem
        for r in self._q(
                "SELECT DISTINCT file, symbol FROM refs"
                " WHERE symbol LIKE 'import:%' AND file != ?", (path,)):
            if module_stem(r["symbol"][7:]) == my_stem:
                link(r["file"], f"imports from {path}")
        if my_symbols:
            ph = ",".join("?" * len(my_symbols))
            for r in self._q(
                    f"SELECT DISTINCT file, name FROM symbols WHERE"
                    f" (name IN ({ph}) OR " +
                    " OR ".join(["name LIKE ?"] * len(my_symbols)) +
                    f") AND file != ?",
                    (*my_symbols,
                     *[f"%.{s}" for s in my_symbols], path)):
                link(r["file"], f"shared symbol {r['name']}")
        ranked = sorted(scores.items(), key=lambda kv: -len(kv[1]))
        return [{"file": f, "reasons": sorted(rs)[:5]}
                for f, rs in ranked[:limit]]
