"""Deterministic stance triggers and the per-turn triple router.

Every turn the harness computes, independently and in code:
  mode   — permission profile (from input intent, else the floor binding)
  stance — thinking procedure chain (from input intent, else the floor default)
  skills — capability set (from input intent, else the floor binding)

The floor binding table (BUILD_SPEC section 3) gives each elevator floor an
autonomy level (supervised = human approves transitions; bounded = free
action inside the approved contract/scope), router-default stance, skills,
mode (permission profile), and exit gate. Intent patterns override the floor
defaults; when nothing matches, the floor defaults apply. IDLE (no mission)
falls back to a neutral advisor.

Firing a stance = (1) routed by CODE from the user input (never model-chosen),
(2) the full procedure loaded into the contract block, (3) the turn output
evaluated against a deterministic rubric. Rubric FAIL rejects the turn exactly
like a judge FAIL (bounded retries, then escalate).

Honest boundary: rubrics check structure (invocation, required fields,
anti-keyword heuristics) — the enforced part is the invocation, schema, and
evaluation pathway, not mental compliance.
"""
from __future__ import annotations

import re

STOP = {"the", "and", "for", "with", "that", "this", "from", "your", "you",
        "are", "was", "were", "have", "has", "had", "will", "would", "could",
        "what", "when", "where", "which", "there", "their", "they", "them",
        "then", "than", "into", "over", "under", "about", "does", "our", "all"}


def _sig_tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower())
            if len(t) >= 3 and t not in STOP}


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


# ---------------------------------------------------------------------------
# Floor binding table (BUILD_SPEC section 3). Six elevator floors (IDLE is
# pre-mission and unbound). Autonomy: supervised = the human approves each
# transition; bounded = the harness acts freely but only inside the approved
# contract/scope.
# ---------------------------------------------------------------------------
FLOORS = {
    "DEFINE": {
        "autonomy": "supervised",
        "stance": ["planning-grill"],
        # Layered rigor loading: distillation (spec discipline) + laws (Layer 1
        # guardrails). Interrogation stays available via explicit skill_add; the
        # discovery grill covers routine questioning.
        "skills": ["mission-definition", "discovery",
                   "rigor-distillation", "rigor-laws"],
        "mode": "plan",
        "exit": "contract drafted → human approves → PLAN",
    },
    "PLAN": {
        "autonomy": "supervised",
        "stance": ["first-principles"],
        # Rigor: decomposition (strategic task breakdown) + scope (guardrail —
        # declare scope before BUILD touches anything).
        "skills": ["decision-analysis", "domain",
                   "rigor-decomposition", "rigor-scope"],
        "mode": "plan",
        "exit": "contract approved (with SCOPE) → BUILD",
    },
    "BUILD": {
        "autonomy": "bounded",
        "stance": ["first-principles"],
        # Rigor: iteration (Reason-Act-Observe convergent loop) + proof-cycles
        # (TDD: red-green-refactor). three-strike is injected by the harness
        # only when the doom-loop circuit breaker fires — never floor-routed.
        "skills": ["repo", "code",
                   "rigor-iteration", "rigor-proof-cycles"],
        "mode": "build",
        "exit": "diff produced → VERIFY",
    },
    "VERIFY": {
        "autonomy": "bounded",
        "stance": ["devil's-advocate"],
        # Rigor: pentagonal audit (correctness/readability/architecture/
        # security/performance) extends the judge panel's evidence review.
        "skills": ["testing", "rigor-pentagonal-audit"],
        "mode": "verify",
        "exit": "exit code 0 → REVIEW",
    },
    "REVIEW": {
        "autonomy": "bounded",
        "stance": ["premortem"],
        # Rigor: entropy reduction pass before SHIP.
        "skills": ["code-review", "rigor-entropy"],
        "mode": "verify",
        "exit": "no regressions/dead code/side effects → SHIP",
    },
    "SHIP": {
        "autonomy": "supervised",
        "stance": ["premortem"],
        # Rigor: checkpoint (state snapshot + known-good commit before release).
        "skills": ["verification", "rigor-checkpoint"],
        "mode": "ship",
        "exit": "completion claimed only on evidence",
    },
}

