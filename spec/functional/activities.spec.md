# Activities: data layers, time, provenance, overrides, quality

Owner of FR-ACT-002 to FR-ACT-006, FR-ACT-009 to FR-ACT-011. Master spec §17 to §24, §47.
The canonical schema (FR-ACT-001) is in
[../integration/canonical-model.spec.md](../integration/canonical-model.spec.md);
deduplication (FR-ACT-007, FR-ACT-008) in
[../integration/deduplication.spec.md](../integration/deduplication.spec.md).

## 1. Data layers (FR-ACT-002)

| Layer | Content | Mutability |
|---|---|---|
| RAW | Provider responses and uploaded files, byte-exact, content-addressed by SHA-256, plus `RawSource` metadata | Immutable. Only privacy deletion removes it (PRIV-DEL-001). |
| NORMALIZED | Canonical Activity and related entities, canonical units, per-field provenance | Rebuilt only by re-normalising RAW with a newer normaliser version |
| EFFECTIVE | NORMALIZED with DataOverrides applied at read time, quality recomputed and current derived values filled in; same Activity contract | View, never stored as a replacement |
| DERIVED | `DerivedMetricValue` records (metrics spec) | Append-only; superseded, never updated |

`RawSource` (contract [../contracts/raw-source.schema.json](../contracts/raw-source.schema.json)):
`rawSourceId`, `athleteId`, `origin` (FILE_UPLOAD, GARMIN_PERSONAL_API, MANUAL_ENTRY),
`provider`, `providerObjectId`, `category`, `receivedAt`, `sha256`, `byteSize`, `detectedType`,
`originalFilename` (sanitised, display only, untrusted), `malwareScan`.

RAW rules (P3):

- A RawSource record is **fully immutable**, like its bytes. The processing outcome of an
  import is tracked by a separate ingestion record (P6), so no RAW field ever changes. This
  replaces the earlier `ingestionStatus` field (intentional spec change, P3).
- Origin rules: GARMIN_PERSONAL_API names provider `garmin`; FILE_UPLOAD and MANUAL_ENTRY
  have no provider. `malwareScan` is CLEAN or NOT_SCANNED for uploaded files and
  NOT_APPLICABLE for provider payloads and manual entries (SEC-UPL-006).
- Bytes are stored content-addressed in a blob store **partitioned per athlete**:
  `<data_dir>/raw/<athleteId>/<sha256[0:2]>/<sha256>`. Partitioning means one athlete can never
  learn whether another athlete stored the same file, and deletion of one athlete's data
  never touches another's.
- Storing the same bytes twice for the same athlete returns the existing RawSource
  (deduplication tier 1, NFR-REL-001). Blobs are verified against their hash when read.
- The original filename keeps only its last path component, without control characters, at
  most 255 characters. It never influences storage paths (SEC-UPL-005).

### 1.1 Re-normalisation (P9)

When a normaliser gains a version (files spec 8: rules 1.1.0 add laps, pool lengths and the
power sensor), activities imported before keep their older observations until the athlete
runs `coach activity reprocess`:

- It selects the RAW sources whose observations were produced by an older version of a
  normaliser that still exists (same name, lower MAJOR.MINOR.PATCH), parses their stored
  bytes again in the sandbox and normalises them with the current rules. RAW never changes.
- Each new observation is stored as the latest revision of its source and part
  (ADR-029): observations stay immutable, earlier revisions are kept for lineage and reads
  use the latest. The activity holding that source is rebuilt from its
  sources' observations (resolution and quality) with its identity, links and status
  unchanged; corrections keep applying; per-activity metrics are recomputed and quality
  issues synchronised, as after any change.
- Deduplication is not run again. A RAW source is skipped, with nothing changed and a reason
  code, when its stored bytes are missing or no longer match their hash (`RAW_UNAVAILABLE`),
  when its parse or normalisation now fails (`REJECTED`), when it now yields another
  number of sessions than it has observations (`SESSIONS_CHANGED`), or when a session's
  sport or start time differs from its observation (`IDENTITY_CHANGED`): the new observation
  would describe another activity.
- One RAW source is one unit of work (ADR-027): its observations and activities change
  together or not at all. Observations already at the current version are untouched, so a
  second run changes nothing (idempotent).
