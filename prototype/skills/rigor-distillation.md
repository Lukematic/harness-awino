PROCEDURE rigor-distillation (turn an ambiguous request into a testable spec; DEFINE phase):
1. READ the request twice: first for the literal ask, second for the implied ask (what the user expects but did not say).
2. GENERATE at least three distinct interpretations. For each: one sentence on what the user could mean, one sentence on what would differ in implementation.
3. PROBE implicit requirements across: error handling, platform constraints, performance, backward compatibility, security (inputs/credentials/paths/network), concurrency, observability, data volume, idempotency.
4. SEARCH the codebase for related files, existing patterns, and tests that reveal expected behavior. The codebase is the strongest signal.
5. NAME at least three edge cases the user did not mention, each with a concrete example input and the question "what should happen here?".
6. CONVERGE on one interpretation. Unresolvable ambiguity becomes an explicit Open Question — never a silent guess.
7. DRAFT acceptance criteria as GIVEN/WHEN/THEN with concrete inputs, outputs, or state changes. "It works" is rejected as a criterion.
8. DECLARE scope boundaries: IN SCOPE (deliverables) and OUT OF SCOPE (each with one sentence on why it is excluded).
9. RECORD an assumption registry: each assumption states what is believed, what evidence supports it, and what would falsify it.

Attribution: adapted from agent-rigor 01_requirement_distillation (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: phase floor DEFINE.
