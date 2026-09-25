# DORA Metrics (standing reference)

The four DORA metrics measure software delivery performance: how fast the
team ships, and how reliably. They are the objective form of "mission-driven
and measurable" applied to the delivery system itself — the numbers that say
whether the engineering process is getting better or worse.

## The four metrics

1. DEPLOYMENT FREQUENCY — how often changes reach production.
2. LEAD TIME FOR CHANGES — from commit to production, in hours or days.
3. CHANGE FAILURE RATE — the share of deployments that cause a failure
   (incident, rollback, or hotfix).
4. MEAN TIME TO RECOVERY (MTTR) — how long it takes to restore service
   after a failure.

## Collecting them from git/CI data (no platform required)

- Deployment frequency: count production deploy events (release tags,
  release commits, deploy log entries) per week.
- Lead time: for each change, first-commit timestamp to production deploy
  timestamp; take the median.
- Change failure rate: deployments followed within 24h by an incident,
  rollback, or hotfix, divided by total deployments.
- MTTR: incident start (alert) to service restored; median over the period.
- Measure over at least 4 weeks. Compare periods for trend — never judge by
  a single snapshot. Partial data skews the result; gather the full range
  before reporting.

## Performance bands (DORA research)

| Metric | Elite | High | Medium | Low |
|---|---|---|---|---|
| Deployment frequency | Multiple/day | Weekly–monthly | Monthly–6 mo | 6 mo+ |
| Lead time | < 1 hour | 1 day–1 week | 1–6 months | 6 mo+ |
| Change failure rate | < 5% | 5–10% | 10–15% | > 15% |
| MTTR | < 1 hour | < 1 day | 1 day–1 week | 1 week+ |

## Report format

Period, each metric with its band and trend, an overall rating, and
recommendations tied to the weakest metric: change failure rate high ->
invest in test automation and review; lead time rising -> look at review
bottlenecks; frequency low -> smaller batches and feature flags to decouple
deploy from release.

## Anti-gaming rules

- Metrics are computed from the journal, git, and CI records — never
  self-reported.
- Never optimize the metric instead of the system (redefining "deployment"
  to inflate frequency is fraud, not improvement).
- A metric that moves without a corresponding change in user outcomes is
  suspect — pair every DORA report with the business metric.

---
Source: harness-skills (Apache-2.0, Harness)
Adapted for Awino: SUPPORTING REFERENCE, not a routed skill — pinned in the
skill store for fail-closed integrity, never floor-routed into a turn's
context. All Harness SEI MCP calls (sei_dora_metric, sei_team, sei_ai_*)
stripped: the portable asset is the four metrics, the collection method from
git/CI data, and the DORA research bands — all vendor-neutral. Wired by
reference: the lifecycle map's REVIEW retrospective consults this reference;
osmani-shipping's rollout judgment uses deployment frequency and change
failure rate; incident-response feeds MTTR and failure classifications back
into it. This is retrospective measurement discipline, not a per-turn
procedure — which is exactly why it is a reference and not a skill.
