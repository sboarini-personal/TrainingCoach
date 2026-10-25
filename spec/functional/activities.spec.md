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
| EFFECTIVE | NORMALIZED with DataOverrides applied at read time | View, never stored as a replacement |
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

## 2. Time model (FR-ACT-003, FR-ACT-004)

- Canonical timestamps are UTC (`...Z`). Naive datetimes are rejected in code (ruff `DTZ`
  rules) and in contracts.
- Each activity keeps `originalLocalTime`, `originalTimezone` (IANA) and `utcOffset` when the
  source provides them; otherwise these fields are UNKNOWN.
- **Training day** = the local date at activity start:
  1. in the activity's own timezone when known (`trainingDayBasis = ACTIVITY_TIMEZONE`);
  2. otherwise in the athlete's profile timezone, flagged `ESTIMATED_VALUE`
     (`trainingDayBasis = ATHLETE_TIMEZONE_ESTIMATED`);
  3. an athlete-configured rule may override (`CONFIGURED`).
- FIT: offset from the activity message local timestamp when present. GPX and TCX normally
  carry UTC without timezone, so rule 2 applies.
- Presentation always converts to the athlete's timezone.
- AT-TIM-001 covers activities crossing local midnight, travel across timezones and GPX
  without timezone.

## 3. Field-level provenance and resolution (FR-ACT-005)

Conflicts between sources are resolved **per field**, never by one global source priority.
Each resolved field stores `{sourceId, path, rule, overrideId?}`.

Default resolution rules (versioned as `resolutionRulesVersion`):

| Field group | Preferred | Fallback |
|---|---|---|
| providerActivityId | provider record | none |
| streams, laps, lengths, sets | FIT | provider JSON |
| summary totals (distance, durations, elevation) | FIT session | provider summary. One value is chosen, never summed. |
| heart-rate summary | FIT | provider summary |
| provider training metrics (training effect, provider load) | provider record | FIT field of the same provider |
| subjective (RPE, soreness, pain, comments) | athlete | none, never inferred |
| recovery metrics | health provider | none |
| any field with a DataOverride | override | none |

## 4. Manual overrides (FR-ACT-006)

`DataOverride`: `overrideId`, `entityType`, `entityId`, `field`, `originalValue`,
`correctedValue`, `reason`, `correctedAt`, `correctedBy` (user ID), `supersedesOverrideId`.

- Overrides are append-only. Reverting is a new override back to the original value.
- RAW and NORMALIZED values are never modified (AT-RAW-001).
- Derived metrics that depend on the field are recomputed as new values; the old values stay
  for lineage.
- Overrides are audited (SEC-AUD-001).

## 5. Data quality (FR-ACT-009)

`DataQuality` on every activity and daily record:

- `completeness`: share (0 to 1) of the sport's expected fields that are known. Expected
  field sets per sport are defined in the metric registry.
- `reliability`: HIGH, MEDIUM, LOW, UNKNOWN, from sensor type when known (chest strap vs
  optical HR, measured vs estimated power), GPS quality and anomaly count.
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
