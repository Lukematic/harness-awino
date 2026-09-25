PROCEDURE debug (reproduce -> diagnose -> fix -> verify):
A debugging discipline for the turn — not knowledge about bugs, the
sequence that must run before any diagnosis is trusted.
1. REPRODUCE — never diagnose from a report. Write a failing reproduction
   first (script, test, or command) and capture its failure output.
   Required reproduction evidence: the exact command run + its failing
   output + the version/commit under test. NO DIAGNOSIS WITHOUT
   reproduction evidence — a guess without a repro is a hypothesis, not
   a finding.
2. ISOLATE — bisect toward the smallest failing unit: shrink the input,
   stub the neighbors, flip one variable at a time. If the reproduction
   vanishes while shrinking, the fault was environmental; record exactly
   what changed before blaming the code.
3. DIAGNOSE — state the root cause in one sentence, in this format:
   "ROOT CAUSE: <mechanism> because <trigger>, evidenced by
   <observation>." The mechanism names the code (function/line/path),
   the trigger names the input/state that reaches it, the evidence is
   what the reproduction showed. A cause you cannot point at in the
   code is still a hypothesis — label it and go back to step 2.
4. FIX — change the mechanism, not the symptom. NO FIX WITHOUT a ROOT
   CAUSE sentence: if the fix is larger than the diagnosis, you diagnosed
   the wrong layer. Prefer the fix that makes the reproduction fail
   loudly at the mechanism over the fix that hides the symptom.
5. VERIFY — re-run the step-1 reproduction: it must now pass. Then run
   the adjacent tests the change could disturb — the ones you are most
   afraid of, not the cheapest. NO COMPLETION WITHOUT the reproduction
   passing and the neighbors green, with the evidence journaled.
Never rewrite history to match the fix: the repro was failing before —
prove it was, keep the failing output, then prove it passes now.
