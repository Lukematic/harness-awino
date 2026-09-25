"""Codebase indexer: extraction accuracy, change detection, query
correctness. Uses temp workspaces; never touches the real tree."""
import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from indexer import (CodeIndex, extract_python, extract_typescript,
                     extract_file)


def _ws(files: dict[str, str]) -> str:
    d = tempfile.mkdtemp(prefix="awino-index-test-")
    for rel, content in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(textwrap.dedent(content))
    return d


PY_SAMPLE = '''
    import os
    from pkg import thing

    CONST = 1

    def top_function(a, b):
        """Do things."""
        return helper(a) + b

    class Widget:
        def __init__(self):
            self.x = 1

        def render(self):
            return top_function(self.x, 2)

        async def load(self):
            pass
'''

TS_SAMPLE = '''
    import { helper } from './util';
    import * as fs from 'fs';

    export interface Config {
        name: string;
    }

    export type Handler = (x: number) => void;

    export class App {
        boot(): void {
            helper();
        }

        async start(port: number): Promise<void> {}
    }

    export function main(): void {
        const app = new App();
        app.boot();
    }

    export const version = "1.0";
'''


class TestExtraction(unittest.TestCase):
    def test_python_ast(self):
        data = extract_python(None, textwrap.dedent(PY_SAMPLE))
        names = {s["name"]: s["kind"] for s in data["symbols"]}
        self.assertEqual(names.get("top_function"), "function")
        self.assertEqual(names.get("Widget"), "class")
        self.assertEqual(names.get("Widget.__init__"), "method")
        self.assertEqual(names.get("Widget.render"), "method")
        self.assertEqual(names.get("Widget.load"), "method")
        # module-level constant is not a symbol (by design)
        self.assertNotIn("CONST", names)
        mods = {i["module"] for i in data["imports"]}
        self.assertIn("os", mods)
        self.assertIn("pkg.thing", mods)

    def test_python_broken_falls_back_to_regex(self):
        data = extract_python(None, "def broken(:\n")
        names = [s["name"] for s in data["symbols"]]
        self.assertIn("broken", names)

    def test_typescript_heuristics(self):
        data = extract_typescript(None, textwrap.dedent(TS_SAMPLE))
        names = {s["name"]: s["kind"] for s in data["symbols"]}
        self.assertEqual(names.get("App"), "class")
        self.assertEqual(names.get("Config"), "interface")
        self.assertEqual(names.get("Handler"), "type")
        self.assertEqual(names.get("main"), "function")
        self.assertEqual(names.get("boot"), "method")
        self.assertEqual(names.get("start"), "method")
        self.assertEqual(names.get("version"), "variable")
        self.assertEqual(names.get("app"), "variable")
        mods = {i["module"] for i in data["imports"]}
        self.assertIn("./util", mods)
        self.assertIn("fs", mods)

    def test_binary_skipped(self):
        d = tempfile.mkdtemp(prefix="awino-index-bin-")
        p = os.path.join(d, "a.py")
        with open(p, "wb") as f:
            f.write(b"\x00\x01\x02def fake():\n")
        from pathlib import Path
        self.assertIsNone(extract_file(Path(p)))


class TestBuildAndChangeDetection(unittest.TestCase):
    def test_incremental(self):
        d = _ws({"a.py": PY_SAMPLE, "b.ts": TS_SAMPLE,
                 "node_modules/x.js": "var z = 1;"})
        idx = CodeIndex(d)
        r1 = idx.build()
        self.assertEqual(r1["added"], 2)  # node_modules skipped
        self.assertEqual(r1["files"], 2)
        self.assertGreater(r1["symbols"], 5)

        r2 = idx.build()
        self.assertEqual(r2["added"], 0)
        self.assertEqual(r2["changed"], 0)
        self.assertEqual(r2["skipped"], 2)

        # touch one file: only it is re-extracted
        with open(os.path.join(d, "a.py"), "a") as f:
            f.write("\n# a comment\n")
        r3 = idx.build()
        self.assertEqual(r3["changed"], 1)
        self.assertEqual(r3["skipped"], 1)

        # delete a file: its rows vanish
        os.remove(os.path.join(d, "b.ts"))
        r4 = idx.build()
        self.assertEqual(r4["removed"], 1)
        self.assertEqual(idx.status()["files"], 1)
        self.assertEqual(idx.where_defined("App"), [])

    def test_gitignore_respected(self):
        d = _ws({".gitignore": "ignored/\n*.log\n",
                 "ignored/a.py": "def x(): pass\n",
                 "debug.log": "x\n",
                 "ok.py": "def y(): pass\n"})
        idx = CodeIndex(d)
        r = idx.build()
        self.assertEqual(r["files"], 1)
        self.assertEqual(idx.where_defined("y")[0]["file"], "ok.py")
        self.assertEqual(idx.where_defined("x"), [])


