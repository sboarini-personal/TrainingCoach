# Deduplication and enrichment

Owner of FR-ACT-007 and FR-ACT-008. Master spec §22, §63. ADR-004. Implemented in Phase P5.

## 1. Inputs

Matching evaluates: athleteId, provider, providerActivityId, sport, start timestamp,
duration, distance, file hash. The new side is a source observation (canonical model
section 6); the existing side is a live Activity (ACTIVE or PENDING_REVIEW) in its EFFECTIVE
view, so an athlete's corrected sport or distance is what gets matched.

## 2. Matching tiers (FR-ACT-007)

Evaluated in order; the first decisive tier wins.

| Tier | Rule | Result |
|---|---|---|
| 1. Same bytes | SHA-256 of the raw bytes equals an existing RawSource of the same athlete | idempotent no-op: return the existing RawSource and Activity, emit `ingestion.duplicate` |
| 2. Same provider record | same `(provider, providerActivityId)` for the athlete | same logical Activity; new raw payload linked as a new source version |
| 3. Candidate match | same athlete, compatible sport, start-time difference within tolerance | scored (section 3) |
| 4. No candidate | none of the above | new logical Activity |

Tier 1 has two layers: the RawSource store returns the existing RawSource for the same bytes
(P3), and ingesting an observation whose `(rawSourceId, part)` is already linked to a live
Activity returns that Activity unchanged.

Sport compatibility: identical sports are compatible; `OTHER` is compatible with any sport
(the more specific sport wins); everything else is incompatible.

## 3. Scoring (dedupRulesVersion 1.0.0, assumption A-16)

A live Activity is a candidate only when its sport is compatible and its start differs from
the new source's start by less than 600 seconds (the start component's zero point).

| Component | Weight | Full score | Zero score |
|---|---|---|---|
| start-time difference | 0.4 | up to 60 s | 600 s or more |
| duration ratio (both known) | 0.3 | within 2% | 15% or more |
| distance ratio (both known) | 0.3 | within 2% | 15% or more |

- Between the full and zero points a component falls linearly.
- A ratio is `|a - b| / max(a, b)`; two zero values are identical. Duration compares elapsed
  time when both sides know it, otherwise moving time when both know it.
- Missing components are excluded and the remaining weights are renormalised. The combined
  score is in [0, 1].
- With several candidates the highest score wins, then the smallest start difference, then
  the lowest activity ID.

Decision on the best candidate's score:

- score at least 0.85: **link** as an ENRICHMENT source of the existing Activity
  (`dedup.linked`);
- score from 0.60 to 0.85: **ambiguous**: a DataQualityIssue (AMBIGUOUS_MATCH, with the
  candidate and the score) asks the athlete to confirm; the new source is kept as a separate
  PENDING_REVIEW Activity excluded from totals until resolved (`dedup.ambiguous`);
- score below 0.60: distinct Activity.

Ambiguous matches are never merged automatically.

## 4. Enrichment without duplication (FR-ACT-008)

- Linking a source adds or improves fields under the field-level resolution rules
  (FR-ACT-005). Totals (distance, duration, load) are chosen from one source, never summed.
- Aggregations (weekly volume, load, interference) iterate logical ACTIVE Activities only. A
  source never contributes on its own.
- AT-ACT-001: Garmin activity first, matching FIT uploaded later: one logical Activity,
  enriched fields, unchanged weekly volume.

## 5. Athlete corrections

- **Merge:** the athlete confirms an ambiguous match; the pending Activity's sources move to
  the candidate as ENRICHMENT sources, the pending Activity becomes MERGED (kept for lineage,
  `mergedIntoId` set), the candidate is re-resolved and its derived values recomputed; the
  issue closes as MERGED; `dedup.merged` is audited.
- **Keep separate:** the athlete rejects an ambiguous match; the pending Activity becomes
  ACTIVE and the issue closes as KEPT_SEPARATE; `dedup.kept_separate` is audited.
- **Split:** the athlete rejects a link; the source becomes its own ACTIVE Activity and the
  original is re-resolved without it (the earliest remaining source becomes PRIMARY when the
  primary left); `dedup.split` is audited. An Activity with a single source cannot be split.
- Merge, keep-separate and split save every changed Activity in one transaction.

## 6. Required properties (tested with property-based tests)

- **Idempotence:** importing the same input twice equals importing it once.
- **Order independence:** for decisive scores (link or distinct), Garmin-then-FIT and
  FIT-then-Garmin produce the same logical Activities, the same resolved values and the same
  totals. An ambiguous pair is flagged in either order; until the athlete resolves it, totals
  count the Activity that arrived first.
- **Volume invariance:** enrichment never adds a source's totals: weekly volume per sport is
  the sum over logical ACTIVE Activities of one resolved value each. When linked sources agree
  on a total, linking leaves the weekly volume unchanged; when they disagree, the resolution
  rule picks one of their values, independently of arrival order.

The last two properties were made precise in P5: field-level resolution prefers FIT totals,
so when a FIT and a provider summary disagree, strict "volume never changes" and "result
independent of arrival order" cannot both hold. The rule keeps order independence and the
intent of FR-ACT-008, which is never counting a session twice.
