"""patch_file: strict unified-diff application on the prototype tool registry.

Fail-closed: ambiguous hunks, fuzzy offsets, context mismatches, and bad
hunk headers are refused with named error codes. Applies are atomic
(temp file + os.replace) and journaled (patch_applied / patch_refused
events). Offered in build mode only.
"""
import hashlib
import os
import tempfile
import unittest

from tools import Sandbox, TOOL_DEFS
from contract import MODES
from contract_loop import compile_turn_contract
from tests.common import make_loop, T
from backends import ScriptedBackend


DIFF_TWO_HUNK = """--- a/notes.txt
+++ b/notes.txt
@@ -1,3 +1,3 @@
 line one
-line two
+line TWO
 line three
@@ -5,3 +5,4 @@
 line five
 line six
+line six-point-five
 line seven
"""

FILE_V1 = "\n".join([
    "line one", "line two", "line three", "line four",
    "line five", "line six", "line seven",
]) + "\n"

FILE_V2 = "\n".join([
    "line one", "line TWO", "line three", "line four",
    "line five", "line six", "line six-point-five", "line seven",
]) + "\n"


def make_sandbox_with(content: str, name: str = "notes.txt"):
    root = tempfile.mkdtemp(prefix="awino-patch-")
    sb = Sandbox(root)
    sb.write_file(name, content)
    return sb, os.path.join(root, name)


class TestPatchToolRegistry(unittest.TestCase):
    def test_registered_consequential_with_args(self):
        self.assertIn("patch_file", TOOL_DEFS)
        self.assertTrue(TOOL_DEFS["patch_file"]["consequential"])
        self.assertEqual(TOOL_DEFS["patch_file"]["args"], ["path", "diff"])

    def test_mode_gating_static(self):
        self.assertIn("patch_file", MODES["build"]["tools"])
        self.assertIn("patch_file", MODES["build"]["consequential"])
        for mode in ("observe", "plan", "verify", "ship"):
            self.assertNotIn("patch_file", MODES[mode]["tools"],
                             f"patch_file must not be offered in {mode}")


class TestPatchApply(unittest.TestCase):
    def test_clean_multi_hunk_apply(self):
        sb, _ = make_sandbox_with(FILE_V1)
        res = sb.patch_file("notes.txt", DIFF_TWO_HUNK)
        self.assertNotIn("error", res, res)
        self.assertEqual(res["hunks_applied"], 2)
        self.assertEqual(sb.read_file("notes.txt")["content"], FILE_V2)

    def test_manifest_updated(self):
        sb, _ = make_sandbox_with(FILE_V1)
        sb.patch_file("notes.txt", DIFF_TWO_HUNK)
        m = sb.manifest()
        self.assertIn("notes.txt", m)
        self.assertEqual(m["notes.txt"],
                         hashlib.sha256(FILE_V2.encode()).hexdigest())

    def test_new_file_patch(self):
        sb = Sandbox(tempfile.mkdtemp(prefix="awino-patch-"))
        diff = ("--- /dev/null\n+++ b/new.txt\n"
                "@@ -0,0 +1,2 @@\n+hello\n+world\n")
        res = sb.patch_file("new.txt", diff)
        self.assertNotIn("error", res, res)
        self.assertEqual(sb.read_file("new.txt")["content"], "hello\nworld\n")

    def test_patch_missing_target_refused(self):
        sb = Sandbox(tempfile.mkdtemp(prefix="awino-patch-"))
        diff = "@@ -1,1 +1,1 @@\n-old\n+new\n"
        res = sb.patch_file("nope.txt", diff)
        self.assertEqual(res.get("error_code"), "PATCH_TARGET_MISSING")

    def test_traversal_refused(self):
        sb, _ = make_sandbox_with(FILE_V1)
        with self.assertRaises(ValueError):
            sb.patch_file("../escape.txt", DIFF_TWO_HUNK)


class TestPatchRefusals(unittest.TestCase):
    def test_bad_hunk_header(self):
        sb, _ = make_sandbox_with(FILE_V1)
        res = sb.patch_file("notes.txt", "@@ -1 @@\n context\n")
        self.assertEqual(res.get("error_code"), "BAD_HUNK_HEADER")

    def test_body_count_mismatch(self):
        sb, _ = make_sandbox_with(FILE_V1)
        # header claims 3 old lines, body carries 2
        diff = "@@ -1,3 +1,3 @@\n line one\n-line two\n"
        res = sb.patch_file("notes.txt", diff)
        self.assertEqual(res.get("error_code"), "BODY_COUNT_MISMATCH")

    def test_context_mismatch(self):
        sb, _ = make_sandbox_with(FILE_V1)
        diff = "@@ -1,2 +1,2 @@\n line one\n-line WRONG\n+line two\n"
        res = sb.patch_file("notes.txt", diff)
        self.assertEqual(res.get("error_code"), "CONTEXT_MISMATCH")

    def test_ambiguous_match_refused(self):
        sb = Sandbox(tempfile.mkdtemp(prefix="awino-patch-"))
        sb.write_file("dup.txt", "q\nx\nq\nx\n")
        # header claims line 1, which holds "q"; the hunk's old line "x"
        # matches at TWO locations -> ambiguous, must refuse
        diff = "@@ -1,1 +1,1 @@\n-x\n+y\n"
        res = sb.patch_file("dup.txt", diff)
        self.assertEqual(res.get("error_code"), "AMBIGUOUS_MATCH")

    def test_fuzzy_offset_refused(self):
        sb = Sandbox(tempfile.mkdtemp(prefix="awino-patch-"))
        sb.write_file("seq.txt", "a\nb\nc\nd\n")
        # header claims line 1, old line "c" matches exactly once (line 3);
        # applying at the drifted offset is forbidden
        diff = "@@ -1,1 +1,1 @@\n-c\n+C\n"
        res = sb.patch_file("seq.txt", diff)
        self.assertEqual(res.get("error_code"), "FUZZY_OFFSET")
        # file untouched
        self.assertEqual(sb.read_file("seq.txt")["content"], "a\nb\nc\nd\n")

    def test_empty_patch_refused(self):
        sb, _ = make_sandbox_with(FILE_V1)
        res = sb.patch_file("notes.txt", "")
        self.assertEqual(res.get("error_code"), "EMPTY_PATCH")
        res = sb.patch_file("notes.txt", "\n  \n")
        self.assertEqual(res.get("error_code"), "EMPTY_PATCH")

    def test_garbage_refused(self):
        sb, _ = make_sandbox_with(FILE_V1)
        res = sb.patch_file("notes.txt", "not a diff at all\n")
        self.assertEqual(res.get("error_code"), "UNSUPPORTED_DIFF")

    def test_multi_file_patch_refused(self):
        sb, _ = make_sandbox_with(FILE_V1)
        diff = ("--- a/notes.txt\n+++ b/notes.txt\n@@ -1,1 +1,1 @@\n"
                "-line one\n+LINE ONE\n"
                "--- a/other.txt\n+++ b/other.txt\n@@ -1,1 +1,1 @@\n"
                "-x\n+y\n")
        res = sb.patch_file("notes.txt", diff)
        self.assertEqual(res.get("error_code"), "MULTI_FILE_PATCH")


