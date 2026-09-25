# Sandbox run: agentic-learning review mission

Workspace: `/tmp/agentic-learning-2aq0sceo`

| Check | Result |
|---|---|
| Interview (planning-grill) fired | PASS |
| Model's set_mission moved IDLE -> DEFINE | PASS |
| Challenge fired (steel-man + premortem) | PASS |
| Stance rubrics ran every turn (12 passed, 0 rejected) | PASS |
| story_plan wrote the Honda-first plan (3 stances) | PASS |
| Stretch goal parked (NABC) | PASS |
| Writes paused for approval | PASS |
| BUILD -> VERIFY on write | PASS |
| Failing check routed back to BUILD | PASS |
| Devil's-advocate on VERIFY | PASS |
| Independent verifier passed | PASS |
| Evidence-gated completion -> SHIP | PASS |
| Story closed onto the brag board | PASS |
| Journal chain intact | PASS |

Phases: IDLE -> DEFINE -> PLAN -> BUILD -> VERIFY -> BUILD -> VERIFY -> REVIEW -> SHIP
Modes routed: plan, build, verify, ship
Stances fired: planning-grill, steel-man, premortem, first-principles, devil's-advocate
check_review.py exit codes: [1, 0]
Journal problems: none

## Final review.md

# Agentic learning: seven approaches

Sources are the original papers (arXiv). Years are first versions.

| Approach | Year | Core idea | Source | How we'd test it |
|---|---|---|---|---|
| ReAct | 2022 | Interleave reasoning traces with tool actions | https://arxiv.org/abs/2210.03629 | Same 20 web tasks, success rate |
| Reflexion | 2023 | Verbal self-critique stored as episodic memory | https://arxiv.org/abs/2303.11366 | Retry budget of 3, gain per retry |
| Voyager | 2023 | Growing skill library + automatic curriculum | https://arxiv.org/abs/2305.16291 | Skills reused across 5 task families |
| MemGPT | 2023 | Tiered memory the agent pages in and out | https://arxiv.org/abs/2310.08560 | Long-horizon tasks past the context window |
| Multi-agent debate | 2023 | Several agents argue, then converge | https://arxiv.org/abs/2305.14325 | Accuracy vs single agent at equal tokens |
| SPIN (self-play fine-tuning) | 2024 | Model improves against its own past outputs | https://arxiv.org/abs/2401.01335 | Win rate vs base after 2 rounds |
| RL with verifiable rewards (GRPO, DeepSeek-R1) | 2025 | RL on checkable outcomes, no reward model | https://arxiv.org/abs/2501.12948 | Pass@1 on unit-tested coding tasks |
