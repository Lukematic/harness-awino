"""High-confidence secret redaction for journaled events and log output.

Scope is deliberately narrow: only secrets with a known, distinctive
shape are redacted. General PII scrubbing of arbitrary prompt/source
content is an arms race and is NOT attempted here.

Covered (each preserves a short identifiable tail for debugging):
  - AWS access key IDs:            AKIA[0-9A-Z]{16}          -> AKIA...<tail>
  - OpenAI-style keys:             sk-<20+ chars>            -> sk-...<tail>
  - OpenRouter keys:               sk-or-v1-<8+ chars>       -> sk-or-v1-...<tail>
  - Anthropic keys:                sk-ant-<8+ chars>         -> sk-ant-...<tail>
  - GitHub tokens:                 ghp_/gho_<20+ chars>,      -> ghp_...<tail>
                                   github_pat_<20+ chars>
  - Bearer tokens in headers:      Bearer <20+ chars>        -> Bearer ...<tail>
  - Slack tokens:                  xoxb-/xoxp-<10+ chars>     -> xoxb-...<tail>
  - Generic assignments:           api_key|secret|token|password (etc.)
                                   followed by `=`/`:` and a high-entropy
                                   value                         -> name=...<tail>

"High-entropy" for the generic case means: length >= 20, OR length >= 10
with at least 3 of 4 character classes (lower/upper/digit/other). A value
that is already redacted (contains "...") is left alone so passes are
idempotent.

Conservative non-goals (documented): user prose such as
"my password is hunter2" (no assignment operator, short low-entropy
value) is left byte-identical. Redacting it would mangle ordinary
sentences ("the password is required"). Only assignment-shaped,
high-entropy values are treated as secrets.

Stdlib only (`re`).
"""
from __future__ import annotations

import re

_TAIL_LEN = 4


def _tail(secret: str) -> str:
    return secret[-_TAIL_LEN:]


def _replace_secret(label: str):
    """Build a re.sub replacement that keeps `label` + tail of the secret.

    The regex must name the secret group "secret". Any text before the
    secret inside the overall match (e.g. "Bearer ") is preserved.
    """
    def _repl(m: "re.Match") -> str:
        secret = m.group("secret")
        s, e = m.span("secret")
        head = m.group(0)[: s - m.start()]
        return f"{head}{label}...{_tail(secret)}"
    return _repl


def _sk_label(secret: str) -> str:
    for prefix in ("sk-or-v1-", "sk-ant-"):
        if secret.startswith(prefix):
            return prefix
    return "sk-"


_SK_RE = re.compile(
    r"\b(?P<secret>sk-(?:or-v1-|ant-)[A-Za-z0-9_\-]{8,}"
    r"|sk-[A-Za-z0-9_\-]{20,})"
)


def _sk_repl(m: "re.Match") -> str:
    secret = m.group("secret")
    return f"{_sk_label(secret)}...{_tail(secret)}"


def _classes(value: str) -> int:
    n = 0
    if re.search(r"[a-z]", value):
        n += 1
    if re.search(r"[A-Z]", value):
        n += 1
    if re.search(r"[0-9]", value):
        n += 1
    if re.search(r"[^A-Za-z0-9]", value):
        n += 1
    return n


def _looks_secret_value(value: str) -> bool:
    """High-confidence gate for generic api_key/secret/token/password values."""
    if "..." in value:  # already redacted: passes are idempotent
        return False
    if len(value) >= 20:
        return True
    return len(value) >= 10 and _classes(value) >= 3


_ASSIGN_RE = re.compile(
    r"(?i)\b(?P<name>api_key|api-key|apikey|secret|token|password|passwd|pwd)"
    r"['\"]?\s*[:=]\s*(?P<q>['\"]?)(?P<secret>[A-Za-z0-9_\-+/.=]{8,})(?P=q)"
)


def _assign_repl(m: "re.Match") -> str:
    secret = m.group("secret")
    if not _looks_secret_value(secret):
        return m.group(0)
    s, e = m.span("secret")
    whole = m.group(0)
    return whole[: s - m.start()] + "..." + _tail(secret) + whole[e - m.start():]


_BEARER_RE = re.compile(
    r"\bBearer\s+(?P<secret>[A-Za-z0-9_\-\.~\+/=]{20,})"
)


def _bearer_repl(m: "re.Match") -> str:
    secret = m.group("secret").rstrip(".")
    return f"Bearer ...{_tail(secret)}"


# (pattern, replacement): specific shapes first; the generic assignment
# rule runs last so it never double-redacts an already-labeled token.
_RULES: list[tuple[re.Pattern, object]] = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     lambda m: f"AKIA...{_tail(m.group(0))}"),
    (re.compile(r"\b(?P<secret>github_pat_[A-Za-z0-9_]{20,})"),
     _replace_secret("github_pat_")),
    (re.compile(r"\b(?P<secret>ghp_[A-Za-z0-9]{20,})"), _replace_secret("ghp_")),
    (re.compile(r"\b(?P<secret>gho_[A-Za-z0-9]{20,})"), _replace_secret("gho_")),
    (_SK_RE, _sk_repl),
    (re.compile(r"\bxox[bp]-[A-Za-z0-9\-]{10,}"),
     lambda m: f"{m.group(0)[:5]}...{_tail(m.group(0))}"),
    (_BEARER_RE, _bearer_repl),
    (_ASSIGN_RE, _assign_repl),
]


def redact_text(text: str) -> str:
    """Redact high-confidence secrets in `text`; leave everything else."""
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


def redact(obj):
    """Recursively redact strings inside dicts/lists/tuples.

    Returns a NEW structure (inputs are never mutated); dict keys are
    left untouched — only values are scanned. Non-string scalars pass
    through unchanged.
    """
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact(v) for v in obj)
    return obj
