PROCEDURE rigor-iteration (Reason-Act-Observe loop; BUILD phase):
1. RECORD baseline: run the full verification suite BEFORE the first edit. Log error count, error types, failing test names. This is iteration zero.
2. STATE your hypothesis in one sentence before touching code: "I believe changing X will fix Y because Z." No written rationale = no edit.
3. ACT once: change <=50 lines in a single coherent edit addressing the hypothesis — nothing else.
4. OBSERVE immediately: run tests, linter, type-checker. Record raw output, new error count and types.
5. EVALUATE against baseline:
   - Converging (fewer/simpler errors) -> continue, update baseline.
   - Stable (same errors) -> increment stagnation counter; at 3, trigger rigor-three-strike.
   - Diverging (more errors or new categories) -> revert the change immediately, re-enter REASON with a different hypothesis.
6. CHECK the iteration budget for the task. Over budget -> STOP and escalate, do not keep patching.
7. REPEAT until all verification passes. Never declare "looks right" from reading code — only from tool output.

Attribution: adapted from agent-rigor 03_convergent_iteration (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: phase floor BUILD.
