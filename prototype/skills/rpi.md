PROCEDURE rpi (repeatable multi-file implementation workflow):
The contract loop already owns the turn; rpi is how a turn plans and
lands a change that spans files WITHOUT escaping the machinery — the
file set, the sequence, and the checks all live inside the turn
contract, so every gate the harness enforces still binds.
1. PLAN the FILE SET — name every file the change touches BEFORE any
   write. The file set is the mission SCOPE (set at the DEFINE/PLAN
   floors): no write may land outside the approved SCOPE, and a
   SCOPE_INVALIDATED turn is refused before anything executes. The
   contract's SCOPE gate IS the file-set gate — writing a file with no
   SCOPE entry is a scope violation, not a shortcut.
2. SEQUENCE the changes — order the files by dependency (interfaces
   before callers, migrations before the code that needs them, config
   before the code that reads it). One bounded edit per file; declare
   the sequence in the turn contract's plan so the pre-execute check
   sees the whole order, not just the next write.
3. VERIFY EACH FILE — after each file's edit, re-read the file and run
   its nearest check (its unit tests, lint, or compile) before touching
   the next file. A failing file blocks the sequence: fix it here, do
   not carry a red file forward into the next edit.
4. INTEGRATE — after the last file: run the full relevant suite, review
   the whole diff (cross-file behavior, not per-file), and confirm the
   mission's done criterion with evidence. Completion is computed by the
   harness from journaled evidence — never claimed by the model.
Checklist gates: file set named in SCOPE before the first write;
sequence declared in the contract before edits begin; per-file check
green before the next file; integration evidence journaled before the
turn may end. rpi adds no tools and widens no permissions — it is the
discipline the loop applies when one file is never enough.