class TestQueries(unittest.TestCase):
    def setUp(self):
        self.d = _ws({
            "util.py": "def helper():\n    return 42\n",
            "main.py": ("import os\nfrom util import helper\n\n"
                        "class Server:\n"
                        "    def start(self):\n"
                        "        helper()\n"),
            "app.ts": ("import { helper } from './util';\n\n"
                       "export function run(): void {\n"
                       "    helper();\n}\n"),
        })
        self.idx = CodeIndex(self.d)
        self.idx.build()

    def test_where_defined(self):
        hits = self.idx.where_defined("Server")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "main.py")
        self.assertEqual(hits[0]["line"], 4)
        # qualname lookup
        hits = self.idx.where_defined("Server.start")
        self.assertEqual(hits[0]["line"], 5)

    def test_who_calls_cross_file(self):
        refs = self.idx.who_calls("helper")
        files = {(r["file"], r["line"]) for r in refs}
        self.assertIn(("main.py", 6), files)
        self.assertIn(("app.ts", 4), files)

    def test_search_symbols_ranked(self):
        hits = self.idx.search_symbols("serv")
        self.assertEqual(hits[0]["name"], "Server")
        self.assertTrue(any(h["name"] == "Server.start" for h in hits))

    def test_related_files(self):
        rel = self.idx.related_files("main.py")
        files = [r["file"] for r in rel]
        self.assertIn("util.py", files)
        # reverse direction: util.py is imported by main.py and app.ts
        rel2 = self.idx.related_files("util.py")
        files2 = [r["file"] for r in rel2]
        self.assertIn("main.py", files2)
        self.assertIn("app.ts", files2)

    def test_chunks_schema_has_nullable_embedding(self):
        con = sqlite3.connect(str(self.idx.db_path))
        try:
            cols = {r[1]: r[2] for r in con.execute(
                "PRAGMA table_info(chunks)")}
            self.assertIn("embedding", cols)
            n = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            self.assertGreater(n, 0)
            nulls = con.execute(
                "SELECT COUNT(*) FROM chunks WHERE embedding IS NULL"
            ).fetchone()[0]
            self.assertEqual(nulls, n)  # all NULL until embeddings land
        finally:
            con.close()

    def test_rebuild_file_targeted(self):
        # add a call to helper() in main.py via targeted rebuild
        p = os.path.join(self.d, "main.py")
        with open(p, "a") as f:
            f.write("\nhelper()\n")
        r = self.idx.rebuild_file("main.py")
        self.assertTrue(r.get("indexed"))
        refs = self.idx.who_calls("helper")
        self.assertIn(("main.py", 8),
                      {(x["file"], x["line"]) for x in refs})