class TestPatchAtomicity(unittest.TestCase):
    def test_failed_apply_leaves_file_byte_identical(self):
        sb, p = make_sandbox_with(FILE_V1)
        from pathlib import Path as _P
        before = hashlib.sha256(_P(p).read_bytes()).hexdigest()
        # first hunk is fine; second hunk's context does not exist
        diff = ("@@ -1,3 +1,3 @@\n line one\n-line two\n+line TWO\n line three\n"
                "@@ -5,2 +5,2 @@\n line five\n-line DOES NOT EXIST\n+zzz\n")
        res = sb.patch_file("notes.txt", diff)
        self.assertIn("error", res)
        after = hashlib.sha256(_P(p).read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(sb.read_file("notes.txt")["content"], FILE_V1)

    def test_no_temp_files_left_behind(self):
        sb, p = make_sandbox_with(FILE_V1)
        sb.patch_file("notes.txt", DIFF_TWO_HUNK)
        leftovers = [f for f in os.listdir(os.path.dirname(p))
                     if ".awino-patch-" in f]
        self.assertEqual(leftovers, [])


class TestPatchModeGating(unittest.TestCase):
    def test_offered_in_build_not_observe_or_plan(self):
        loop, _ = make_loop()
        for mode, expect in (("build", True), ("observe", False),
                             ("plan", False), ("verify", False),
                             ("ship", False)):
            loop.state.snapshot["mode"] = mode
            c = compile_turn_contract(loop.state)
            self.assertEqual("patch_file" in c["offered_tools"], expect,
                             f"mode={mode}")
            self.assertEqual("patch_file" in c["consequential_tools"], expect,
                             f"mode={mode}")


class TestPatchJournaling(unittest.TestCase):
    def _drive_build_turn(self, tool_calls):
        backend = ScriptedBackend([
            T(tool_calls=tool_calls, progress_delta="patching",
              assumptions=["Cause: the test needs a patch applied."]),
        ])
        loop, _ = make_loop(backend=backend)
        loop.set_mission("Patch notes", ["manual"])
        loop.state.record("plan_updated", {"plan": ["Patch notes.txt"]})
        loop.approve_contract()
        loop.approve_contract(["notes.txt"])
        sb = loop.sandbox
        sb.write_file("notes.txt", FILE_V1)
        r = loop.run_user_turn("apply the patch")
        if r["status"] == "awaiting_approval":
            for aid in r["approvals"]:
                loop.approve(aid)
        return loop

    def test_applied_patch_journaled(self):
        loop = self._drive_build_turn([
            {"name": "patch_file",
             "args": {"path": "notes.txt", "diff": DIFF_TWO_HUNK}},
        ])
        applied = [e for e in loop.state.events
                   if e["type"] == "patch_applied"]
        self.assertTrue(applied, "expected a patch_applied journal event")
        self.assertEqual(applied[0]["data"]["hunks_applied"], 2)
        self.assertIn("notes.txt", applied[0]["data"]["path"])
        # the generic tool_result journal also carries the outcome
        results = [e for e in loop.state.events
                   if e["type"] == "tool_result"
                   and e["data"].get("tool") == "patch_file"]
        self.assertTrue(results)
        self.assertNotIn("error", results[0]["data"]["result"])
        # and the file really changed
        self.assertEqual(loop.sandbox.read_file("notes.txt")["content"],
                         FILE_V2)

    def test_refused_patch_journaled(self):
        bad = "@@ -1,2 +1,2 @@\n line one\n-line WRONG\n+line two\n"
        loop = self._drive_build_turn([
            {"name": "patch_file", "args": {"path": "notes.txt", "diff": bad}},
        ])
        refused = [e for e in loop.state.events
                   if e["type"] == "patch_refused"]
        self.assertTrue(refused, "expected a patch_refused journal event")
        self.assertEqual(refused[0]["data"]["error_code"], "CONTEXT_MISMATCH")
        # refusal left the file untouched
        self.assertEqual(loop.sandbox.read_file("notes.txt")["content"],
                         FILE_V1)


if __name__ == "__main__":
    unittest.main()
