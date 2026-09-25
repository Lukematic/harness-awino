# Definition of Done (standing reference)

A standing, project-wide bar that every change must clear before it counts as done.
Unlike acceptance criteria, which vary per task and answer "did we build the
right thing?", the Definition of Done is the same every time and answers
"is this finished to our standard?"

| | Acceptance Criteria | Definition of Done |
|---|---|---|
| Scope | Specific to one task or mission | Applies to every increment |
| Changes | Different for each item | Fixed and reused |
| Answers | "Did we build *this thing*?" | "Is it *ready*?" |
| Owner | Defined when the mission contract is drafted | Defined once for the project |

The two are complementary. A task is done only when **its** acceptance
criteria are met **and** the standing Definition of Done is satisfied.
Skipping either leaves work that looks finished but is not. This is the
formal form of "only done when production ready."

## The Standing Checklist

### Correctness
- [ ] All acceptance criteria (mission done-criteria) are met
- [ ] Code runs and behaves as intended, verified at runtime — not just
      compiled or typechecked (verify in the real runtime surface)
- [ ] New behavior is covered by tests that fail without the change and pass with it
- [ ] Existing tests still pass; no regressions introduced
- [ ] Edge cases and error paths are handled, not just the happy path

### Quality
- [ ] Code reveals intent through naming and structure
- [ ] No duplicated business logic
- [ ] No dead code, debug output, or commented-out blocks left behind
- [ ] Changes are scoped to the task; no unrelated refactors snuck in
- [ ] The project's lint/type gates pass
- (depth: osmani-code-review — the five-axis review)

### Integration
- [ ] Change works with the rest of the system, not just in isolation
- [ ] Migrations, config changes, and feature flags are accounted for
- [ ] Backward compatibility considered for any public interface change

### Documentation
- [ ] Public interfaces and user-facing behavior are documented
- [ ] Architectural decisions worth preserving are recorded
- [ ] Documentation describes the current state, not the change history

### Ship-readiness
- [ ] Security implications reviewed for any untrusted input, auth, or data
      handling (osmani-security)
- [ ] Rollback path exists for anything risky (osmani-shipping)
- [ ] The human has reviewed and approved before merge or deploy
      (supervised phases; rigor-checkpoint's binary commit gate)

## How to Apply

- **Per task**: Correctness + Quality before the task is checked off (VERIFY/REVIEW).
- **Per feature**: Integration + Documentation before the feature is complete.
- **Per release**: the full checklist is the floor; osmani-shipping adds the
  deploy-specific gates (rollback plan, staged rollout, error-budget) on top.

Tailor the list to the project once, then reuse it unchanged. A Definition
of Done that is renegotiated under deadline pressure is not a Definition
of Done.

## Red Flags

- "It's done, I just haven't run it yet" — unverified work is not done.
- "Tests pass" used as a synonym for done while docs, regressions, or
  runtime verification are skipped.
- A different bar applied depending on deadline pressure.
- Acceptance criteria treated as the whole bar, with no standing quality floor.
- "Done" declared before human review on changes that need it.

---
Attribution: adapted from agent-skills references/definition-of-done.md (MIT, Addy Osmani).
Adapted for Awino: this is a SUPPORTING REFERENCE, not a routed skill — it is
pinned in the skill store for fail-closed integrity but is never floor-routed
into a turn's context. Wired in two places: osmani-constraints (PLAN) defines
the project's bar once at contract approval and points here; osmani-shipping
(SHIP) applies this checklist as the final ship gate. Skill references
rewritten to their Awino ports (osmani-code-review, osmani-security,
osmani-shipping); references to unported source skills (observability-and-
instrumentation, documentation-and-adrs, code-simplification) folded into the
checklist text. "Verified at runtime" kept and strengthened: Awino's bar is
proof in the real runtime, not green checks alone.
