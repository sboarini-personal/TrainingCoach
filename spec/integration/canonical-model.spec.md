# Canonical Activity model

Owner of FR-ACT-001. Master spec §18, §52. Contract:
[../contracts/activity.schema.json](../contracts/activity.schema.json) (schema version 1.x).

## 1. Principles

- Provider-independent: no Garmin types, field names or enumerations in the canonical model
  (ADR-002). Provider-specific labels are kept only as untrusted display data
  (`classification.providerType`) and in RAW.
- One Activity = one **logical** session. Multiple sources link to it (deduplication spec).
- `null` = UNKNOWN; absent = not applicable (spec README).
- Every persisted Activity carries `schemaVersion` and `version` (optimistic concurrency).
- The stored Activity is the NORMALIZED layer: it is rebuilt from its sources' observations
  (section 6) and never edited by corrections. Corrections and derived values appear only in
  the EFFECTIVE view, which uses the same contract (functional/activities.spec.md section 1).

## 2. Structure

| Group | Fields |
|---|---|
| identity | `schemaVersion`, `id`, `athleteId`, `version`, `status` (ACTIVE, PENDING_REVIEW, MERGED), `mergedIntoId` (MERGED only) |
| provenance | `sources[]` (sourceId = rawSourceId, part, origin, provider, providerActivityId, fileHash, linkKind PRIMARY or ENRICHMENT, matchScore, dedupRulesVersion, linkedAt), `importedAt`, `resolutionRulesVersion`, `qualityRulesVersion`, `fields` (field path -> provenance) |
| classification | `sport`, `subtype`, `providerType` |
| metadata | `name`, `description` (UNTRUSTED external text, SEC-INJ-001) |
| timing | `startTimeUtc`, `originalLocalTime`, `originalTimezone`, `utcOffset`, `trainingDay`, `trainingDayBasis`, `elapsedSeconds`, `movingSeconds` |
| distance | `meters` |
| elevation | `gainMeters`, `lossMeters` |
| cardiovascular | `averageHr`, `maxHr`, `hrSensor` (CHEST_STRAP, OPTICAL, OTHER), `hrZones` (zone model ID, seconds per zone) |
| performance | `averageSpeedMps`, `maxSpeedMps`, `averagePaceSecondsPerKm`, `averagePaceSecondsPer100m`, `cadence` (average, max, unit), `power` (source MEASURED/ESTIMATED/UNKNOWN, average, max), `runningDynamics` (stride length, vertical oscillation, ground contact time) |
| swimming | `lengthCount`, `strokeType`, `averageStrokeRate`, `swolf` (value, origin) |
| strength | `strengthSessionId` |
| training | `aerobicEffect`, `anaerobicEffect` (provider metrics), `load.external`, `load.internal` (session RPE load, provider load) |
| subjective | `rpe`, `soreness`, `pain`, `comments` (untrusted) |
| environment | `temperatureC`, `humidityPct`, `windMps`, `altitudeMeters`, `terrain`, `surface`, `poolLengthMeters` |
| streams | references to stored stream blobs (not inline) |
| quality | completeness, reliability, anomalies, confidence, uncertainty reasons, warnings |

## 3. Enumerations

| Enum | Values |
|---|---|
| sport | RUNNING, CYCLING, SWIMMING, STRENGTH, TENNIS, OTHER |
| subtype (running) | ROAD, TRAIL, TRACK, TREADMILL, OTHER |
| subtype (cycling) | ROAD, MOUNTAIN, GRAVEL, INDOOR_TRAINER, OTHER |
| subtype (swimming) | POOL, OPEN_WATER |
| subtype (tennis) | MATCH, PRACTICE |
| subtype (strength) | GYM, HOME, OTHER |
| power.source | MEASURED, ESTIMATED, UNKNOWN |
| trainingDayBasis | ACTIVITY_TIMEZONE, ATHLETE_TIMEZONE_ESTIMATED, CONFIGURED |
| terrain | ROAD, TRAIL, TRACK, MIXED, INDOOR, WATER, COURT |
| surface (tennis) | HARD, CLAY, GRASS, CARPET, OTHER |
| hrSensor | CHEST_STRAP, OPTICAL, OTHER |
| status | ACTIVE, PENDING_REVIEW, MERGED |

The swimming and tennis subtypes are enforced by the schema; the others are documented here
and enforced in code.

## 4. Provider metrics

Provider metrics are objects `{value, provider, metricName, origin}` with origin RAW_PROVIDER
or PROVIDER_DERIVED. They are never compared with another provider's metric of a similar name
(FR-MET-007).

## 5. Units

Metres, seconds, metres per second, bpm, watts, degrees Celsius, percent, millimetres
(vertical oscillation), milliseconds (ground contact time). Pace fields are derived from
speed and stored for convenience with their unit in the field name.

## 6. Field paths, observations and status (P5)

- Every leaf of the model has a **field path** (`distance.meters`,
  `cardiovascular.averageHr`, `performance.power`). Provenance, overrides, expected fields and
  resolution rules all address fields by path. Composite values (`cadence`, `power`, `hrZones`,
  `swolf`, provider metrics, `pain`) are one field each. The field catalogue
  (`coach.domain.activity.fields`) lists every path with its type, unit range, resolution
  group, whether a source may provide it and whether the athlete may correct it.
- A **source observation** (contract
  [../contracts/source-observation.schema.json](../contracts/source-observation.schema.json))
  (1.1.0 from P6: athlete entries may come from a FILE_UPLOAD of the CSV activity template)
  is the normalised content of one part of one RawSource: the field values that source
  provides, the path of each value inside the source, stream references and anomalies the
  normaliser detected. It is immutable and identified by `(rawSourceId, part)`; `part` is 0
  unless one raw file holds several sessions (multisport FIT, P6). A raw source contributes at
  most one part to a given Activity.
- Fields a source never provides: `timing.originalLocalTime`, `timing.trainingDay`,
  `timing.trainingDayBasis` (computed, activities spec section 2), pace fields (computed from
  speed: 1000 / speed and 100 / speed), `cardiovascular.hrZones` (application zone model, P8:
  filled in the EFFECTIVE view from the current `any.hr_zone_time` values, metrics spec 9.2),
  `training.load.internal.sessionRpeLoad` (the current `any.srpe_load` value, filled in the
  EFFECTIVE view only) and `strength.strengthSessionId` (linked by strength ingestion, P10).
- `status`: ACTIVE activities count toward totals. PENDING_REVIEW holds a source whose match
  was ambiguous until the athlete decides (deduplication spec section 3); it is excluded from
  totals. MERGED is the record of a pending activity whose sources moved to `mergedIntoId`; it
  is kept for lineage, is excluded from totals and is never matched again.

Contract version note (intentional spec change, P5): `status`, `mergedIntoId`,
`provenance.resolutionRulesVersion`, `provenance.qualityRulesVersion`, `sources[].part` and
`cardiovascular.hrSensor` were added to the Activity contract before its first use. No
Activity document had been produced or stored and no code consumed the contract, so it stays
at version 1.0.0; from now on the change process of `spec/README.md` (minor for additive,
major for breaking changes) applies.