# Layered rigor loading on the intent fast-paths: each mission-execution
# intent carries its phase floor's rigor skills (same sets as FLOORS).
# three-strike is never routed here — only the circuit breaker injects it.
_INTENT_RIGOR = {
    "fix": ["rigor-iteration", "rigor-proof-cycles"],  # BUILD floor
    "ship": [],  # settled: the ship fast-path stays ["verification"]
}

# ---------------------------------------------------------------------------
# Canonical intent table: (intent, pattern, mode, stance chain, skills).
# Checked in order; first match wins. Intent overrides the floor defaults.
# ---------------------------------------------------------------------------
INTENT_TABLE = [
    ("opinion",
     re.compile(r"\bi think\b|\bwe should\b"),
     "plan", ["steel-man"], ["domain"]),
    ("teach",
     re.compile(r"\bteach me\b|\bhow does\b|\bhow do\b|\blearn\b"),
     "observe", ["feynman"], ["explainer", "domain"]),
    ("advise",
     re.compile(r"\badvise me\b|\bwhat would you recommend\b"),
     "plan", ["steel-man", "premortem"], ["decision-analysis", "domain"]),
    ("triage",
     re.compile(r"you'?re? (not working|broken|useless|wrong)|"
                r"agent is (bad|broken|not working)|"
                r"\bmisbehaving\b|\bacting weird\b|\byou messed up\b"),
     "plan", ["triage"], ["triage", "domain"]),
    ("fix",
     re.compile(r"\bfix\b|\bbug\b|\bdebug\b|\bpatch\b|\bimplement\b|\brepair\b"),
     "build", ["first-principles"], ["repo", "code"]),
    ("ship",
     re.compile(r"\bship it\b|\bready to ship\b|\blet'?s ship\b|\brelease\b"),
     "ship", ["premortem"], ["verification"]),
]


def route_triple(snapshot: dict, text: str,
                 kind: str) -> tuple[str | None, str, list[str], list[str], str]:
    """Code router for the per-turn triple.

    Returns (intent, mode, stance_chain, skills, trigger_label).
    The backend is never consulted.
    """
    t = (text or "").lower()
    if kind == "new_objective":
        return ("new-task", "plan", ["planning-grill"],
                ["mission-definition", "discovery",
                 "rigor-distillation", "rigor-laws"],
                "new objective")
    for intent, rx, mode, chain, skills in INTENT_TABLE:
        if rx.search(t):
            return (intent, mode, list(chain),
                    list(skills) + _INTENT_RIGOR.get(intent, []),
                    f"intent pattern: {intent}")
    if (not snapshot.get("mission") and kind == "info"
            and len(t.split()) > 8):
        return ("new-task", "plan", ["planning-grill"],
                ["mission-definition", "discovery",
                 "rigor-distillation", "rigor-laws"],
                "raw idea without mission")
    floor = FLOORS.get(snapshot.get("phase") or "")
    if floor:
        return (None, floor["mode"], list(floor["stance"]),
                list(floor["skills"]),
                f"floor default ({snapshot.get('phase')})")
    return (None, "observe", ["advisor"], [], "default")


def route_stance(snapshot: dict, text: str,
                 kind: str) -> tuple[str, str]:
    """Backwards-compatible wrapper: primary stance + trigger label."""
    _intent, _mode, chain, _skills, trigger = route_triple(snapshot, text, kind)
    return (chain[0], trigger)


