# Deduplication and enrichment

Owner of FR-ACT-007 and FR-ACT-008. Master spec §22, §63. ADR-004. Implemented in Phase P5.

## 1. Inputs

Matching evaluates: athleteId, provider, providerActivityId, sport, start timestamp,
duration, distance, file hash.

## 2. Matching tiers (FR-ACT-007)

Evaluated in order; the first decisive tier wins.

| Tier | Rule | Result |
|---|---|---|
| 1. Same bytes | SHA-256 of the raw bytes equals an existing RawSource of the same athlete | idempotent no-op: return the existing RawSource and Activity, emit `ingestion.duplicate` |
| 2. Same provider record | same `(provider, providerActivityId)` for the athlete | same logical Activity; new raw payload linked as a new source version |
| 3. Candidate match | same athlete, compatible sport, start-time difference within tolerance | scored (section 3) |
| 4. No candidate | none of the above | new logical Activity |

Sport compatibility: identical sports are compatible; `OTHER` is compatible with any sport
(the more specific sport wins); everything else is incompatible.

## 3. Scoring (initial parameters, versioned as `dedupRulesVersion`, assumption A-16)

| Component | Full score | Zero score |
|---|---|---|
| start-time difference | up to 60 s | 600 s or more |
| duration ratio (both known) | within 2% | 15% or more |
| distance ratio (both known) | within 2% | 15% or more |

Missing components are excluded and the remaining weights are renormalised. The combined score
is in [0, 1]:

- score at least 0.85: **link** as an ENRICHMENT source of the existing Activity
  (`dedup.linked`);
- score from 0.60 to 0.85: **ambiguous**: a DataQualityIssue asks the athlete to confirm;
  the new source is kept as a separate, pending Activity excluded from totals until resolved
  (`dedup.ambiguous`);
- score below 0.60: distinct Activity.

Ambiguous matches are never merged automatically.

## 4. Enrichment without duplication (FR-ACT-008)

- Linking a source adds or improves fields under the field-level resolution rules
  (FR-ACT-005). Totals (distance, duration, load) are chosen from one source, never summed.
- Aggregations (weekly volume, load, interference) iterate logical Activities only. A source
  never contributes on its own.
- AT-ACT-001: Garmin activity first, matching FIT uploaded later: one logical Activity,
  enriched fields, unchanged weekly volume.

## 5. Athlete corrections

- **Merge:** the athlete confirms an ambiguous match; sources move to one Activity; derived
  values are recomputed; `dedup.merged` is audited.
- **Split:** the athlete rejects a link; the source becomes its own Activity; `dedup.split`
  is audited.

## 6. Required properties (tested with property-based tests)

- **Idempotence:** importing the same input twice equals importing it once.
- **Order independence:** Garmin-then-FIT and FIT-then-Garmin produce the same logical
  Activities and the same totals.
- **Volume invariance:** enrichment never changes weekly volume per sport.