- The same bytes imported again after an upgrade (deduplication tier 1, for example a file
  that comes back in another export) are re-normalised the same way on the way in: a newer
  version of the same session becomes a revision, the activity is rebuilt, and the import
  stays a DUPLICATE.
- It needs the ACTIVITIES consent, reports counts (sources reprocessed, distinct activities
  rebuilt, up to date, skipped by reason) and emits an event with those counts only. Stream blobs of replaced observations
  stay in the blob store (content-addressed) until privacy deletion.

## 2. Time model (FR-ACT-003, FR-ACT-004)

- Canonical timestamps are UTC (`...Z`). Naive datetimes are rejected in code (ruff `DTZ`
  rules) and in contracts.
- Sources may provide `utcOffset` (FIT: the activity message local timestamp minus its UTC
  timestamp), `originalTimezone` (IANA), both or neither. The Activity keeps each when known;
  otherwise it is UNKNOWN. `utcOffset` comes from a source when one provides it, otherwise
  from the timezone at the start instant. `originalLocalTime` is computed as start plus
  offset and is UNKNOWN without an offset. Offsets are within -14:00 and +14:00.
- **Training day** = the local date at activity start:
  1. in the activity's own local time (its offset, or its timezone) when known
     (`trainingDayBasis = ACTIVITY_TIMEZONE`);
  2. otherwise in the athlete's profile timezone, flagged `ESTIMATED_VALUE`
     (`trainingDayBasis = ATHLETE_TIMEZONE_ESTIMATED`);
  3. an athlete correction of `timing.trainingDay` (section 4) wins in the EFFECTIVE view,
     with `trainingDayBasis = CONFIGURED`.
- GPX and TCX normally carry UTC without timezone, so rule 2 applies.
- Presentation always converts to the athlete's timezone.
- AT-TIM-001 covers activities crossing local midnight, travel across timezones and GPX
  without timezone.

## 3. Field-level provenance and resolution (FR-ACT-005)

Conflicts between sources are resolved **per field**, never by one global source priority.
Each resolved field stores `{sourceId, path, rule, overrideId?}`. Resolution is a pure
function of the set of linked source observations (canonical model section 6), so it never
depends on the order in which sources arrived.

