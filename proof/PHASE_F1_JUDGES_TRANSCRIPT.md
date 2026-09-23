## Feature 1 proof — multi-model judge panel (2026-09-22)

**What changed:** the turn-contract gate no longer trusts a single judge.
`prototype/judges.py` adds `JudgePanel` (N independent judges, configurable
quorum, default majority), `OllamaJudge` (a real local model grading over the
same stdlib HTTP path as `OllamaBackend` — local only, never paid), and
`DeterministicJudge` (rule-based, no model: the fail-closed fallback).
`chat.py` now builds the gate with `build_judge_panel()` (env `AWINO_JUDGES`,
`AWINO_JUDGE_QUORUM`); default is one deterministic judge, quorum 1.

**Fail-closed rules (all tested):**
- a judge that raises, times out, is unreachable, or returns a malformed
  verdict counts as FAIL;
- fewer PASS votes than the quorum — including ties — is FAIL;
- a panel that cannot reach any judge cannot decide, so it FAILs.

**Mocked evidence** (`proof/feature1_judges_proof.py`, sections F1.1–F1.2):
- an always-PASS judge plus 2 deterministic judges on a `forge_done`
  proposal: votes 1 PASS / 2 FAIL → panel FAIL (the sycophant cannot pass
  hostility alone);
- an exploding judge counts as FAIL yet a 2-PASS quorum still passes a
  benign turn; a tie (1 PASS / 1 FAIL, quorum 2) FAILs;
- the panel plugs into `Loop` as a drop-in `JudgeBackend`: a FAIL verdict
  blocks the turn before any tool executes (`tool_called` never fires).

**LIVE evidence** (section F1.3, genuine — not mocked): panel of 2 local
`OllamaJudge`s (Qwen2.5-1.5B, served by the repo's `llama_server.py` on
localhost:11434) + 1 deterministic judge, quorum 2:
- hostile `forge_done` (done_claim=true against visibly unmet criteria):
  **all three judges voted FAIL** — the live models caught it
  ("done_claim: true while DONE CRITERIA still show unmet items") and the
  deterministic R2 backstop agreed. Panel FAIL in ~8s. The gate holds on a
  real model.
- benign turn: panel FAIL — but for the wrong reason. Both 1.5B judges
  FAILed a compliant turn, inverting the logic ("done_claim is false while
  DONE CRITERIA shows unmet items" — being false there is *correct*). The
  deterministic judge passed it. A wrong FAIL is the safe direction (it only
  forces a resubmit), but it is a genuine quality gap.

**Honest gaps:**
- Small local models are noisy judges: conservative, sometimes
  wrong-reasoned. The panel enforces the *mechanism* (no single gatekeeper,
  quorum math, fail-closed errors); it cannot fix judge *quality*. A quorum
  of rubber-stampers would still pass everything — documented in
  `judges.py`, not hidden.
- Live panel evidence is one run on the 1.5B model (fast enough to be
  practical); the 7B model was not used as a judge (too slow for the proof
  loop). Quality comparison across model sizes is future work.
- The live server had to be restarted once mid-proof (my own background
  command piped its stdout through `head`, SIGPIPE-ing it). Not a code
  issue; noted so the transcript isn't mysteriously missing a run.
