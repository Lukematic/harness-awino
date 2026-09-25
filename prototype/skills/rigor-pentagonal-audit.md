PROCEDURE rigor-pentagonal-audit (structured review before commit-ready; VERIFY phase):
1. GENERATE the diff. The review operates on the diff only — changed lines plus ~10 lines of context. Unrelated issues found elsewhere are logged as backlog, never fixed here (see rigor-scope).
2. WALK all five axes. Every axis gets a concrete finding with severity or an explicit "No issues found" — no axis skipped, none left blank:
   - CORRECTNESS: edge cases, boundaries, null/empty inputs, error propagation, off-by-one, race conditions, type coercion. Does the code do what it claims under ALL conditions?
   - READABILITY: names describe purpose, linear control flow, comments explain WHY not what, no magic values, consistent style. Could a stranger understand it in 60 seconds?
   - ARCHITECTURE: module boundaries respected, dependency direction correct, no cycles, single responsibility, established patterns followed, minimal public surface.
   - SECURITY: inputs validated, no hardcoded secrets, no injection (SQL/command/path), auth checks present, no sensitive data in logs or errors.
   - PERFORMANCE: no N+1 patterns, no nested loops over unbounded data, right data structures, no wasteful allocation in hot paths.
3. CLASSIFY every finding: CRITICAL (breaks correctness, introduces vulnerability, violates an invariant — MUST fix before commit), WARNING (degrades quality — SHOULD fix or document why deferred), INFO (optional).
4. FIX all CRITICALs, then SECOND-PASS: re-audit the fix itself. A fix introducing a new CRITICAL is not a fix. More than 3 second-pass cycles -> the change is fundamentally flawed: revert and redesign.
5. FRESH EYES: review as a stranger's code. Assume mistakes. Hunt flaws; do not confirm correctness.
6. VERDICT: PASS only when all five axes are addressed and zero CRITICALs remain open. Record the audit (axis, finding, severity, action) in the mission journal.
Rubber-stamp signal: a non-trivial diff (>20 lines) with zero findings across all axes is statistically implausible — re-run adversarially.

Attribution: adapted from agent-rigor 06_pentagonal_audit (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: phase floor VERIFY.