STANCES = {
    "advisor": {
        "procedure": (
            "PROCEDURE advisor (the default stance — direct helpful answer):\n"
            "1. Answer the user's question directly from the contract's GOAL, "
            "MISSION, and KNOWLEDGE.\n"
            "2. State what you did (progress_delta) in plain words; "
            "cite files you wrote or read.\n"
            "3. If blocked, say what you need — ask it in `questions`, "
            "do not guess.\n"
            "4. Never claim a done criterion is satisfied; the harness "
            "computes that."
        ),
    },
    "verifier": {
        "procedure": (
            "PROCEDURE verifier (Track G — separate worker, never the builder):\n"
            "1. Read the mission's done criteria from the contract.\n"
            "2. For each criterion: name the evidence needed, then check it "
            "exists — a missing proof link is a NO, not a maybe.\n"
            "3. Run the project's test/lint recipe; record name + exit code.\n"
            "4. Every DAG task marked done must have an existing evidence link.\n"
            "5. Confirm no open blockers.\n"
            "6. Journal the verdict in the exact shape: criterion / "
            "needed_evidence / accomplished (yes|no) / proof_link.\n"
            "7. Never grade your own builder work; never claim on prose."
        ),
    },
    "steel-man": {
        "procedure": (
            "PROCEDURE steel-man:\n"
            "1. Restate the user's proposal fairly in your own words.\n"
            "2. Articulate the STRONGEST counter-case: put each counter-argument "
            "as a full sentence in `assumptions` (this field carries the opposition).\n"
            "3. A lone contrast word ('however', 'but') with no substantive "
            "counter-case FAILS the rubric.\n"
            "4. Then give your calibrated take."
        ),
    },
    "feynman": {
        "procedure": (
            "PROCEDURE feynman (all four steps required, in order):\n"
            "1. Everyday ANALOGY, labeled 'Analogy:' in the progress text.\n"
            "2. One targeted GAP QUESTION (in `questions`) that exposes what "
            "the learner does not yet know.\n"
            "3. One concrete EXAMPLE, labeled 'Example:' in the progress text.\n"
            "4. One-sentence teaching SNAPSHOT, labeled 'Snapshot:' in the "
            "progress text.\n"
            "5. No tool calls while teaching."
        ),
    },
    "planning-grill": {
        "procedure": (
            "PROCEDURE planning-grill:\n"
            "1. Ask ONE material question at a time (in `questions`) about scope, "
            "constraints, or acceptance — or advance the draft plan. Exactly "
            "one of the two per turn: never both, never neither.\n"
            "2. A grill turn must either ask a single question or carry a "
            "non-empty plan; idle turns and plan-rush turns FAIL.\n"
            "3. Do NOT call tools in a turn that asks questions.\n"
            "4. While the discovery interview is open, the frontier is mission -> "
            "primary user -> goals -> tenets -> expectations -> success metric; "
            "never present a spec until it is resolved (PLAN_RUSH FAILS)."
        ),
    },
    "first-principles": {
        "procedure": (
            "PROCEDURE first-principles:\n"
            "1. State the hypothesized cause / decomposition in `assumptions` "
            "BEFORE proposing any fix.\n"
            "2. Derive the plan from the cause: each step must address a stated "
            "assumption.\n"
            "3. Never patch blindly — a fix without a stated cause FAILS the rubric."
        ),
    },
    "premortem": {
        "procedure": (
            "PROCEDURE premortem:\n"
            "1. Assume the work has FAILED. Write the failure story: list each "
            "way it could have gone wrong as a full sentence in `assumptions`.\n"
            "2. For the top failure mode, state the tripwire that would have "
            "caught it.\n"
            "3. A plan with no named failure modes FAILS the rubric."
        ),
    },
    "devil's-advocate": {
        "procedure": (
            "PROCEDURE devil's-advocate (on the results):\n"
            "1. Attack the evidence: list every way the results could be "
            "misleading as full sentences in `assumptions`.\n"
            "2. Check specifically: were tests modified to force a pass? Is the "
            "evidence stale? Does it cover the failing case?\n"
            "3. Give the strongest case that the work is NOT done."
        ),
    },
    "triage": {
        "procedure": (
            "PROCEDURE triage (vague complaint → named failure mode):\n"
            "1. Restate the observed misbehavior concretely: what happened vs "
            "what was expected.\n"
            "2. Name the failure mode: pick from the triage skill catalog (or "
            "coin a precise name) and put the diagnosis as a full sentence "
            "in `assumptions`.\n"
            "3. State the falsifier: what observation would prove this "
            "diagnosis wrong.\n"
            "4. Propose the smallest next probe that tests the diagnosis — "
            "not a fix."
        ),
    },
}

