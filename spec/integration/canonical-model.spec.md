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

## 2. Structure

| Group | Fields |
|---|---|
| identity | `schemaVersion`, `id`, `athleteId`, `version` |
| provenance | `sources[]` (rawSourceId, origin, provider, providerActivityId, fileHash, linkKind PRIMARY or ENRICHMENT, matchScore, dedupRulesVersion, linkedAt), `importedAt`, `fields` (field path -> provenance) |
| classification | `sport`, `subtype`, `providerType` |
| metadata | `name`, `description` (UNTRUSTED external text, SEC-INJ-001) |
| timing | `startTimeUtc`, `originalLocalTime`, `originalTimezone`, `utcOffset`, `trainingDay`, `trainingDayBasis`, `elapsedSeconds`, `movingSeconds` |
| distance | `meters` |
| elevation | `gainMeters`, `lossMeters` |
| cardiovascular | `averageHr`, `maxHr`, `hrZones` (zone model ID, seconds per zone) |
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
