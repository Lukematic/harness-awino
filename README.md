# Harness Awino

A.W.I.N.O. rebuilt as the **automatic per-turn loop owner** — not a CLI the
model may or may not call, but the `while` loop that owns every turn:
contract compiled from code-owned state, mode × stance × skill routed per
turn, permissions gated before execution, a separate judge on every turn,
approvals bound to exact arguments, and completion computed from evidence
(never from claims).

## Contents

- `BUILD_SPEC.md` — the build specification. Authoritative; supersedes
  `ARCHITECTURE.md`.
- `CAPABILITY_REGISTRY.md` — every mode, stance, skill, intent route, and
  floor default, plus the legacy-skill mapping and gaps.
- `AUTHORING_TEMPLATE.md` — the required template and test contract for
  adding a mode, stance, or skill.
- `prototype/` — Phase 0 implementation. Standard library only, mock model
  backends only, no network, no paid APIs.
- `proof/` — `proof_session.py` drives one mission against an adversarial
  model for ten turns and writes `TRANSCRIPT.md`: every attack the loop
  blocked, with the event-log evidence.

## Run it

```sh
cd prototype && ./run_tests.sh        # 95 unit tests: routing, enforcement, judge, approvals, recovery
cd proof && python3 proof_session.py  # adversarial session -> TRANSCRIPT.md (8/8 verdicts)
```

## Status

Phase 0 (deterministic enforcement, mock backends): done and proven.
Phase A (live model backend, human approval interrupt, kill/restart against
a real provider): pending provider, spend ceiling, and explicit authorization.
