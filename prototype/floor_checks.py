"""Mechanical layer: the osmani-constraints FLOOR as deterministic checks.

These rules lived as prompt text ("FLOOR (always enforced, no setup)" in
osmani-constraints). The skill itself demands the escalation written ->
scripted -> tool-backed, so they are enforced here by code, on the mission
diff, at the REVIEW -> SHIP transition. The model never self-grades.

Pure functions: no I/O, no model judgment. Findings are dicts with
{check, file, line, detail}. A finding is a refusal, not a warning — the
floor is always enforced, no setup.
"""

import re

# ---------------------------------------------------------------------------
# Finding constructor
# ---------------------------------------------------------------------------

def _finding(check, path, line, detail):
    return {"check": check, "file": path, "line": line, "detail": detail}

# ---------------------------------------------------------------------------
# Check 1: no new suppression comments
# ---------------------------------------------------------------------------

_SUPPRESSION_RES = [
    re.compile(p, re.IGNORECASE) for p in (
        r"@ts-ignore",
        r"@ts-nocheck",
        r"@ts-expect-error",
        r"eslint-disable",
        r"tslint:\s*disable",
        r"pylint:\s*disable",
        r"#\s*noqa\b",
        r"#\s*type:\s*ignore",
        r"//\s*nolint",
        r"#\s*nolint",
        r"rubocop:\s*disable",
    )
]

# ---------------------------------------------------------------------------
# Check 2: no unimplemented stubs or empty catch blocks
# ---------------------------------------------------------------------------

_STUB_RES = [
    re.compile(r"raise\s+NotImplementedError\b"),
    re.compile(r"^\s*\.\.\.\s*(#.*)?$"),          # bare ellipsis body
    re.compile(r"\b(TODO|FIXME|XXX)\b"),           # unfinished work markers
]
_EXCEPT_RE = re.compile(r"^\s*except\b.*:\s*(#.*)?$")
_PASS_RE = re.compile(r"^\s*pass\s*(#.*)?$")

# ---------------------------------------------------------------------------
# Check 3: no skipped/deleted tests without a reason
# ---------------------------------------------------------------------------

_SKIP_RES = [
    re.compile(r"@pytest\.mark\.skip\b"),
    re.compile(r"@pytest\.mark\.skipif\b"),
    re.compile(r"@unittest\.skip\b"),
    re.compile(r"@unittest\.skipIf\b"),
    re.compile(r"\b(describe|it|test)\.skip\s*\("),
    re.compile(r"\bx(describe|it|test)\s*\("),
    re.compile(r"\bt\.Skip\s*\("),
]
_REASON_RE = re.compile(r"""\breason\s*=|['"][^'"]{4,}['"]""")

# ---------------------------------------------------------------------------
# Check 4: no secrets in source (high-precision patterns only)
# ---------------------------------------------------------------------------

_SECRET_KEY_RE = re.compile(
    r"""(?i)\b(api[_-]?key|secret|passwd|password|pwd|pw|"""
    r"""auth[_-]?token|access[_-]?token|private[_-]?key|"""
    r"""client[_-]?secret)\b\s*[:=]\s*['"]([^'"]+)['"]"""
)
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_PLACEHOLDERS = {
    "xxx", "***", "your-key", "your_api_key", "yourapikey", "changeme",
    "example", "test", "testing", "placeholder", "dummy", "none", "null",
    "undefined", "redacted", "secret", "password",
}


def _looks_like_secret(value: str) -> bool:
    v = value.strip()
    if v.lower() in _PLACEHOLDERS:
        return False
    if len(v) < 12:
        return False
    has_letter = any(c.isalpha() for c in v)
    has_digit = any(c.isdigit() for c in v)
    return (has_letter and has_digit) or len(v) >= 20

# ---------------------------------------------------------------------------
# Diff handling
# ---------------------------------------------------------------------------

def _iter_added_lines(diff_text):
    """Yield (path, lineno, line) for added lines in a unified diff.

    Deleted files contribute nothing (their removal is checked
    separately). Only the b/ side path is reported.
    """
    path = None
    new_lineno = 0
    in_deleted_file = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            in_deleted_file = False
            path = None
            continue
        if raw.startswith("deleted file mode"):
            in_deleted_file = True
            continue
        if raw.startswith("+++ "):
            p = raw[4:].strip()
            if p == "/dev/null":
                path = None
            else:
                path = p[2:] if p.startswith("b/") else p
            continue
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if m:
            new_lineno = int(m.group(1))
            continue
        if in_deleted_file or path is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            yield path, new_lineno, raw[1:]
            new_lineno += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue  # removed line: does not advance the new-file cursor
        else:
            new_lineno += 1  # context line


