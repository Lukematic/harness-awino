PROCEDURE rigor-checkpoint (version control as a state machine; BUILD/SHIP):
Core invariant: HEAD always points to a verified, passing state. Work-in-progress lives ONLY in the working tree, never in history.
1. PRE-TASK: verify a clean working tree. Dirty tree from a previous task -> commit it if verified, revert it if not. Record the rollback target (git rev-parse HEAD). For experimental/uncertain work, branch first (experiment/<task-id>).
2. PRE-CONDITIONS: run the full verification suite before starting. Building on a broken foundation is prohibited — fix or report pre-existing failures first.
3. DURING: work on ONE task only. Unrelated issues discovered mid-task are logged for later, never fixed in this tree. No intermediate commits; stash WIP if you must park it.
4. BINARY GATE: run FULL verification (tests, linter, type-checker). PASS -> commit. FAIL -> hard revert (git reset --hard HEAD && git clean -fd) and return to rigor-iteration. There is no "mostly working" commit.
5. COMMIT atomically: one task, one commit, prescribed format "<type>(<scope>): <TASK-ID> <description>". Push so the work survives this machine.
6. POST-COMMIT: working tree clean, HEAD verified. The next task starts from a known-good state.

Attribution: adapted from agent-rigor 04_state_checkpoint_protocol (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: phase floor SHIP.
