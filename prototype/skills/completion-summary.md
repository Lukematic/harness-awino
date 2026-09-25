# Completion Summary (standing reference)

Every mission, fan-out worker result, and significant operation ends with a
completion summary in this shape. It is the operational form of "proof, not
claims": the reader sees what was done, what it was checked against, and what
remains — without reading the whole transcript.

## The shape

```
## Summary
- Operation: <build | fix | review | ship | analyze | incident>
- Artifact or mission: <what this was>
- Scope: <mission id / files / environment>

## What I confirmed
- Scope: <what was in scope, what was explicitly out>
- Dependencies: <what it relied on, verified present — or "none">
- Source of truth: <the effect journal, the turn contract, the test run —
  never the model's memory of what it did>

## What changed
- Created/updated/fixed: <identifiers, commits, artifacts>
- Key behaviors: <the decisions that matter>

## Risks or follow-ups
- <what could still go wrong, what was deferred, or "none">

## Recommended next step
- <the single most useful thing to do next>
```

## Failure variant

If the operation failed, replace "What changed" with "What blocked completion":

```
## What blocked completion
- Blocker: <the specific blocker, with evidence>
- What did complete: <partial results, stated explicitly — never silently dropped>
- What unblocks it: <who or what is needed>
```

## Notes

- Keep the summary short and operational. No payload dumps: never repeat the
  whole diff, log, or transcript unless asked.
- The source of truth for every claim is the journal or the contract, never
  the summary itself. A summary that cannot point at journal events is a claim.
- Fan-out workers: every worker result is a completion summary in this shape.
  The synthesizer merges summaries, not raw transcripts — this is what keeps
  the fan-out's context bounded as workers multiply.
- Wired in: the lifecycle-sequence mission-close (every track ends here);
  osmani-shipping's final gate (no ship claim without one).

---
Source: harness-skills (Apache-2.0, Harness)
Adapted for Awino: SUPPORTING REFERENCE, not a routed skill — pinned in the
skill store for fail-closed integrity, never floor-routed into a turn's
context. The source's Harness resource types (pipeline, connector, secret)
are replaced with Awino's (mission, artifact, turn contract); "schema/source
of truth" becomes the effect journal and the turn contract — the two records
in Awino that cannot be hallucinated. The failure variant is kept and
strengthened: partial results must be stated explicitly, never silently
dropped. Added the fan-out worker note: worker results merge as summaries,
which bounds the synthesizer's context.
