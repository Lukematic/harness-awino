"""The JSON-turn prompt's tool catalog is generated from TOOL_SCHEMAS.

Field bug (v0.6.0, real VS Code): the model was told `list_dir {} (takes no
arguments)`; the sidecar's str.replace patch had silently stopped matching,
so every "look in folder X" request listed the workspace root instead.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import awino_sidecar as s
import backends
from tool_schema import TOOL_SCHEMAS, tool_catalog


class ToolPromptTest(unittest.TestCase):
    def setUp(self):
        s._apply_sidecar_tool_profile()

    def test_no_hand_written_tool_list_left(self):
        self.assertIn("{tools}", backends._OLLAMA_SYSTEM)
        self.assertNotIn("takes no arguments", backends._OLLAMA_SYSTEM)
        self.assertNotIn('list_dir takes "args": {}', backends._OLLAMA_SYSTEM)

    def test_every_schema_parameter_is_named(self):
        cat = tool_catalog()
        for name, schema in TOOL_SCHEMAS.items():
            line = next(l for l in cat.splitlines()
                        if l.strip().startswith(f"- {name} "))
            for param in schema["parameters"].get("properties", {}):
                self.assertIn(f'"{param}"', line, f"{name}.{param}")

    def test_list_dir_takes_an_optional_path(self):
        self.assertIn('- list_dir {"path"?}', tool_catalog())

    def test_consequential_tools_are_marked(self):
        cat = tool_catalog()
        for name in ("write_file", "patch_file", "run_command"):
            line = next(l for l in cat.splitlines() if f"- {name} " in l)
            self.assertIn("needs approval", line)


class SidecarSearchFilesArgsTest(unittest.TestCase):
    """The native-tools schema sends file_glob/top_k; the sidecar sandbox
    must accept them instead of raising TypeError."""

    def test_schema_arguments_are_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.py").write_text("needle\n")
            Path(d, "b.txt").write_text("needle\n")
            sb = s.WorkspaceSandbox(d)
            r = sb.search_files(pattern="needle", file_glob="*.py", top_k=5)
            self.assertEqual([h["file"] for h in r["hits"]], ["a.py"])
            r = sb.search_files(pattern="needle", top_k=1)
            self.assertEqual(len(r["hits"]), 1)
            self.assertTrue(r["truncated"])


if __name__ == "__main__":
    unittest.main()
