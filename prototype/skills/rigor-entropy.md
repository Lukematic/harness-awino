PROCEDURE rigor-entropy (simplify without breaking; VERIFY phase):
Governing laws: PRESERVED INTENT (never remove what you cannot explain — articulate why the code exists first, via history/blame/tests), IMPLICIT DEPENDENCY (every observable behavior may have dependents — changing incidental behavior is a breaking change until proven otherwise), ENTROPY GROWS (codebases never simplify themselves; reduction takes deliberate effort).
1. TARGET with a reason: a complexity-budget violation, a readability complaint, or a bug in the area. Record baseline metrics first: cyclomatic complexity, line count, nesting depth.
2. COMPLEXITY BUDGET (violations are CRITICAL): function <=40 lines, cyclomatic complexity <=10, nesting depth <=3, parameters <=4 (else a config object), class <=7 public methods, file <=300 lines.
3. CHARACTERIZE before cutting: write characterization tests capturing CURRENT behavior — branches, edge cases, error paths, suspected-incidental behavior. These prove the simplification preserves behavior.
4. SIMPLIFY in small steps, running characterization tests after each. Any behavior change -> STOP: either the characterization was wrong (fix the test and justify) or the simplification is wrong (revert).
5. VERIFY: metrics improved, all tests green, no behavior delta. Record before/after metrics in the journal.

Attribution: adapted from agent-rigor 07_entropy_reduction (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: phase floor REVIEW.