KEYWORD_ONLY = re.compile(
    r"^(however|but|on the other hand|although|conversely|yet)[,.\s]*$", re.I)


def _rb_steel_restatement(turn: dict, user_text: str) -> str | None:
    if not (user_text or "").strip():
        return "steel-man: no user proposal to restate"
    turn_text = " ".join([
        turn.get("objective", ""), " ".join(turn.get("plan", [])),
        turn.get("progress_delta", ""), " ".join(turn.get("assumptions", [])),
    ])
    if len(_sig_tokens(user_text) & _sig_tokens(turn_text)) < 2:
        return "steel-man: proposal not restated (fewer than 2 shared terms)"
    return None


def _rb_steel_substance(turn: dict, user_text: str) -> str | None:
    ok = [a for a in turn.get("assumptions", [])
          if len(_words(a)) >= 8 and not KEYWORD_ONLY.match(a.strip())]
    if not ok:
        return ("steel-man: keyword-only opposition — no substantive "
                "counter-case in assumptions")
    return None


def _rb_feynman_steps(turn: dict, user_text: str) -> str | None:
    pd = turn.get("progress_delta", "").lower()
    missing = [lbl for lbl in ("analogy:", "example:", "snapshot:")
               if lbl not in pd]
    if missing:
        return "feynman: missing required steps: " + ", ".join(missing)
    order = [pd.index(lbl) for lbl in ("analogy:", "example:", "snapshot:")]
    if order != sorted(order):
        return "feynman: steps out of order (analogy → example → snapshot)"
    return None


def _rb_feynman_question(turn: dict, user_text: str) -> str | None:
    if not turn.get("questions"):
        return "feynman: no gap question asked"
    return None


def _rb_feynman_no_tools(turn: dict, user_text: str) -> str | None:
    if turn.get("tool_calls"):
        return "feynman: no tool calls while teaching"
    return None


def _rb_grill_advance(turn: dict, user_text: str) -> str | None:
    if not turn.get("questions") and not turn.get("plan"):
        return "planning-grill: turn neither asks a question nor advances a plan"
    return None


def _rb_grill_no_tools_while_asking(turn: dict, user_text: str) -> str | None:
    if turn.get("questions") and turn.get("tool_calls"):
        return "planning-grill: must not act in a turn that asks questions"
    return None


def _rb_grill_one_question(turn: dict, user_text: str) -> str | None:
    qs = turn.get("questions") or []
    if len(qs) > 1:
        return ("planning-grill: ask ONE question at a time "
                f"({len(qs)} asked); later questions must build on settled "
                "answers (QUESTION_DRIP)")
    return None


def _rb_grill_no_plan_while_asking(turn: dict, user_text: str) -> str | None:
    if turn.get("questions") and turn.get("plan"):
        return ("planning-grill: ask a question OR advance the plan in one "
                "turn, not both (PLAN_RUSH: no spec until the interview "
                "frontier is resolved)")
    return None


def _rb_fp_plan(turn: dict, user_text: str) -> str | None:
    if not turn.get("plan"):
        return "first-principles: plan must be derived (non-empty)"
    return None


def _rb_fp_cause(turn: dict, user_text: str) -> str | None:
    if not turn.get("assumptions"):
        return ("first-principles: state the hypothesized cause in "
                "`assumptions` before acting")
    return None


def _rb_premortem_failures(turn: dict, user_text: str) -> str | None:
    ok = [a for a in turn.get("assumptions", []) if len(_words(a)) >= 8]
    if not ok:
        return "premortem: no substantive failure modes named in assumptions"
    return None


def _rb_premortem_plan(turn: dict, user_text: str) -> str | None:
    if not turn.get("plan"):
        return "premortem: plan required (what survives the failure story)"
    return None


