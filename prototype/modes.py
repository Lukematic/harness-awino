"""Intelligent role modes (Track D): data, not new loops.

A role mode is a *lens* over the existing six-phase loop (IDLE/DEFINE/PLAN/
BUILD/VERIFY/REVIEW/SHIP). It changes what the harness routes into the
contract (skill bodies, evidence checklists, decomposition playbooks) — it
never changes the offered tools or the permission model. The floor mode
(observe/plan/build/verify/ship) remains the sole owner of tools and gates.

Roles
-----
  software-engineer         — spec-first, contract-first, vertical slices.
  ai-researcher             — hypothesis -> experiment -> evidence -> conclusion.
  ai-architect              — constraints -> options -> decision matrix -> ADR.
  forward-deployed-engineer — discover -> thin slice -> deploy -> iterate.
  cybersecurity-engineer    — asset -> threat -> mitigation -> verify.

The deterministic router proposes a role from the mission text, the current
phase, and registry context. It re-evaluates at mission start and at phase
boundaries; mid-mission signals (secrets, experiments, results) can trigger
a re-route. The proposal is surfaced in the contract with its reason, and
the user can always override. No model call is involved — this is honest
pattern matching, not magic.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROLE_IDS = (
    "software-engineer",
    "ai-researcher",
    "ai-architect",
    "forward-deployed-engineer",
    "cybersecurity-engineer",
)
DEFAULT_ROLE = "software-engineer"


# ---------------------------------------------------------------------------
# Role profiles. Each role carries everything the harness needs to route it
# into the contract and to seed the mission's task DAG.
# ---------------------------------------------------------------------------
ROLES: dict[str, dict] = {
    "software-engineer": {
        "perspective": (
            "Ship working software in vertical slices. The spec is the "
            "contract; the tests are the proof. SOLID is a guide, not a "
            "religion — simplicity wins ties."),
        "use_during": [
            "building features, fixing bugs, refactoring",
            "any mission whose done criteria mention code, tests, or behavior",
        ],
        "red_flags": [
            "writing code before the done criteria are written",
            "big-bang changes instead of small verifiable slices",
            "tests added after the fact to bless existing behavior",
            "dependencies added without a pinned version",
        ],
        "required_evidence": [
            "every done criterion has a passing test that fails without the change",
            "lint clean (ruff), tests green via the project recipe",
            "vertical slice demo: user-visible behavior, not internal plumbing",
        ],
        "skills_to_load": ["code", "testing", "repo"],
        "decision_rule": (
            "When uncertain, choose the smallest change that makes a "
            "done criterion verifiably true."),
        "close_out": (
            "Run the full recipe, demo the slice, record what was learned. "
            "Leave the tree greener than you found it."),
        "phase_affinities": ["PLAN", "BUILD", "VERIFY"],
        "toolchain": ["python", "ruff", "pytest", "just", "git"],
        "decomposition_playbook": [
            "Write the spec: restate the mission as checkable done criteria",
            "Choose the smallest vertical slice that proves the idea",
            "Write the failing test for the slice",
            "Implement the slice (contract-first, SOLID where it pays)",
            "Run lint + tests; fix until green",
            "Demo the slice; record learnings; plan the next slice",
        ],
    },
    "ai-researcher": {
        "perspective": (
            "Knowledge is earned: hypothesis -> experiment -> evidence -> "
            "conclusion. Every claim gets an assumption rating; "
            "contradictions are hunted, not hidden. Reproducibility is the "
            "product."),
        "use_during": [
            "experiments, evaluations, measurements, literature review",
            "missions whose done criteria mention results, benchmarks, or findings",
        ],
        "red_flags": [
            "conclusions drawn from a single unlogged run",
            "dependencies unpinned — results nobody can reproduce",
            "cherry-picked examples presented as evidence",
            "assumptions never written down, so never challenged",
        ],
        "required_evidence": [
            "hypothesis written BEFORE the experiment ran",
            "pinned dependencies (exact versions) and seeds recorded",
            "run log for every experiment: command, seed, result",
            "contradiction check: what evidence would disprove the claim?",
            "citation chain: every borrowed claim traces to a source",
        ],
        "skills_to_load": ["decision-analysis", "domain"],
        "decision_rule": (
            "No claim without evidence; no evidence without a logged run. "
            "When two results disagree, rerun — never average away the "
            "contradiction."),
        "close_out": (
            "Write the conclusion as: hypothesis, method, evidence, "
            "assumption ratings, contradictions found, what remains open."),
        "phase_affinities": ["DEFINE", "PLAN", "VERIFY"],
        "toolchain": ["python", "pinned requirements.txt", "run logs"],
        "decomposition_playbook": [
            "Frame the hypothesis and its assumption ratings",
            "Design the experiment: what changes, what is measured, what disproves it",
            "Pin dependencies and seeds; set up experiments/ run logs",
            "Run the experiment; log every run, including failures",
            "Analyze: conclusions, contradictions, confidence levels",
            "Write up: method + evidence + citations so anyone can rerun",
        ],
    },
    "ai-architect": {
        "perspective": (
            "Decisions are the deliverable. Constraints first, options next, "
            "a decision matrix, then an ADR that records why — including the "
            "options rejected and why. Architecture is risk management with "
            "a long memory."),
        "use_during": [
            "system design, build-vs-buy, technology selection",
            "missions with competing constraints (cost, latency, reliability)",
        ],
        "red_flags": [
            "choosing a stack before the constraints are written",
            "a decision with no recorded alternatives",
            "optimizing for resume value instead of the constraint set",
            "ignoring cost/latency until after the design is 'done'",
        ],
        "required_evidence": [
            "constraints written and ranked before any option is scored",
            "decision matrix: options x constraints with scores and weights",
            "an ADR per decision: context, options, decision, consequences",
            "build-vs-buy analysis with real cost/latency numbers",
            "rejected options documented with reasons",
        ],
        "skills_to_load": ["decision-analysis", "repo", "domain"],
        "decision_rule": (
            "Score options against the written constraints, not against "
            "each other. The matrix decides; taste advises."),
        "close_out": (
            "Ship the ADRs, not just the diagram. Future-you must be able "
            "to replay every decision without asking present-you."),
        "phase_affinities": ["DEFINE", "PLAN", "REVIEW"],
        "toolchain": ["docs/adr/", "decision matrices", "cost models"],
        "decomposition_playbook": [
            "Elicit and rank the constraints (functional + non-functional)",
            "Enumerate candidate options (include build AND buy)",
            "Score the decision matrix against constraints",
            "Write the ADR: context, options, decision, consequences",
            "Prototype the riskiest assumption, not the whole design",
            "Review: does the matrix still hold with what we learned?",
        ],
    },
    "forward-deployed-engineer": {
        "perspective": (
            "The customer has the problem today. Discover fast, ship a thin "
            "slice, watch it in the wild, iterate with the stakeholder in the "
            "loop. Runbooks over heroics; handoff over dependency."),
        "use_during": [
            "deployments, integrations, customer-facing delivery",
            "missions that end with someone else operating the result",
        ],
        "red_flags": [
            "shipping without a runbook the stakeholder can follow alone",
            "no rollback path for the thin slice",
            "debugging by guessing instead of the sequence: logs -> repro -> isolate",
            "stakeholder learns about failures from the system, not from us",
        ],
        "required_evidence": [
            "thin slice deployed and exercised end-to-end in the target env",
            "runbook: deploy, operate, roll back — each step checkable",
            "debugging log: symptom -> hypothesis -> test -> result",
            "stakeholder handoff note: what was delivered, how to run it",
        ],
        "skills_to_load": ["repo", "code", "triage"],
        "decision_rule": (
            "The thinnest slice that creates real feedback wins over the "
            "complete solution nobody has touched."),
        "close_out": (
            "Hand off, don't hand over: the stakeholder can run it, fix "
            "the common failures, and knows when to call."),
        "phase_affinities": ["BUILD", "VERIFY", "SHIP"],
        "toolchain": ["adapters", "runbooks", "deployment scripts"],
        "decomposition_playbook": [
            "Discover: stakeholder interview — problem, constraints, success signal",
            "Define the thinnest slice that produces real feedback",
            "Build the slice with its runbook (deploy + rollback)",
            "Deploy to the target environment; watch it run",
            "Debug in the open: logs -> repro -> isolate -> fix, all logged",
            "Hand off: stakeholder runs it solo while you watch",
        ],
    },
    "cybersecurity-engineer": {
        "perspective": (
            "Assume breach. Asset -> threat -> mitigation -> verify. Default "
            "deny everything not explicitly allowed; secrets never touch "
            "logs, prompts, or repos; every input is adversarial until "
            "proven otherwise."),
        "use_during": [
            "anything touching secrets, credentials, auth, or network policy",
            "threat modeling, hardening, incident response",
        ],
        "red_flags": [
            "a secret in a prompt, log, file, or chat transcript",
            "trusting input because it came from 'our' tool or page",
            "a mitigation with no verification step",
            "permissions wider than the task requires 'for convenience'",
        ],
        "required_evidence": [
            "asset inventory for the mission scope",
            "STRIDE-lite threat model: spoofing, tampering, repudiation, "
            "info disclosure, denial of service, elevation of privilege",
            "each threat has a mitigation AND a verification step",
            "secrets audit: no secret material in logs, files, or journal",
            "adversarial input tests for every trust boundary crossed",
        ],
        "skills_to_load": ["triage", "code", "repo"],
        "decision_rule": (
            "Deny by default; every permission granted is explicit, minimal, "
            "and logged. If verification can't be demonstrated, the "
            "mitigation doesn't exist."),
        "close_out": (
            "Re-run the threat model against the shipped state. Any new "
            "threat becomes a tracked task, not a footnote."),
        "phase_affinities": ["PLAN", "BUILD", "VERIFY"],
        "toolchain": ["threat-model template", "secret scanners", "adversarial tests"],
        "decomposition_playbook": [
            "Inventory assets in scope; draw the trust boundaries",
            "STRIDE-lite: enumerate threats per boundary",
            "Rank by impact x likelihood; assign mitigations",
            "Implement mitigations with default-deny posture",
            "Verify: adversarial inputs, secrets audit, permission review",
            "Record residual risks as tracked tasks",
        ],
    },
}

# Skill body names for the five role lenses (pinned in skills/manifest.json).
ROLE_SKILL_NAMES = {r: f"mode-{r}" for r in ROLE_IDS}


# ---------------------------------------------------------------------------
# Deterministic router. Pure function: mission text + phase + registry
# context -> (role, reason). No model involved.
# ---------------------------------------------------------------------------
_SECURITY_RX = re.compile(
    r"\b(secret|secrets|credential|credentials|token|password|passwd|api[-_ ]?key|"
    r"auth|authentication|authorization|oauth|jwt|encrypt|decrypt|tls|ssl|"
    r"vulnerab|exploit|cve|attack|threat|malware|phish|injection|xss|csrf|"
    r"sanitize|harden|breach|pentest|security audit|2fa|mfa)\b", re.I)
_RESEARCH_RX = re.compile(
    r"\b(experiment|experiments|hypothes|hypotheses|hypothesis|benchmark|"
    r"evaluation|evaluat|measurement|dataset|run log|ablation|statistical|"
    r"significance|p-value|reproducib|literature|paper|papers|survey of|"
    r"results|findings)\b", re.I)
_ARCHITECT_RX = re.compile(
    r"\b(architect|architecture|adr|decision matrix|trade-?off|tradeoffs|"
    r"build[- ]vs[- ]buy|constraints|cost[/-]latency|scalab|system design|"
    r"technology selection|choose (a |the )?stack|design doc)\b", re.I)
_FDE_RX = re.compile(
    r"\b(deploy|deployment|production|stakeholder|customer|runbook|onboard|"
    r"on-call|incident|rollback|release|go[- ]live|handoff|site reliability|"
    r"operate|operations)\b", re.I)
_CODE_RX = re.compile(
    r"\b(code|refactor|bug|feature|function|module|class|test|tests|pytest|"
    r"implement|rewrite|ship (it|this)|app|cli|api endpoint)\b", re.I)

# Mid-mission signals that force a re-route even when the phase affinity
# would otherwise keep the current role.
_TRIGGER_SECURITY_RX = re.compile(
    r"\b(secret|secrets|credential|credentials|token|password|api[-_ ]?key|"
    r"private key|\.pem\b|ssh key|auth header)\b", re.I)
_TRIGGER_RESEARCH_RX = re.compile(
    r"\b(experiments?|benchmarks?|hypothes[ei]s|results are in|data shows|"
    r"measured?|evaluation|findings)\b", re.I)


def _score(text: str) -> dict[str, int]:
    """Count signal hits per role. Deterministic, order-independent."""
    return {
        "cybersecurity-engineer": len(_SECURITY_RX.findall(text or "")),
        "ai-researcher": len(_RESEARCH_RX.findall(text or "")),
        "ai-architect": len(_ARCHITECT_RX.findall(text or "")),
        "forward-deployed-engineer": len(_FDE_RX.findall(text or "")),
        "software-engineer": len(_CODE_RX.findall(text or "")),
    }


def propose_role(mission_text: str = "", phase: str = "",
                 registry_context: str = "", configured_profile: str | None = None,
                 current_role: str | None = None) -> dict:
    """Propose a role mode. Returns {role, reason, source}.

    Sources: "project_yaml" (configured profile wins ties/weak signals),
    "router" (text signals), "phase_affinity" (phase favors a role when the
    text is ambiguous). A mid-mission security or research trigger overrides
    everything except an explicit user override.
    """
    text = " ".join(filter(None, [mission_text, registry_context]))
    scores = _score(text)

    # Mid-mission triggers: secrets and experiments always re-route.
    if _TRIGGER_SECURITY_RX.search(text or ""):
        return {"role": "cybersecurity-engineer",
                "reason": ("security signal in mission/context "
                           "(secret/credential/token) — asset→threat→mitigation→verify lens required"),
                "source": "trigger"}
    if _TRIGGER_RESEARCH_RX.search(text or ""):
        return {"role": "ai-researcher",
                "reason": ("research signal in mission/context "
                           "(experiment/results/data) — hypothesis→experiment→evidence→conclusion lens required"),
                "source": "trigger"}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_role, top_hits = ranked[0]

    if top_hits == 0:
        # No signal: fall back to the configured profile, then the default.
        role = configured_profile if configured_profile in ROLE_IDS else DEFAULT_ROLE
        return {"role": role,
                "reason": ("no role signals in the mission text — using the "
                           f"configured profile ({role})"),
                "source": "project_yaml" if configured_profile in ROLE_IDS
                else "default"}

    # Phase affinity: at a boundary the phase can disambiguate a weak
    # single-hit signal; it never overrules a strong multi-signal.
    if top_hits == 1 and phase:
        for rid in ROLE_IDS:
            if scores.get(rid, 0) == 0 and phase in ROLES[rid]["phase_affinities"]:
                # keep the top signal unless the current role is a better
                # phase fit — affinity is advisory only.
                break
        if current_role and current_role in ROLE_IDS and phase in ROLES[current_role]["phase_affinities"]:
            return {"role": current_role,
                    "reason": (f"mission text is ambiguous (1 signal: {top_role}); "
                               f"keeping {current_role} — it fits the {phase} phase"),
                    "source": "phase_affinity"}

    if configured_profile in ROLE_IDS and top_hits == 1 and top_role != configured_profile:
        # weak single signal loses to the user's configured profile
        return {"role": configured_profile,
                "reason": (f"only a weak signal for {top_role} (1 hit); the "
                           f"configured profile {configured_profile} wins ties"),
                "source": "project_yaml"}

    what = ", ".join(f"{r}:{scores[r]}" for r in ROLE_IDS if scores[r])
    return {"role": top_role,
            "reason": f"mission signals matched {top_role} (signal counts: {what})",
            "source": "router"}


def role_skill_names(role: str) -> list[str]:
    """Skills the harness loads when this role lens is active."""
    if role not in ROLES:
        return []
    base = list(ROLES[role]["skills_to_load"])
    lens = ROLE_SKILL_NAMES[role]
    return [lens] + base


def role_contract_section(role: str, reason: str,
                          offered_before: list[str],
                          offered_after: list[str]) -> str:
    """Contract block for the active role lens.

    The proof line: offered tools are identical before and after the lens is
    applied — a role is a lens, never a permission expansion.
    """
    if role not in ROLES:
        return ""
    r = ROLES[role]
    unchanged = offered_before == offered_after
    lines = [
        "## ROLE MODE (lens only — routed by harness code, user-overridable)",
        f"active role: {role}",
        f"why: {reason}",
        f"perspective: {r['perspective']}",
        f"decision rule: {r['decision_rule']}",
        f"required evidence: {'; '.join(r['required_evidence'])}",
        "permissions: UNCHANGED — the role lens adds zero tools. "
        f"(offered tools identical before/after: {unchanged})",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Role state mirror: .awino/role.json — the loop journals every switch, and
# mirrors the current role here so `awino status`/`awino plan` work without
# a running loop.
# ---------------------------------------------------------------------------
def read_role_state(awino_dir: str | Path) -> dict:
    """Read the mirrored role state. Never raises; {} when absent."""
    try:
        p = Path(awino_dir) / "role.json"
        data = json.loads(p.read_text())
        if isinstance(data, dict) and data.get("role") in ROLE_IDS:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def write_role_state(awino_dir: str | Path, role: str, reason: str,
                     source: str, phase: str = "") -> dict:
    """Mirror the active role to .awino/role.json. Never raises."""
    try:
        Path(awino_dir).mkdir(parents=True, exist_ok=True)
        state = {"role": role, "reason": reason, "source": source,
                 "phase": phase}
        (Path(awino_dir) / "role.json").write_text(json.dumps(state, indent=2))
        return state
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Decomposition: seed the mission's task DAG from the active role's playbook
# (Track F). Returns the created task dicts in playbook order.
# ---------------------------------------------------------------------------
def compile_initial_dag(registry, mission_text: str, role: str,
                        mission_id: str = "") -> list[dict]:
    """Create the initial task DAG for a mission from the role's playbook.

    Steps chain in order (each depends on the previous), so the DAG is a
    valid topological sequence from the start. Idempotent per mission: if
    the mission already has tasks, nothing is created.
    """
    if role not in ROLES:
        role = DEFAULT_ROLE
    # Idempotent: if the registry already holds DAG-compiled tasks, skip.
    try:
        if any(t.get("source") == "dag:initial" for t in registry.tasks()):
            return []
    except Exception:
        return []
    created: list[dict] = []
    prev_id: str | None = None
    for step in ROLES[role]["decomposition_playbook"]:
        task = registry.add_task(
            f"{step} — {mission_text[:60]}".rstrip(),
            source="dag:initial",
            depends_on=[prev_id] if prev_id else [],
            done_criteria=step,
            mission_id=mission_id,
        )
        created.append(task)
        prev_id = task["id"]
    return created
