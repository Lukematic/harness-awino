PROCEDURE rigor-proof-cycles (test-driven thin slices; BUILD phase):
1. ORACLE FIRST: from the task spec, write in plain language what should happen: GIVEN input/state, the system does behavior, producing output. Cover one happy path, one edge case, one error case. The oracle comes from the SPEC, never from running the code and copying what it does.
2. RED: write ONE failing test for one behavior (test_<unit>_<scenario>_<expected>). Run it and CONFIRM it fails with a meaningful message. A test that passes without implementation is tautological — investigate.
3. GREEN: write the MINIMUM code to pass. Not clean, not elegant — the simplest thing that passes. Confirm it passes, then run ALL tests and confirm nothing broke.
4. REFACTOR: improve naming/structure/duplication while ALL tests stay green. After every refactor edit, rerun all tests; any failure -> undo the refactor immediately. Refactoring must not change behavior — new behavior goes back to RED.
5. REPEAT per behavior slice. When all slices are done: full verification suite, then rigor-checkpoint for the commit gate.
Without tests, an agent is guessing: plausible code is not correct code. The failing-then-passing test is the only proof the behavior exists.

Attribution: adapted from agent-rigor 05_incremental_proof_cycles (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: phase floor BUILD.
