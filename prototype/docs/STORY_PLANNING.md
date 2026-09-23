# Story planning: the advisory flow

How a story's approach ("the spine") gets written during PLAN. This is
the planning DNA — enforced in code (`story.py`: `plan_story`,
`expand_bugatti`, `seed_dag_from_story`), not in prompts.

## The approach shape (a)–(f)

`plan_story` enforces this shape AT WRITE TIME — a missing or empty
part raises `ValueError` naming it. The hard BUILD gate stays
deliberately simple (approach non-empty); the discipline lives where
the authoring happens.

- **(a) First-principles breakdown** — the problem reduced to its
  essentials. Written by the architect stance.
- **(b) Existing approaches surveyed** — what already exists, what was
  tried. Written by the researcher stance.
- **(c) User guidance incorporated** — what the user said, and how it
  shaped the plan. The user's original proposal is the starting point;
  the fleshed-out version is co-authored.
- **(d) Proposed approach** — the pitch: A/B/C options + Bugatti in
  BRIEF form, with explicit reasoning for why the pick beats the
  alternatives. Claims carry the contract calibration convention:
  [Certain] / [Likely] / [Guessing]. The Honda — the committed scope,
  what was asked — is the recommendation.
- **(e) Ordered steps** — "if we do X then Y then Z": ordered,
  prioritized steps with dependencies (each step lists earlier step
  indexes; the chain is acyclic by construction). Every step carries
  its own **success AND failure criteria** — fail-fast thinking: we
  know what "worked" looks like and what "kill it now" looks like
  before we start. The DAG seeding consumes these steps: each becomes
  a registry task DAG node preserving order and `depends_on`
  (`seed_dag_from_story`, automatic in `plan_story`, idempotent).
- **(f) Bugatti proposal** — the inventive option, in brief form at
  plan time.

## Multi-stance planning

Planning deliberately cycles three stances instead of collapsing to
one — these are the mode router's own PLAN-affine roles
(`modes.ROLES` phase affinities):

| stance | owns |
|---|---|
| `ai-architect` | (a) the first-principles breakdown |
| `ai-researcher` | (b) existing approaches surveyed |
| `software-engineer` | feasibility check and the pitch (d) |

Each contribution is journaled (`story_plan_stance` breadcrumb in the
registry + event in the mission stream). The user can override the
stance list at any point (`plan_story(..., stances=[...])` — validated
against the real role ids).

## Honda + Bugatti: mandatory planning DNA

Every story plan presents two things:

1. **The Honda** — the committed scope. What was asked, built well.
   This is the recommendation in (d).
2. **The Bugatti proposal** — the inventive option: what we'd do step
   by step, what we'd get.

**Brief-first:** the pitch carries A/B/C options + the Bugatti in
brief form. The harness expands into the full breakdown ONLY when the
user asks (`expand_bugatti`) — never unprompted, never as a
side-effect of planning.

**IRON RULE** (standing user philosophy): the Bugatti is pitched,
never built unasked. It is rendered with every Bugatti proposal, and
`expand_bugatti` writes words, never work.

## Forward-thinking is the Bugatti pitch

There is deliberately NO separate soft-nudge mechanism for
"forward thinking". The enforced behavior IS the mandatory Bugatti
proposal in every plan: each story is forced to articulate the
inventive option, briefly, with the iron rule attached. If the user
wants it, they ask for the expansion; if not, it stays a pitch.

## Parked ideas

Ideas that aren't now-shaped get `status=parked` instead of dying
quietly:

- Each parked idea carries `revisit_on` (YYYY-MM-DD), defaulting to
  30 days out, user-settable via
  `story_update(id, status="parked", revisit_on="2026-10-23")`.
- Parked ideas are excluded from the stale-story rule and the 25-turn
  nudge — being parked is a decision, not abandonment. Their nudge is
  the monthly revisit.
- Session start surfaces parked ideas whose revisit date has arrived:
  "you tabled X — revisit, discard, or keep parked?"
- A parked idea converts to a spike with `convert_parked_to_spike`
  (e.g. "write the white paper", "sponsor discussion prep" — spikes
  with done criteria). The link is preserved both ways
  (`spike.converted_from`, `parked.converted_to`).

## Git lifecycle

See `STORY_GIT_RULES.md`: branch per issue, commit early and often
locally, best-effort push + PR on close, offline-safe, never
published. WINDOWS-safe by construction.
