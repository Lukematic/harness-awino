"""Harness-level verification (Track G): the verifier's deterministic core.

The verifier is a *separate worker* with the verifier stance — never the
builder. This module computes the verdict; the loop journals it on the
WORKER's journal, and only a worker-journaled pass verdict unlocks REVIEW.

Verdict shape (exact — no extra conceptual layers). A list of entries:

    criterion:       the done criterion text, verbatim
    needed_evidence: the concrete evidence this criterion required
    accomplished:    "yes" | "no"
    proof_link:      path to the evidence (must exist on disk)

Checks, in order:
  1. Mission done criteria (project YAML / contract) -> evidence per
     criterion, using the active role's Required Evidence checklist.
  2. Project test/lint recipes through justfile/Makefile (name + exit code).
  3. Every DAG task marked done has an existing evidence link.
  4. No open blockers.

Any "no" -> verdict FAIL: the transition to REVIEW is refused, the mission
routes back to BUILD, and each failed criterion becomes a new DAG task.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def criterion_text(criterion: dict) -> str:
    """Human-readable text for a parsed done-criterion dict."""
    if not isinstance(criterion, dict):
        return str(criterion)
    if criterion.get("text"):
        return str(criterion["text"])
    kind = criterion.get("kind", "")
    if kind == "manual":
        return "manual (operator sign-off)"
    if kind == "artifact_exists":
        return f"artifact exists: {criterion.get('path', '?')}"
    if kind == "event":
        return f"event: {criterion.get('event_type', '?')}"
    return str(criterion)


def find_recipe(project_root: str | Path) -> tuple[str, str] | None:
    """Locate the project's test recipe: (runner, recipe).

    Prefers `just test` when a justfile exists and declares a `test`
    recipe; falls back to `make test`. Returns None when neither exists —
    verification then notes the missing recipe instead of crashing.
    """
    root = Path(project_root)
    for runner, fname in (("just", "justfile"), ("just", "Justfile"),
                          ("make", "Makefile"), ("make", "makefile")):
        p = root / fname
        if not p.is_file():
            continue
        try:
            text = p.read_text()
        except OSError:
            continue
        if _declares_test_recipe(text, runner):
            return runner, "test"
    return None


def _declares_test_recipe(text: str, runner: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if runner == "just":
            if stripped.startswith("test:") or stripped.startswith("test "):
                return True
        else:  # make
            if stripped.startswith("test:"):
                return True
    return False


def run_recipe(project_root: str | Path, runner: str, recipe: str,
               timeout: int = 300) -> dict:
    """Run the recipe; never raises. Returns {runner, recipe, exit_code, output}."""
    try:
        proc = subprocess.run(
            [runner, recipe], cwd=str(project_root), capture_output=True,
            text=True, timeout=timeout)
        out = (proc.stdout + proc.stderr)[-4000:]
        return {"runner": runner, "recipe": recipe,
                "exit_code": proc.returncode, "output": out}
    except FileNotFoundError:
        return {"runner": runner, "recipe": recipe, "exit_code": 127,
                "output": f"{runner} not installed — recipe could not run"}
    except subprocess.TimeoutExpired:
        return {"runner": runner, "recipe": recipe, "exit_code": 124,
                "output": f"recipe timed out after {timeout}s"}
    except OSError as e:
        return {"runner": runner, "recipe": recipe, "exit_code": 126,
                "output": f"could not run recipe: {e}"}


def compute_verdict(criteria: list[str],
                    evidence_links: dict[str, str] | None = None,
                    dag_tasks: list[dict] | None = None,
                    blockers: list[dict] | None = None,
                    recipe_result: dict | None = None,
                    role_evidence: list[str] | None = None,
                    project_root: str | Path = ".") -> dict:
    """Compute the verification verdict. Pure + deterministic.

    criteria: done criterion texts (verbatim).
    evidence_links: {criterion_text: proof_link path}. A link counts only
        when the target exists on disk under project_root.
    dag_tasks: task dicts with id/text/state/evidence.
    blockers: unblock_report() entries.
    recipe_result: run_recipe() output (or a stub in tests).
    role_evidence: the active role's Required Evidence checklist.
    """
    root = Path(project_root)
    evidence_links = evidence_links or {}
    dag_tasks = dag_tasks or []
    blockers = blockers or []
    entries: list[dict] = []

    for crit in criteria:
        link = evidence_links.get(crit, "")
        needed = "; ".join(role_evidence or ["proof that the criterion holds"])
        exists = bool(link) and (root / link).exists()
        entries.append({
            "criterion": crit,
            "needed_evidence": needed,
            "accomplished": "yes" if exists else "no",
            "proof_link": link if exists else "",
        })

    # Every DAG task marked done must have an existing evidence link.
    dag_failures = []
    for t in dag_tasks:
        if t.get("state") != "done":
            continue
        ev = [e for e in (t.get("evidence") or []) if (root / e).exists()]
        if not ev:
            dag_failures.append(t)
            entries.append({
                "criterion": f"DAG task done but unevidenced: {t.get('text', t.get('id'))}",
                "needed_evidence": "an existing evidence link on the done task",
                "accomplished": "no",
                "proof_link": "",
            })

    # The recipe must have run green.
    recipe_ok = True
    if recipe_result is not None:
        recipe_ok = recipe_result.get("exit_code") == 0
        if not recipe_ok:
            entries.append({
                "criterion": (f"project recipe "
                              f"{recipe_result.get('runner')} {recipe_result.get('recipe')} green"),
                "needed_evidence": "recipe exit code 0",
                "accomplished": "no",
                "proof_link": "",
            })

    blocker_open = bool(blockers)
    if blocker_open:
        entries.append({
            "criterion": "no open blockers",
            "needed_evidence": "every DAG task unblocked or resolved",
            "accomplished": "no",
            "proof_link": "",
        })

    passed = (all(e["accomplished"] == "yes" for e in entries)
              and bool(entries))
    return {
        "verdict": entries,
        "passed": passed,
        "dag_failures": [t.get("id") for t in dag_failures],
        "recipe": recipe_result,
    }


def findings_as_tasks(failed_entries: list[dict]) -> list[str]:
    """Turn failed verdict entries into DAG task texts (routed back to BUILD)."""
    out = []
    for e in failed_entries:
        if e.get("accomplished") == "no":
            out.append(f"verification finding — unmet: {e['criterion']} "
                       f"(needed: {e['needed_evidence']})")
    return out
