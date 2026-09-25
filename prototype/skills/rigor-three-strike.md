PROCEDURE rigor-three-strike (escape doom loops; cross-phase guardrail):
1. KEEP an iteration ledger per task: attempt number, approach tried, exact result, and whether it is BETTER / SAME / WORSE than the previous attempt.
2. DIVERGENCE DETECTOR after every attempt:
   - errors decreasing -> converging, continue.
   - errors stable -> stalled, try a fundamentally different approach.
   - errors increasing -> diverging, STOP and roll back to last known-good.
   - SAME error 3+ times -> looping: the mental model is wrong, not the code.
   - new unrelated errors appearing -> cascading: your changes are corrupting adjacent systems, STOP.
3. THREE-STRIKE PROTOCOL — same approach (or minor variations) fails 3 times:
   STOP editing. REVERT to the last known-good state. LOG the failure pattern (what was tried, why each attempt failed, root-cause hypothesis). RETHINK a fundamentally different approach — different algorithm or decomposition, not a tweak. RESTART from the clean state.
4. ESCALATION LADDER: second approach also strikes out (6 attempts) -> decompose the task further, re-examine the spec assumptions, check the environment/tooling. Third approach fails (9 attempts) -> flag for human review with the full ledger. Do not continue autonomously past this point.
5. SPIRAL SIGNALS (continuous): editing the same function a 4th time, "just one more tweak" thinking, error messages you have stopped reading carefully, fixes that address symptoms. Any of these -> stop and re-read the ledger before touching code.

Attribution: adapted from agent-rigor 17_recursive_self_correction (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: injected by the harness only when the doom-loop circuit breaker fires; never floor-routed.