def _rb_da_attacks(turn: dict, user_text: str) -> str | None:
    ok = [a for a in turn.get("assumptions", []) if len(_words(a)) >= 8]
    if not ok:
        return ("devil's-advocate: no substantive attack on the results "
                "in assumptions")
    return None


TRIAGE_FAILURE_MODES = (
    "silent misroute",
    "rubric false-positive",
    "approval stall",
    "scope drift",
    "forged completion",
    "tool outside mode",
)


def _triage_text(turn: dict) -> str:
    return " ".join(turn.get("assumptions", []) + [turn.get("progress_delta", "")]).lower()


def _rb_triage_names_mode(turn: dict, user_text: str) -> str | None:
    text = _triage_text(turn)
    named = [m for m in TRIAGE_FAILURE_MODES if m in text]
    substantive = [a for a in turn.get("assumptions", []) if len(_words(a)) >= 8]
    if not named or not substantive:
        return ("triage: name the failure mode (from the triage catalog or a "
                "precise coined name) as a full sentence in assumptions")
    return None


def _rb_triage_falsifier(turn: dict, user_text: str) -> str | None:
    text = _triage_text(turn)
    if "falsif" not in text and "prove" not in text:
        return "triage: state the falsifier — what would prove this diagnosis wrong"
    return None


def _rb_triage_readonly(turn: dict, user_text: str) -> str | None:
    bad = [c.get("name") for c in turn.get("tool_calls", [])
           if c.get("name") not in ("read_file", "list_dir")]
    if bad:
        return f"triage: diagnosis only — no repair tools ({', '.join(bad)})"
    return None


def _rb_advisor_progress(turn: dict, user_text: str) -> str | None:
    if not (turn.get("progress_delta") or "").strip():
        return "advisor: progress_delta must say what was done"
    return None


def _rb_verifier_no_tools(turn: dict, user_text: str) -> str | None:
    """The verifier is read-only: it judges, it never builds."""
    if turn.get("tool_calls"):
        return "verifier: must not call tools (read-only judge)"
    return None


def _rb_verifier_verdict_shape(turn: dict, user_text: str) -> str | None:
    """A verifier turn must carry a per-criterion verdict, not a prose claim."""
    prog = (turn.get("progress_delta") or "").lower()
    if "criterion" not in prog and "verdict" not in prog:
        return ("verifier: progress_delta must carry the per-criterion "
                "verdict (criterion / needed evidence / accomplished)")
    return None


RUBRICS = {
    "steel-man": [_rb_steel_restatement, _rb_steel_substance],
    "feynman": [_rb_feynman_steps, _rb_feynman_question, _rb_feynman_no_tools],
    "planning-grill": [_rb_grill_advance, _rb_grill_no_tools_while_asking,
                       _rb_grill_one_question, _rb_grill_no_plan_while_asking],
    "first-principles": [_rb_fp_plan, _rb_fp_cause],
    "premortem": [_rb_premortem_failures, _rb_premortem_plan],
    "devil's-advocate": [_rb_da_attacks],
    "triage": [_rb_triage_names_mode, _rb_triage_falsifier, _rb_triage_readonly],
    "advisor": [_rb_advisor_progress],
    "verifier": [_rb_verifier_no_tools, _rb_verifier_verdict_shape],
}


def evaluate_stance(stance: str, turn: dict,
                    user_text: str) -> tuple[bool, list[str]]:
    """Run the stance rubric. Returns (passed, failures)."""
    failures: list[str] = []
    for check in RUBRICS.get(stance, []):
        reason = check(turn, user_text or "")
        if reason:
            failures.append(reason)
    return (not failures), failures


def evaluate_chain(chain: list[str], turn: dict,
                   user_text: str) -> tuple[bool, list[str]]:
    """Evaluate every stance in the chain; all must pass."""
    failures: list[str] = []
    for stance in chain:
        ok, f = evaluate_stance(stance, turn, user_text)
        if not ok:
            failures.extend(f"{stance}: {x}" for x in f)
    return (not failures), failures