Source kinds, from the RawSource: `FIT_FILE` (uploaded FIT), `PROVIDER_RECORD` (provider API
payload), `OTHER_FILE` (TCX, GPX and other uploads), `ATHLETE_ENTRY` (manual entry, and rows
of the CSV activity template, which are the athlete's own declarations: files spec 8.4).
Precision (P7): a file downloaded from a provider is a file: `FIT_FILE` or `OTHER_FILE` of
origin GARMIN_PERSONAL_API, without a provider record ID; only the provider's JSON record is
`PROVIDER_RECORD` (files spec 9.1).

Resolution rules 1.0.0 (`resolutionRulesVersion`). For each field the first source in the
group's order with a known value wins. When every source that provides the field has it
UNKNOWN, the field is UNKNOWN. A field no source provides is absent (UNKNOWN when the contract
requires it).

| Group | Fields | Source order | Rule |
|---|---|---|---|
| sport | `classification.sport` | a specific sport wins over OTHER; then provider record, FIT, other file, athlete | `SPORT_MOST_SPECIFIC` |
| subtype | `classification.subtype` | sources reporting the resolved sport, same order | `SUBTYPE_MATCHING_SPORT` |
| timing | `timing.startTimeUtc`, `timing.originalTimezone`, `timing.utcOffset` | FIT, provider record, other file, athlete | `TIMING_PREFER_FIT` |
| summary totals | `timing.elapsedSeconds`, `timing.movingSeconds`, `distance.meters`, `elevation.*`, `training.load.external.*` | FIT, provider record, other file, athlete. One value is chosen, never summed. | `TOTALS_PREFER_FIT` |
| heart-rate summary | `cardiovascular.averageHr`, `cardiovascular.maxHr`, `cardiovascular.hrSensor` | FIT, provider record, other file, athlete | `HR_PREFER_FIT` |
| sensor summaries | `performance.*` (speed, cadence, power, running dynamics), `swimming.*` | FIT, provider record, other file, athlete | `SENSOR_PREFER_FIT` |
| provider training metrics | `training.aerobicEffect`, `training.anaerobicEffect`, `training.load.internal.providerLoad` | provider record, FIT field of the same provider, other file | `PROVIDER_METRIC_PREFER_RECORD` |
| metadata | `metadata.name`, `metadata.description`, `classification.providerType` | provider record, athlete, FIT, other file | `METADATA_PREFER_PROVIDER` |
| environment | `environment.temperatureC`, `humidityPct`, `windMps`, `altitudeMeters`, `poolLengthMeters` | FIT, provider record, other file, athlete | `ENVIRONMENT_PREFER_FIT` |
| declared conditions | `environment.terrain`, `environment.surface` | athlete, provider record, FIT, other file | `DECLARED_PREFER_ATHLETE` |
| subjective | `subjective.*` (RPE, soreness, pain, comments) | athlete only, never inferred | `SUBJECTIVE_ATHLETE_ONLY` |
| streams, laps, lengths, sets | stream references, per kind | FIT, provider record, other file | `STREAMS_PREFER_FIT` |
| computed | `timing.originalLocalTime`, `timing.trainingDay`, `timing.trainingDayBasis`, pace fields | computed from resolved fields (section 2; canonical model section 6) | `LOCAL_TIME_FROM_OFFSET`, `OFFSET_FROM_TIMEZONE`, `TRAINING_DAY_ACTIVITY_TIME`, `TRAINING_DAY_ATHLETE_TIMEZONE`, `PACE_FROM_SPEED` |
| recovery metrics | health provider | none (P11) | |
| any field with a DataOverride | override, in the EFFECTIVE view | none | `OVERRIDE` |

- `providerActivityId` is never resolved: each source link keeps its own.
- Ties between sources of the same kind are broken by the lowest raw SHA-256, never by
  arrival order.
- A newer payload of the same provider record (same provider and `providerActivityId`)
  replaces the older one in resolution (latest `receivedAt`); the older stays linked for
  lineage.
- Only athlete entries may carry subjective fields; an observation from another source that
  carries them is rejected.

## 4. Manual overrides (FR-ACT-006)

`DataOverride`: `overrideId`, `entityType`, `entityId`, `field`, `originalValue`,
`correctedValue`, `reason`, `correctedAt`, `correctedBy` (user ID), `supersedesOverrideId`.

Contract: [../contracts/data-override.schema.json](../contracts/data-override.schema.json).

- Overrides are append-only. Reverting is a new override back to the original value.
- RAW and NORMALIZED values are never modified (AT-RAW-001).
- Derived metrics that depend on the field are recomputed as new values; the old values stay
  for lineage.
- Overrides are audited (SEC-AUD-001).

Rules (P5):

- Correctable fields (the contract enumerates them; any other field is rejected):
  `classification.sport`, `classification.subtype`, `timing.trainingDay`,
  `timing.elapsedSeconds`, `timing.movingSeconds`, `distance.meters`, `elevation.gainMeters`,
  `elevation.lossMeters`, `cardiovascular.averageHr`, `cardiovascular.maxHr`,
  `performance.averageSpeedMps`, `performance.maxSpeedMps`, `swimming.lengthCount`,
  `swimming.strokeType`, `subjective.rpe`, `subjective.soreness`, `environment.temperatureC`,
  `environment.humidityPct`, `environment.windMps`, `environment.altitudeMeters`,
  `environment.terrain`, `environment.surface`, `environment.poolLengthMeters`,
  `metadata.name`, `metadata.description`.
- The corrected value must be valid for the field (type and range); `null` marks the value
  UNKNOWN. A corrected subtype must be valid for the effective sport. The reason is required.
- One chain per entity and field: a new override names the field's current override in
  `supersedesOverrideId` (`null` for the first); any other value is rejected as a concurrent
  change. The last override of the chain applies.
- `originalValue` is the NORMALIZED value when the correction was made (`null` when it was
  UNKNOWN or absent). A correction keeps applying when a later source changes the NORMALIZED
  value.
- EFFECTIVE view: corrected values replace NORMALIZED values with provenance rule `OVERRIDE`
  and the `overrideId`; the `sourceId` stays that of the NORMALIZED value (the primary source
  when the field had none). Pace follows a corrected speed; a subtype that is not valid for a
  corrected sport becomes UNKNOWN; quality is recomputed on the effective values.
- Corrections are refused on MERGED activities (the athlete corrects the activity it was
  merged into). Overrides stay attached to the activity they were made on.
- Audit action `activity.corrected` (target `DATA_OVERRIDE`) and event `activity.corrected`
  carry the field path only. The reason is untrusted free text: it is stored with the
  override and never written to audit events, logs or observability events.
- Activity-bound metrics (metric catalogue: `any.srpe_load` reads `subjective.rpe` and
  `timing.elapsedSeconds`) are recomputed from the EFFECTIVE view after every change to an
  activity: ingestion, linking, correction, merge and split. An unchanged input snapshot keeps
  the current value; a changed one supersedes it. An effective value outside a metric input's
  valid range is not used: the metric records MISSING_INPUT with SENSOR_QUALITY.

## 5. Data quality (FR-ACT-009)

`DataQuality` on every activity and daily record:

- `completeness`: share (0 to 1) of the sport's expected fields that are known. Expected
  field sets per sport are part of the quality rules (section 5.1).
- `reliability`: HIGH, MEDIUM, LOW, UNKNOWN, from sensor type when known (chest strap vs
  optical HR, measured vs estimated power), GPS quality (from P6) and anomaly count.
- `anomalies[]`: `{code, field, detail}` from plausibility rules (proposed, versioned,
  configurable; assumption A-18):

  | Code | Rule |
  |---|---|
  | HR_OUT_OF_RANGE | heart rate below 25 or above 230 bpm |
  | SPEED_IMPLAUSIBLE | running above 12 m/s; cycling above 30 m/s; swimming above 3 m/s |
  | DURATION_INCONSISTENT | moving time greater than elapsed time |
  | DISTANCE_GPS_JUMP | instantaneous jump inconsistent with sport speed limits |
  | POWER_OUT_OF_RANGE | power above 2500 W |
  | TIME_IN_FUTURE | start time after ingestion time |

- `confidence`: HIGH (completeness at least 0.8, reliability HIGH, no anomalies), LOW
  (completeness below 0.5, reliability LOW, or blocking anomalies), MEDIUM otherwise.
- Each anomaly also raises a `DataQualityIssue` visible to the athlete.

Unknown stays UNKNOWN: missing metrics are never interpolated into stored values.

### 5.1 Quality rules 1.0.0 (`qualityRulesVersion`, assumption A-18)

Assessed on an activity's field values: NORMALIZED values when stored, EFFECTIVE values when
read.

| Sport | Expected fields |
|---|---|
| RUNNING | `timing.elapsedSeconds`, `timing.movingSeconds`, `distance.meters`, `elevation.gainMeters`, `cardiovascular.averageHr`, `cardiovascular.maxHr`, `performance.averageSpeedMps`, `performance.cadence` |
| CYCLING | `timing.elapsedSeconds`, `timing.movingSeconds`, `distance.meters`, `elevation.gainMeters`, `cardiovascular.averageHr`, `cardiovascular.maxHr`, `performance.averageSpeedMps`, `performance.power`, `performance.cadence` |
| SWIMMING | `timing.elapsedSeconds`, `timing.movingSeconds`, `distance.meters`, `cardiovascular.averageHr`, `performance.averageSpeedMps`, `swimming.strokeType` |
| STRENGTH | `timing.elapsedSeconds`, `cardiovascular.averageHr`, `cardiovascular.maxHr`, `subjective.rpe` |
| TENNIS | `timing.elapsedSeconds`, `cardiovascular.averageHr`, `cardiovascular.maxHr`, `subjective.rpe` |
| OTHER | `timing.elapsedSeconds`, `cardiovascular.averageHr`, `subjective.rpe` |

| Anomaly | Field | Blocking |
|---|---|---|
| HR_OUT_OF_RANGE | `cardiovascular.averageHr`, `cardiovascular.maxHr` | no |
| SPEED_IMPLAUSIBLE | `performance.averageSpeedMps` | yes |
| SPEED_IMPLAUSIBLE | `performance.maxSpeedMps` | no |
| SPEED_IMPLAUSIBLE | `distance.meters`, when distance divided by moving time (elapsed time when moving is unknown) exceeds the limit | yes |
| DURATION_INCONSISTENT | `timing.movingSeconds` | yes |
| POWER_OUT_OF_RANGE | `performance.power` (average or max) | no |
| TIME_IN_FUTURE | `timing.startTimeUtc`, start after the activity's `importedAt` | yes |
| DISTANCE_GPS_JUMP | `distance.meters`: a segment of the distance samples faster than the sport limit, detected by the normaliser (P6) and carried on the observation | no |

Speed limits apply to running, cycling and swimming. An activity carries the anomalies of
its sources' observations in addition to its own.

- Reliability: sensor evidence is CHEST_STRAP = HIGH, OPTICAL or OTHER = MEDIUM (HR sensor)
  and MEASURED = HIGH, ESTIMATED = MEDIUM (power). The base is the lowest evidence present, or
  UNKNOWN without evidence. One anomaly lowers it one level (HIGH to MEDIUM; MEDIUM, LOW or
  UNKNOWN to LOW); two or more make it LOW.
- Uncertainty reasons: MISSING_DATA when an expected field is unknown; SENSOR_QUALITY when an
  anomaly exists or reliability is LOW; ESTIMATED_VALUE when the training day is estimated or
  power is ESTIMATED. NON_COMPARABLE_SESSION, ENVIRONMENT and INSUFFICIENT_HISTORY come from
  analytics (P8 onward).
- Warnings are display text and are not produced by the rules: anomalies and uncertainty
  reasons are the machine-readable signal.

### 5.2 DataQualityIssue

Contract: [../contracts/data-quality-issue.schema.json](../contracts/data-quality-issue.schema.json).

- Kinds: ANOMALY (per activity, code and field), AMBIGUOUS_MATCH (deduplication spec
  section 3) and IMPORT_FAILED (from P6: a rejected or failed file import; it names the
  ingestion record instead of an activity and its code is the reason code). Status OPEN or
  CLOSED. Resolutions: MERGED and KEPT_SEPARATE (ambiguous matches), NO_LONGER_DETECTED (the
  anomaly left the EFFECTIVE view, for example after a correction or a better source),
  ACKNOWLEDGED (the athlete accepts the data as it is, or has seen a failed import).
- Contract 1.1.0 (P6) adds IMPORT_FAILED and `ingestionId`; 1.0.0 documents stay valid.
- Issues are synchronised after every change to an activity: a detected anomaly opens an
  issue unless an open or acknowledged issue exists for the same activity, code and field;
  an open anomaly issue that is no longer detected is closed. Activities that are not live
  (MERGED) keep no open anomaly issues.
- Events `quality.issue.raised` and `quality.issue.resolved` carry codes only.

## 6. Uncertainty reasons (FR-ACT-010)

`MISSING_DATA`, `SENSOR_QUALITY`, `NON_COMPARABLE_SESSION`, `ENVIRONMENT`,
`INSUFFICIENT_HISTORY`, `ESTIMATED_VALUE`. Insights and decisions MUST list the reasons that
materially affect them.

## 7. Subjective data

Per activity: `rpe` (CR-10, 0 to 10), `soreness` (1 to 5), `pain` `{flag, intensity 0 to 10,
location}`, `comments` (untrusted text). A pain report may trigger the safety rules in
[../safety/sport-safety.spec.md](../safety/sport-safety.spec.md).

## 8. Unplanned activities (FR-ACT-011)

A completed activity with no matching planned workout gets a WorkoutExecution with status
`UNPLANNED` (FR-PLN-007). It still contributes to training load, interference, recovery,
analytics and adaptation. Example (AT-UNP-001): a planned rest day with 120 minutes of
unplanned tennis must change the evaluation of the next day's planned workout.

## 9. Consent and ownership

- Ingesting an observation, correcting, merging, splitting or keeping activities separate
  requires the ACTIVITIES consent (PRIV-CON-002): without it nothing is stored and the
  operation fails with the missing consent. Analytics read paths are gated from P8.
- Every activity, observation, override and issue is owner-scoped (ADR-016). A reference to
  another athlete's activity, source or issue behaves exactly like a missing one.