class TestReadOnlyGuarantee(unittest.TestCase):
    """The turn tool path must never be able to write the index."""

    def test_sandbox_index_query_has_no_write_path(self):
        import inspect
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from awino_sidecar import WorkspaceSandbox
        src = inspect.getsource(WorkspaceSandbox.index_query)
        for token in ("INSERT", "UPDATE", "DELETE", "build(", "rebuild_file(",
                      "_index_one", "_index_refs", "executescript"):
            self.assertNotIn(token, src,
                             f"index_query must not contain {token!r}")

    def test_tool_def_is_non_consequential(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from awino_sidecar import _apply_sidecar_tool_profile
        from tools import TOOL_DEFS
        _apply_sidecar_tool_profile()
        self.assertIn("index_query", TOOL_DEFS)
        self.assertFalse(TOOL_DEFS["index_query"]["consequential"])


class TestSidecarIntegration(unittest.TestCase):
    """Full subprocess round-trip: hello -> index_status (auto-build) ->
    index_query (all four ops) -> index_rebuild (targeted). Proves the
    protocol wiring, not just the Python API."""

    def setUp(self):
        import json as _json
        import select as _select
        import shutil as _shutil
        import subprocess as _sp
        self._json, self._select, self._shutil, self._sp = \
            _json, _select, _shutil, _sp
        self.ws = tempfile.mkdtemp(prefix="awino-index-itest-")
        with open(os.path.join(self.ws, "svc.py"), "w") as f:
            f.write("def deploy():\n    start_server()\n\n"
                    "def start_server():\n    pass\n")
        self.home = tempfile.mkdtemp(prefix="awino-index-ihome-")
        env = dict(os.environ, AWINO_HOME=self.home)
        sidecar = os.path.join(os.path.dirname(__file__), "..",
                               "awino_sidecar.py")
        self.p = _sp.Popen(["python3", sidecar], stdin=_sp.PIPE,
                           stdout=_sp.PIPE, stderr=_sp.PIPE, env=env)
        os.set_blocking(self.p.stdout.fileno(), False)
        self._rbuf = b""

    def tearDown(self):
        try:
            self._send({"cmd": "bye"})
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()
        self._shutil.rmtree(self.ws, ignore_errors=True)
        self._shutil.rmtree(self.home, ignore_errors=True)

    def _send(self, obj):
        self.p.stdin.write((self._json.dumps(obj) + "\n").encode())
        self.p.stdin.flush()

    def _recv(self, timeout=60):
        deadline = __import__("time").time() + timeout
        while True:
            nl = self._rbuf.find(b"\n")
            if nl >= 0:
                line = self._rbuf[:nl]
                self._rbuf = self._rbuf[nl + 1:]
                return self._json.loads(line.decode())
            remaining = deadline - __import__("time").time()
            assert remaining > 0, "timed out waiting for sidecar event"
            r, _, _ = self._select.select([self.p.stdout], [], [], remaining)
            assert r, "timed out waiting for sidecar event"
            chunk = os.read(self.p.stdout.fileno(), 65536)
            assert chunk, "sidecar closed stdout"
            self._rbuf += chunk

    def _cmd(self, name, args=None):
        self._send({"cmd": "command", "name": name, "args": args or {}})
        ev = self._recv()
        self.assertEqual(ev["event"], "command_result")
        self.assertEqual(ev["name"], name)
        self.assertTrue(ev["ok"], f"{name} failed: {ev['result']}")
        return ev["result"]

    def test_round_trip(self):
        self._send({"cmd": "hello", "workspace": self.ws,
                    "provider": "echo"})
        ev = self._recv()
        self.assertEqual(ev["event"], "ready")

        st = self._cmd("index_status")
        self.assertTrue(st["index"]["indexed"])
        # hello bootstraps README.md/lessons.md into the workspace, so the
        # count includes those — what matters is svc.py got indexed.
        self.assertGreaterEqual(st["index"]["files"], 1)
        self.assertGreater(st["index"]["symbols"], 0)

        q = self._cmd("index_query", {"op": "where_defined",
                                     "query": "deploy"})
        self.assertEqual(q["results"][0]["file"], "svc.py")

        q = self._cmd("index_query", {"op": "who_calls",
                                     "query": "start_server"})
        self.assertTrue(any(r["file"] == "svc.py" for r in q["results"]))

        q = self._cmd("index_query", {"op": "search_symbols",
                                     "query": "depl"})
        self.assertEqual(q["results"][0]["name"], "deploy")

        q = self._cmd("index_query", {"op": "related_files",
                                     "path": "svc.py"})
        self.assertIsInstance(q["results"], list)

        # unknown op fails closed
        self._send({"cmd": "command", "name": "index_query",
                    "args": {"op": "drop_table"}})
        ev = self._recv()
        self.assertFalse(ev["ok"])

        # targeted rebuild after edit
        with open(os.path.join(self.ws, "svc.py"), "a") as f:
            f.write("\ndef stop():\n    pass\n")
        r = self._cmd("index_rebuild", {"paths": ["svc.py"]})
        self.assertTrue(r["files"][0].get("indexed"))
        q = self._cmd("index_query", {"op": "where_defined",
                                     "query": "stop"})
        self.assertEqual(q["results"][0]["name"], "stop")


if __name__ == "__main__":
    unittest.main()
