
## Phase B proof (2026-09-22)

- 1. typed contract: TurnContract coerced; plan=('p',), done_claim=False
- 1. type violation rejected: plan must be a list of strings
- 1. immutability enforced (setattr raises)
- 2. revision_history: 2 entries, rev1=1 'First mission', rev2=2 'Second mission' (immutable)
- 3. legal PLAN ok; illegal PLAN->REVIEW refused (transition_refused event seq 3)
- 4. time budget exhausted -> budget_exhausted (terminal)
- 4. budgets(): turns 1/50 (remaining 49), tokens used 1528, seconds remaining 3600
- 5. effect_journal: ['list_dir', 'read_file'] in order; verify_journal ok
- 5. forged duplicate detected: tool_result t9.9 has no tool_called...
- 6. manifest tracks ['proof.txt']; verify ok
- 6. external tamper detected: proof.txt: hash mismatch (modified externally)