def _deleted_test_files(diff_text):
    """Paths of deleted files that look like test files."""
    out = []
    cur = None
    deleted = False
    for raw in diff_text.splitlines():
        m = re.match(r"^diff --git a/(.*) b/(.*)$", raw)
        if m:
            if cur and deleted and _looks_like_test(cur):
                out.append(cur)
            cur, deleted = m.group(1), False
            continue
        if raw.startswith("deleted file mode"):
            deleted = True
    if cur and deleted and _looks_like_test(cur):
        out.append(cur)
    return out


def _looks_like_test(path):
    p = path.lower()
    base = p.rsplit("/", 1)[-1]
    return (base.startswith("test") or base.endswith("_test.py")
            or base.endswith(".test.js") or base.endswith(".test.ts")
            or base.endswith("_test.go") or "/test" in p or "/tests/" in p)


def diff_for_new_file(path, content):
    """Render file content as a synthetic added-file unified diff.

    Lets whole new files (e.g. untracked files at REVIEW -> SHIP) go
    through the same added-line checks as a git diff.
    """
    lines = [f"diff --git a/{path} b/{path}", "new file mode 100644",
             "--- /dev/null", f"+++ b/{path}"]
    body = content.splitlines()
    lines.append(f"@@ -0,0 +1,{len(body)} @@")
    lines.extend("+" + l for l in body)
    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# The four checks
# ---------------------------------------------------------------------------

def check_suppressions(added):
    findings = []
    for path, lineno, line in added:
        for rx in _SUPPRESSION_RES:
            if rx.search(line):
                findings.append(_finding(
                    "suppression-comment", path, lineno,
                    f"new suppression comment: {line.strip()[:80]}"))
                break
    return findings


def check_stubs(added):
    findings = []
    rows = list(added)
    for i, (path, lineno, line) in enumerate(rows):
        for rx in _STUB_RES:
            if rx.search(line):
                findings.append(_finding(
                    "stub", path, lineno,
                    f"unimplemented stub left in new code: {line.strip()[:80]}"))
                break
        # empty catch block: `except ...:` immediately followed by `pass`
        if (_EXCEPT_RE.match(line) and i + 1 < len(rows)
                and rows[i + 1][0] == path
                and _PASS_RE.match(rows[i + 1][2])):
            findings.append(_finding(
                "stub", path, rows[i + 1][1],
                "empty catch block (except: pass)"))
    return findings


def check_skipped_tests(added):
    findings = []
    for path, lineno, line in added:
        for rx in _SKIP_RES:
            if rx.search(line):
                # An inline reason is honored — better than the commit
                # message the floor asks for; an unexplained skip fails.
                if not _REASON_RE.search(line):
                    findings.append(_finding(
                        "skipped-test", path, lineno,
                        f"test skipped without a reason: {line.strip()[:80]}"))
                break
    return findings


def check_secrets(added):
    findings = []
    for path, lineno, line in added:
        m = _SECRET_KEY_RE.search(line)
        if m and _looks_like_secret(m.group(2)):
            findings.append(_finding(
                "secret", path, lineno,
                f"possible secret assigned to {m.group(1)!r}"))
        elif _AWS_KEY_RE.search(line):
            findings.append(_finding(
                "secret", path, lineno, "possible AWS access key ID"))
    return findings


def check_diff(diff_text):
    """Run all four floor checks over a unified diff.

    Returns a list of findings; empty means the floor holds.
    """
    added = list(_iter_added_lines(diff_text))
    findings = []
    findings.extend(check_suppressions(added))
    findings.extend(check_stubs(added))
    findings.extend(check_skipped_tests(added))
    findings.extend(check_secrets(added))
    for path in _deleted_test_files(diff_text):
        findings.append(_finding(
            "deleted-test", path, 0,
            "test file deleted without a recorded reason"))
    return findings


def check_files(files):
    """Run all four floor checks over whole files: [(path, content)].

    Used when there is no diff baseline — every line counts as new.
    Binary files (NUL bytes) are skipped.
    """
    diff_text = ""
    for path, content in files:
        if "\x00" in content:
            continue
        diff_text += diff_for_new_file(path, content)
    return check_diff(diff_text)
