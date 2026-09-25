PROCEDURE rigor-decomposition (spec -> dependency-ordered vertical slices; PLAN phase):
1. REQUIRE a distilled spec first. Decomposing from a raw request is prohibited — run rigor-distillation first.
2. SLICE VERTICALLY, not horizontally. Each task delivers a working, testable increment end to end (interface to storage, input to output). Test: if a task cannot be demonstrated as working behavior or verified by a test, recut it.
3. SIZE each task to ~30 minutes of focused work including tests and verification. Split when: >5 files touched, multiple independent behaviors bundled, "and" in the title, >3 GIVEN/WHEN/THEN criteria, or creation+migration mixed.
4. MAP every spec acceptance criterion to at least one task. A criterion with zero tasks means a task is missing; a task with no criterion is unjustified — delete or justify it.
5. DECLARE dependencies only when Task B needs a concrete artifact from Task A (file, function, schema, API). Test: "if I deleted all of A's code, would B fail to compile, fail its tests, or be unable to execute?" YES = real dependency; NO = phantom, remove it.
6. MARK the critical path (longest dependency chain). Delays there delay everything.
7. OUTPUT the ordered task list with sizes, dependencies, and criterion mapping into the mission plan. One task active at a time.

Attribution: adapted from agent-rigor 02_strategic_decomposition (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: phase floor PLAN.
