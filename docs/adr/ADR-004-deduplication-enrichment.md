# ADR-004: Activity deduplication and enrichment model

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-ACT-007, FR-ACT-008, NFR-REL-001

## Context

The same session can arrive from Garmin and later as an uploaded FIT, in any order. Counting
it twice corrupts volume, load and adaptation decisions.

## Decision

Separate the **logical Activity** from its **sources**. Matching tiers: identical bytes
(no-op), same provider record, scored candidate match, new activity. High scores link a
source as enrichment; ambiguous scores ask the athlete; nothing ambiguous is merged
automatically. Aggregations iterate logical activities only. Rules are versioned
(`dedupRulesVersion`).

## Consequences

- Athletes may need to confirm ambiguous matches.
- Merge and split operations exist and are audited.

## Verification

- Property-based tests: idempotence, order independence, volume invariance.
- AT-ACT-001.
