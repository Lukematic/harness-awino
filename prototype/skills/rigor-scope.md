PROCEDURE rigor-scope (contain scope creep; cross-phase guardrail):
1. DECLARE the scope boundary before coding: "I WILL modify: [specific files/functions]. I WILL NOT modify: [everything else]." Out-of-scope discoveries go to a deferred log, never into this change.
2. TOUCH AUDIT before commit: for EVERY file in the diff, ask: is it in my scope declaration? If not, is the change strictly necessary for the task (import added, signature changed)? If neither -> revert that file immediately.
3. DEFERRED OBSERVATIONS: when you spot something worth fixing outside scope, log it (location, observation, recommended action, priority, reason for deferral). Captured is not lost; it is simply not now.
4. CREEP DETECTOR: the diff keeps growing beyond the declared scope; "while I'm here" reasoning; one side-fix revealing another. Any of these -> stop, re-read the scope declaration, revert out-of-scope changes.
5. REVIEW CONTAMINATION RULE: a diff mixing task changes with unrelated improvements is unreviewable and unrevertable. If the task fails and must roll back, the "bonus" fixes die with it. Keep the diff pure.

Attribution: adapted from agent-rigor 18_scope_containment (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: phase floor PLAN.
