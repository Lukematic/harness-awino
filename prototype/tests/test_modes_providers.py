#!/usr/bin/env python3
"""Scope #4 (Kilo-parity, A.W.I.N.O.-style): scoped provider bindings,
modes-as-data, skill-as-persona.

Fast import-level tests: the YAML subset parser, binding precedence,
the mode registry, tool-policy intersection (modes can never widen the
stage's offered set), per-call temperature plumbing, persona tool
priority, and key-material redaction. Subprocess end-to-end tests live in
test_sidecar.py.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "awino_sidecar_mod",
    str(Path(__file__).resolve().parent.parent / "awino_sidecar.py"))
sc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(sc)


class YamlSubsetTest(unittest.TestCase):
    def test_valid_file(self):
        parsed = sc._parse_providers_yaml(
            "# comment line\n"
            "default_environment: default\n"
            "environments:\n"
            "  default:\n"
            "    provider: ollama  # trailing comment\n"
            "    model: qwen2.5:7b\n"
            "  prod:\n"
            "    provider: openai\n"
            "    model: gpt-4o-mini\n"
            "    endpoint: https://api.openai.com/v1\n"
            "    key_id: prod-openai-key\n")
        self.assertNotIn("error", parsed)
        self.assertEqual(parsed["default_environment"], "default")
        self.assertEqual(parsed["environments"]["prod"]["key_id"],
                         "prod-openai-key")
        self.assertEqual(parsed["environments"]["default"]["provider"],
                         "ollama")

    def test_quoted_values(self):
        parsed = sc._parse_providers_yaml(
            'environments:\n  d:\n    model: "qwen2.5:7b"\n')
        self.assertEqual(parsed["environments"]["d"]["model"], "qwen2.5:7b")

    def test_tabs_rejected(self):
        self.assertIn("error", sc._parse_providers_yaml(
            "environments:\n\tbad:\n"))

    def test_unknown_provider_rejected(self):
        self.assertIn("error", sc._parse_providers_yaml(
            "environments:\n  d:\n    provider: skynet\n"))

    def test_bad_key_id_rejected(self):
        self.assertIn("error", sc._parse_providers_yaml(
            "environments:\n  d:\n    key_id: 'has spaces!'\n"))

    def test_unknown_field_rejected(self):
        self.assertIn("error", sc._parse_providers_yaml(
            "environments:\n  d:\n    api_key: sk-SECRET-MATERIAL\n"))

    def test_duplicate_key_rejected(self):
        self.assertIn("error", sc._parse_providers_yaml(
            "environments:\n  d:\n    provider: ollama\n    provider: openai\n"))

    def test_dangling_default_environment_rejected(self):
        self.assertIn("error", sc._parse_providers_yaml(
            "default_environment: nope\nenvironments:\n  d:\n"))

    def test_hash_inside_url_preserved(self):
        parsed = sc._parse_providers_yaml(
            "environments:\n  d:\n"
            "    endpoint: http://localhost:11434/#frag\n")
        self.assertEqual(parsed["environments"]["d"]["endpoint"],
                         "http://localhost:11434/#frag")

    def test_never_raises(self):
        for bad in ("", "[[[", "environments: 42\n", "a:\n b:\n  c:\n   d: e\n"):
            r = sc._parse_providers_yaml(bad)
            self.assertIsInstance(r, dict)


class BindingPrecedenceTest(unittest.TestCase):
    def _sidecar_with_yaml(self, yaml_text):
        ws = Path(tempfile.mkdtemp(prefix="awino-bind-"))
        if yaml_text is not None:
            d = ws / ".awino"
            d.mkdir()
            (d / "providers.yaml").write_text(yaml_text)
        s = sc.Sidecar()
        s.workspace = ws
        return s

    YAML = ("default_environment: default\nenvironments:\n"
            "  default:\n    provider: ollama\n    model: qwen2.5:7b\n"
            "  prod:\n    provider: openai\n    model: gpt-4o-mini\n"
            "    key_id: prod-key\n")

    def test_no_file_uses_global_settings(self):
        s = self._sidecar_with_yaml(None)
        b = s._resolve_binding({"provider": "echo"})
        self.assertEqual(b["provider"], "echo")
        self.assertEqual(b["source"], "global-settings")
        self.assertIsNone(b["environment"])

    def test_project_file_overrides_global(self):
        s = self._sidecar_with_yaml(self.YAML)
        b = s._resolve_binding({"provider": "echo"})
        self.assertEqual(b["provider"], "ollama")
        self.assertEqual(b["model"], "qwen2.5:7b")
        self.assertEqual(b["environment"], "default")
        self.assertEqual(b["source"], "project-file")
        self.assertIsNone(b["key_id"])

    def test_named_environment_wins_over_default(self):
        s = self._sidecar_with_yaml(self.YAML)
        b = s._resolve_binding({"provider": "echo", "environment": "prod"})
        self.assertEqual(b["provider"], "openai")
        self.assertEqual(b["model"], "gpt-4o-mini")
        self.assertEqual(b["key_id"], "prod-key")
        self.assertEqual(b["source"], "named-environment")

    def test_unknown_environment_is_an_error(self):
        s = self._sidecar_with_yaml(self.YAML)
        b = s._resolve_binding({"environment": "nope"})
        self.assertIn("error", b)

    def test_invalid_file_fails_closed(self):
        s = self._sidecar_with_yaml("environments:\n\td:\n")
        b = s._resolve_binding({"provider": "echo"})
        self.assertIn("error", b)

    def test_binding_carries_no_key_material(self):
        s = self._sidecar_with_yaml(self.YAML)
        os.environ["AWINO_KEY_PROD_KEY"] = "sk-SECRET-MATERIAL-xyz"
        try:
            b = s._resolve_binding({"environment": "prod"})
            blob = json.dumps(b)
            self.assertNotIn("sk-SECRET-MATERIAL-xyz", blob)
            self.assertEqual(b["key_id"], "prod-key")  # opaque ref is fine
        finally:
            del os.environ["AWINO_KEY_PROD_KEY"]

    def test_key_lookup_statuses(self):
        mat, st = sc._lookup_key_material("")
        self.assertEqual((mat, st), (None, "not-required"))
        mat, st = sc._lookup_key_material("absent-key")
        self.assertEqual((mat, st), (None, "missing"))
        os.environ["AWINO_KEY_ABSENT_KEY"] = "sk-abc"
        try:
            mat, st = sc._lookup_key_material("absent-key")
            self.assertEqual((mat, st), ("sk-abc", "configured"))
        finally:
            del os.environ["AWINO_KEY_ABSENT_KEY"]


class ModesAsDataTest(unittest.TestCase):
    def test_stage_defaults(self):
        s = sc.Sidecar()
        s.loop = None
        for phase, expected in [("DEFINE", "interview"), ("PLAN", "architect"),
                                ("BUILD", "code"), ("VERIFY", "test"),
                                ("REVIEW", "review"), ("SHIP", "release")]:
            self.assertEqual(s._stage_default_mode(phase)["id"], expected)

    def test_builtin_modes_are_pure_data(self):
        for m in sc.BUILTIN_MODES:
            for field in ("id", "label", "stages", "stance_prompt",
                          "tool_policy", "sampling"):
                self.assertIn(field, m, m["id"])
            self.assertTrue(m["stance_prompt"].strip())

    def test_tutor_lineage_and_temperature(self):
        tutor = next(m for m in sc.BUILTIN_MODES if m["id"] == "tutor")
        self.assertIn("grill", tutor["stance_prompt"])
        self.assertGreater(tutor["sampling"]["temperature"], 0.5)

    def test_tool_emphasis_never_widens(self):
        mode = {"id": "x", "tool_policy": ["write_file", "nope_tool_xyz"]}
        offered = ["read_file", "write_file"]
        emphasis, dropped = sc._mode_tool_emphasis(mode, offered)
        self.assertEqual(emphasis, ["write_file", "read_file"])
        self.assertEqual(dropped, ["nope_tool_xyz"])
        self.assertTrue(all(t in offered for t in emphasis))

    def test_tool_emphasis_none_is_identity(self):
        mode = {"id": "x", "tool_policy": None}
        offered = ["a", "b"]
        emphasis, dropped = sc._mode_tool_emphasis(mode, offered)
        self.assertEqual((emphasis, dropped), (offered, []))

    def test_custom_mode_validation(self):
        good = {"id": "my-mode", "stages": ["BUILD"],
                "stance_prompt": "Build carefully.",
                "tool_policy": ["read_file"], "sampling": {"temperature": 0.1}}
        m = sc._validate_custom_mode(good)
        self.assertNotIn("error", m)
        self.assertTrue(m["custom"])
        bad_shadow = dict(good, id="code")
        self.assertIn("error", sc._validate_custom_mode(bad_shadow))
        bad_temp = dict(good, sampling={"temperature": 99})
        self.assertIn("error", sc._validate_custom_mode(bad_temp))
        bad_field = dict(good, evil="x")
        self.assertIn("error", sc._validate_custom_mode(bad_field))

    def test_temperature_injected_from_active_mode(self):
        seen = {}

        class FakeBackend:
            def generate(self, cb, history, feedback=None, temperature=None):
                seen["temperature"] = temperature
                return {}

        s = sc.Sidecar()
        s.loop = None
        wrapped = sc._ModeAwareBackend(FakeBackend(), s)
        wrapped.generate("h", [])
        self.assertEqual(seen["temperature"], 0.3)  # interview default
        s._mode_overlay = {"mode": "tutor", "scope": "mission",
                           "invoked_turn": 0}
        wrapped.generate("h", [])
        self.assertEqual(seen["temperature"], 0.8)

    def test_overlay_expiry(self):
        s = sc.Sidecar()
        s.loop = None
        s._mode_overlay = {"mode": "tutor", "scope": "turns",
                           "invoked_turn": 0, "expires_turn": 0}
        mode, info = s._active_mode()
        self.assertEqual(info["source"], "stage-default")
        self.assertIsNone(s._mode_overlay)


class PersonaTest(unittest.TestCase):
    def test_tool_priority_is_intersection_in_mention_order(self):
        body = ("Always use write_file for changes; run_command to verify; "
                "never touch the prod database. write_file again.")
        offered = ["read_file", "write_file", "run_command"]
        self.assertEqual(sc._persona_tool_priority(body, offered),
                         ["write_file", "run_command"])

    def test_unmentioned_tools_excluded(self):
        body = "Think carefully."
        self.assertEqual(sc._persona_tool_priority(body, ["read_file"]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
