# Awino Adapter Contract & Conformance Ladder

Integration standard for every surface that hosts the Awino loop.
Adapted from vscarpenter/claude-code-build-system `docs/harness-compatibility.md` (MIT).

## The portable unit

The portable unit is the **controller protocol**, not the surface. These are
provider-neutral and identical everywhere:

- the mission contract (objective, acceptance criteria, scope, constraints)
- the phase lifecycle (DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP)
- the judge panel and fail-closed gates
- SHA-256-pinned skills and references
- the journal and evidence chain

These are **adapter responsibilities** and may differ per surface:

- instruction discovery, sandbox flags, structured output shape
- authentication and credential storage
- hosted invocation and session persistence

No surface may reimplement the loop. Awino keeps one enforced loop in the
Python sidecar; surfaces are adapters, not harnesses.

## Adapter contract

An Awino surface adapter must:

1. **Probe** its capabilities at startup (tools available, auth state, instruction
   files, skill visibility) and report them honestly; never claim what it has
   not verified.
2. **Accept one bounded work order** per turn through data, never shell
   interpolation or prompt concatenation.
3. **Run inside controller-owned boundaries**: the mission's working directory,
   the mode's tool policy, the turn timeout. It does not widen its own scope.
4. **Receive zero delivery credentials**: no push, publish, merge, or
   customer-facing send capability. Delivery belongs to the controller (the
   human-authorized path), never to the worker or the surface.
5. **Emit exactly one structured result** per turn (contract verdict, tool
   calls, journal entries) in the versioned schema the controller validates.
6. **Treat missing data as unknown** and model text as advisory. A
   model-authored `DONE=true` string has no authority — the controller
   re-validates every claimed path, test result, and completion flag against
   the journal and the filesystem.
7. **Preserve controller-owned state**: the mission branch, HEAD, and journal
   are never rewritten by the adapter.

## Current surfaces

| Surface | Enforcement level | Notes |
|---|---|---|
| `awino chat` (CLI) | Full — no bypass | Reference implementation |
| VS Code extension | Full — loop-owner sidecar | Priority surface |
| Claude Code hook | Gate — blocks off-contract tool calls | Inert until a project opts in via `.awino/` |
| Kilo Code agent | Discipline-grade | Kilo owns the loop; harness tools are advisory |

## Conformance before promotion

A new surface (e.g. the OpenCode plugin) moves up only by passing the gates:

- **Context** — discovery and invocation smoke tests pass.
- **Interactive** — bounded turns complete under the contract with a human
  driving; malformed output and timeout handling proven.
- **Autonomous local** — credential-stripping (no delivery capability reachable),
  protected-path enforcement, verification-result validation, pause/resume, and
  recovery from killed sessions, all against the real loop.
- **Hosted** — a real end-to-end run plus live proof that its automation
  identity cannot bypass human-owned gates (merge, publish, destructive ops).

Skipping a rung is not allowed. "It worked in a demo" is Context-level
evidence, nothing more.

## Precedence

When this contract conflicts with a surface's convenience, the contract wins.
When the contract conflicts with the Charter (human-owned risk boundary), the
Charter wins.
