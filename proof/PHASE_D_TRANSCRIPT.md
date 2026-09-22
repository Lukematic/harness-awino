
## Phase D proof (2026-09-22)

- 1. worker A cannot write worker B's files (ScopeViolation)
- 1b. worker A can write its own files (a/ok.txt)
- 2. third worker refused (budget exhausted: worker budget exhausted: allocated 4, requested 1, parent limit 4)
- 3. journey PLAN->BUILD->VERIFY->REVIEW->SHIP in order, no skips
- 3b. illegal DEFINE->SHIP jump refused
- 4. collect merged artifacts: ['out/done.txt']
