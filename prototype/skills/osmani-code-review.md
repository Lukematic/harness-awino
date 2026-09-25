PROCEDURE osmani-code-review (five-axis diff review with severity labels; REVIEW phase):
Every change is reviewed before it advances to SHIP — no exceptions. The review covers five axes; rigor-pentagonal-audit (VERIFY) audits evidence, this skill reviews the diff itself.
1. CONTEXT FIRST: before reading code, state what the change is trying to accomplish, which spec/task it implements, and the expected behavior change.
2. TESTS FIRST: do tests exist? Do they test behavior, not implementation? Edge cases covered? Would they catch a regression? A bugfix without a regression test is incomplete.
3. FIVE AXES, in this order of leverage:
   - CORRECTNESS: matches spec; edge cases (null, empty, boundary) and error paths handled; no off-by-one, races, or state inconsistencies.
   - READABILITY: names descriptive; control flow straightforward; no clever tricks; dead code flagged (unused vars, shims, "// removed" comments); a new conditional bolted onto an unrelated flow is a design smell — push it into its own helper/policy. Repeated conditionals on the same shape signal a missing model/dispatcher.
   - ARCHITECTURE: follows existing patterns; module boundaries clean; no circular deps; refactors must REDUCE complexity, not relocate it (count the concepts a reader must hold — if unchanged, it isn't cleaner); feature logic must not leak into shared modules; prefer deleting an abstraction to polishing it.
   - SECURITY: input validated at boundaries; no secrets in code/logs/history; auth checked; no injection; external data treated as untrusted. See osmani-security for the full discipline.
   - PERFORMANCE: no N+1, no unbounded loops/fetching, pagination on lists, no heavy work in hot paths.
4. STRUCTURAL REMEDIES: never flag a structural problem without proposing the move — replace conditional chains with a dispatcher, collapse duplicate branches, separate orchestration from business logic, extract a helper, split an oversized file.
5. SEVERITY LABELS on every finding: Critical (blocks; security/data-loss/broken), Required (no prefix; must address), Nit (optional), Consider (suggestion), FYI (no action).
6. SIZE: ~100 lines changed is reviewable; ~300 acceptable for one logical change; ~1000 must be split. Separate refactoring from feature work — never both in one change.
7. HONESTY: no rubber-stamp "LGTM"; no softened real issues; quantify when possible ("adds ~50ms per item"); push back on bad approaches — sycophancy is a failure mode. Accept override gracefully: if the author (human or user) has full context and disagrees, defer — comment on code, not people.
8. VERIFY THE VERIFICATION: what tests ran? Build green? Manual verification done? Screenshots for UI changes?
Verdict: Approve (ready to advance), Request changes (Critical/Required outstanding), or Defer with justification. This review PRODUCES findings; rigor-checkpoint's binary gate DECIDES the commit.

Attribution: adapted from agent-skills code-review-and-quality (MIT, Addy Osmani).
Adapted for Awino: slash-command references removed; mapped to the REVIEW phase (the diff review before SHIP). The source's "approve when it definitely improves" standard is kept for review judgment, but the commit/merge bar stays rigor-checkpoint's binary gate — review informs, checkpoint decides. Multi-model review pattern trimmed (Awino's judge panel covers independent review).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all skills into one turn's context.
Routing: phase floor REVIEW.
