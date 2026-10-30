# File ingestion

Owner of INT-FIL-001 to INT-FIL-004. Master spec §15 and §16. Security controls in
[../security/uploads.spec.md](../security/uploads.spec.md). Implemented in Phase P6; laps,
pool lengths and power sensors from P9 (normalisation rules 1.1.0).

## 1. Formats (INT-FIL-001)

| Format | Parser | Notes |
|---|---|---|
| FIT | official Garmin FIT Python SDK (`garmin-fit-sdk`), CRC check enabled | messages used: file_id, session, record, activity; from P9 lap, length and device_info; from P10 set |
| TCX | hardened XML parser (`defusedxml`) | Garmin TrainingCenterDatabase v2 root element required |
| GPX | hardened XML parser (`defusedxml`) | GPX 1.1 (1.0 accepted); heart rate and cadence only from recognised extension namespaces |
| CSV | standard library `csv` with limits | only the application's own documented templates (section 8.4) |
| ZIP | standard library `zipfile` with limits | only as a container for the formats above (for example Garmin activity downloads) |

Third-party CSV exports are not supported in V1: their formats are not specified here and are
not guessed. A template can be added once a real sample is reviewed. P6 imports session
summaries and the time series; P9 adds laps, pool lengths and power sensors (section 8.6);
P10 adds the strength sets of FIT files (section 8.7).

## 2. Pipeline (INT-FIL-002)

Stages run in this order; each failure stops the pipeline with a typed reason code.

| # | Stage | Rejection code |
|---|---|---|
| 1 | Upload received (size streamed and counted) | `TOO_LARGE` |
| 2 | Extension allow-list | `EXTENSION_NOT_ALLOWED` |
| 3 | Content signature (magic bytes, root element) | `CONTENT_MISMATCH` |
| 4 | Size limits per format | `TOO_LARGE` |
| 5 | Quarantine: stored under a random name outside the blob store | none |
| 6 | Malware scan hook (SEC-UPL-006) | `MALWARE_SUSPECTED` |
| 7 | SHA-256 computed; duplicate check (tier 1) | `DUPLICATE` (not an error) |
| 8 | Safe parser in a sandboxed subprocess with timeout and limits | `PARSE_FAILED`, `PARSE_TIMEOUT`, `LIMIT_EXCEEDED` |
| 9 | Schema validation of the parser output | `SCHEMA_INVALID` |
| 10 | Semantic validation (plausibility, time ordering, units) | `SEMANTIC_INVALID` (or quality anomalies) |
| 11 | Normalisation to canonical model | `NORMALIZATION_FAILED` |
| 12 | Deduplication (tiers 2 to 4) | none |
| 13 | Persistence: RawSource + blob, stream blobs, observations, activities and links | `PERSISTENCE_FAILED` |

Precisions (P6):

- Quarantine comes before the malware scan (intentional spec change, P6): the scanner works on
  the quarantined file, as `uploads.spec.md` section 6 requires. Earlier versions listed the
  scan first.
- Stage 1 caps the stream at the largest per-format limit; stage 4 applies the limit of the
  detected format. A file rejected at stage 1 was not read completely, so its record has no
  SHA-256.
- Stage 7: when an earlier import of the same bytes completed, the upload is a duplicate and
  returns the earlier activities. When a RawSource with the same bytes exists without a
  completed import (an interrupted import, or bytes received from a provider), the pipeline
  continues; every later stage is idempotent, so the retry completes the import.
- Stages 8 to 11 finish for every session of the file (and every archive member) before
  anything is persisted: one invalid session rejects the whole upload.
- Stage 13 stores each activity atomically. An interruption between activities of one upload
  leaves the stored ones in place; importing the same file again completes the rest (threat
  model R-18).
- Text taken from files (activity names, descriptions, comments, device labels) is screened
  for pasted secrets before it is stored (SEC-SEC-005); a detection replaces the value with
  `[REDACTED-SECRET]` and the result carries the fixed notice.
- Ingesting requires the ACTIVITIES consent; without it nothing is read, stored or recorded.

## 3. Limits (INT-FIL-003)

Defaults, all configurable. There is never an unlimited mode: every limit must be a positive
number.

| Limit | Default |
|---|---|
| Maximum file size: FIT | 32 MiB |
| Maximum file size: TCX, GPX | 64 MiB |
| Maximum file size: CSV | 16 MiB |
| Maximum ZIP size (compressed) | 64 MiB |
| Maximum ZIP total uncompressed size | 256 MiB |
| Maximum ZIP compression ratio per member | 100 |
| Maximum ZIP members | 50 |
| Maximum records per file | 2,000,000 |
| Maximum activities per import | 500 |
| Maximum XML element depth | 64 |
| Maximum XML elements | 5,000,000 |
| Maximum parser output | 256 MiB |
| Parser timeout | 60 s |
| Parser memory | 1 GiB where the platform supports enforcement; record limits apply everywhere |

Records are FIT messages, XML elements of track points, and CSV rows.

## 4. Failure handling (INT-FIL-004)

- A failed import persists nothing except an ingestion record (hash, size, detected type,
  reason code, timestamp) and a DataQualityIssue the athlete can see (kind `IMPORT_FAILED`,
  activities spec section 5.2).
- Rejected file bytes are deleted from quarantine immediately (retention configurable,
  default 0 days). Accepted bytes leave quarantine once stored in the blob store.
- Partial activities are never persisted.
- Every outcome emits an `ingestion.*` event (NFR-OBS-001): `ingestion.file.received` (type),
  `ingestion.file.parsed` (type, sessions), `ingestion.file.rejected` (reason),
  `ingestion.file.failed` (reason), `ingestion.duplicate`. Events carry codes, never file
  names or content.

## 5. Idempotency

Re-uploading identical bytes returns the existing records (NFR-REL-001). An upload request may
also carry an `Idempotency-Key`: a second request with the same key returns the first result;
the same key with different bytes is refused.

## 6. Archives

- A ZIP is a container only. The archive itself passes stages 1 to 7; it is then opened in
  memory and every member is checked (archive limits of section 3 and uploads spec section 3,
  then stages 2 to 4) before any member is parsed. Each member is treated as its own file: it
  passes stages 8 to 11 and becomes its own RawSource with its byte-exact content, so the same FIT file
  uploaded inside or outside a ZIP is the same RAW source. The archive itself is not stored;
  its SHA-256 and size are kept in the ingestion record.
- Validated member bytes are quarantined under random names like any upload; archive paths
  never become file system paths.
- All or nothing: a rejected member rejects the archive, with that member's reason code.

## 7. Parser output (internal contract)

The sandboxed parser returns JSON that must validate against
[../contracts/parser-output.schema.json](../contracts/parser-output.schema.json): the format,
the parser name and version, the record count and a list of sessions. A session carries the
format's own labels (sport, sub-sport, manufacturer), its start time and UTC offset when the
format states one, summary values keyed by name (section 8), untrusted texts, the location of
each summary value in the file and a columnar time series. From version 1.1.0 (P9) a session
also carries its laps, its pool lengths and the device types of the file's sensors (section
8.6); every parser emits them, empty when its format has none. From version 1.2.0 (P10) a
session also carries its strength sets (section 8.7), empty for every format but FIT. The
parent decodes the JSON strictly: unknown keys, wrong types, non-finite numbers, unequal sample columns or too many
sessions are `SCHEMA_INVALID`.

## 8. Normalisation rules 1.2.0

Each normaliser has a name and version recorded on the observations it produces (`fit`,
`tcx`, `gpx`, `csv_activity`; 1.0.0 in P6, 1.1.0 from P9, which adds laps, pool lengths
and the power sensor, section 8.6, and 1.2.0 from P10, which adds strength sets, section
8.7). Observations of an older version are brought up to date
by re-normalisation (activities spec section 1.1). Values are converted to canonical units in code;
nothing is inferred that the file does not state. Formats without a timezone or offset leave
the training day to the athlete's timezone (activities spec section 2).

Semantic validation (stage 10), for every format:

- a session has a start time between 1990-01-01 and the end of the year after the import;
- time series offsets are non-negative and never decrease; cumulative distances never
  decrease;
- summary values are finite and non-negative, except UTC offsets;
- a FIT file whose `file_id.type` is not `activity` is `SEMANTIC_INVALID` (`NOT_AN_ACTIVITY`),
  checked before sessions: Garmin data exports hold mostly wellness files (monitoring, sleep,
  metrics; their proprietary types decode as numbers) that have no session either; a file
  without any activity session is `SEMANTIC_INVALID` (`NO_SESSION`);
- implausible but well-formed values (heart rate 245, speed 40 m/s) are not rejected: they
  become quality anomalies (activities spec section 5.1).

### 8.1 FIT

One observation per `session` message (part = session index).

| FIT | Canonical |
|---|---|
| `session.start_time` | `timing.startTimeUtc` |
| `activity.local_timestamp` minus `activity.timestamp` (exactly one activity message; whole minutes within 14 h) | `timing.utcOffset` |
| `total_elapsed_time` | `timing.elapsedSeconds` |
| `total_moving_time` | `timing.movingSeconds` (timer time is not moving time and is not mapped) |
| `total_distance` | `distance.meters` |
| `total_ascent`, `total_descent` | `elevation.gainMeters`, `elevation.lossMeters` |
| `avg_heart_rate`, `max_heart_rate` | `cardiovascular.averageHr`, `cardiovascular.maxHr` |
| `enhanced_avg_speed` (else `avg_speed`), `enhanced_max_speed` (else `max_speed`) | `performance.averageSpeedMps`, `performance.maxSpeedMps` |
| running: `2 x (avg_cadence + avg_fractional_cadence)`, `2 x (max_cadence + max_fractional_cadence)` | `performance.cadence` in STEPS_PER_MIN (FIT stores strides per minute) |
| cycling: `avg_cadence`, `max_cadence` | `performance.cadence` in RPM |
| `avg_power`, `max_power` | `performance.power` with source UNKNOWN (P9 decides MEASURED) |
| `total_work` / 1000 | `training.load.external.workKilojoules` |
| `total_training_effect`, `total_anaerobic_training_effect` | `training.aerobicEffect`, `training.anaerobicEffect` as provider metrics of `file_id.manufacturer` (metric names "total training effect", "total anaerobic training effect", PROVIDER_DERIVED); omitted when the manufacturer is unknown |
| swimming: `num_active_lengths` (else `num_lengths`), `pool_length` (pool only), `swim_stroke` | `swimming.lengthCount`, `environment.poolLengthMeters`, `swimming.strokeType` (`im` and `im_by_round` as MIXED) |
| `sport`, `sub_sport` | classification (table below); `classification.providerType` = `fit:<sport>/<sub_sport>` |
| `record` messages inside the session's time window | time series (section 8.5) |
| (P9) a session's window runs from its start to its end, and stops before the next session's start, so a boundary message of a multisport file belongs to one session only | records, laps and lengths |

Laps, lengths and `device_info` follow section 8.6. Not mapped: `avg_temperature` (the
device's own sensor, not ambient temperature), calories, the heart-rate sensor type (a
session does not state it). Set messages follow section 8.7.

| FIT sport | sub_sport | Sport, subtype |
|---|---|---|
| running | road, street / trail / track / treadmill, indoor_running / generic or absent / other | RUNNING: ROAD / TRAIL / TRACK / TREADMILL / unknown / OTHER |
| cycling | road / mountain, downhill / gravel_cycling, cyclocross / indoor_cycling, spin, virtual_activity / generic or absent / other | CYCLING: ROAD / MOUNTAIN / GRAVEL / INDOOR_TRAINER / unknown / OTHER |
| swimming | lap_swimming / open_water / other | SWIMMING: POOL / OPEN_WATER / unknown |
| training | strength_training | STRENGTH, subtype unknown |
| tennis | match / other | TENNIS: MATCH / unknown |
| anything else | any | OTHER, subtype unknown |

### 8.2 TCX

One observation per `Activity` (part = index). `Sport` attribute: Running = RUNNING, Biking =
CYCLING, Other = OTHER; subtype unknown. Times must carry `Z` or an offset (naive times are
`SEMANTIC_INVALID`); a non-UTC offset on the first lap start sets `timing.utcOffset`.

| TCX | Canonical |
|---|---|
| first `Lap/@StartTime` | `timing.startTimeUtc` |
| last lap start plus its `TotalTimeSeconds`, minus the first lap start | `timing.elapsedSeconds` |
| sum of `Lap/DistanceMeters` | `distance.meters` |
| `TotalTimeSeconds`-weighted mean of `Lap/AverageHeartRateBpm`, rounded (only when every lap reports it) | `cardiovascular.averageHr` |
| maximum of `Lap/MaximumHeartRateBpm` (only when every lap reports it) | `cardiovascular.maxHr` |
| sum of distances / sum of `TotalTimeSeconds` | `performance.averageSpeedMps` |
| maximum of `Lap/MaximumSpeed` (every lap) | `performance.maxSpeedMps` |
| Biking: time-weighted mean of `Lap/Cadence` (every lap; TCX states no maximum) | `performance.cadence` in RPM, maximum unknown |
| `Activity/Notes` | `metadata.description` (untrusted) |
| `Trackpoint`s | time series (section 8.5) |

`Lap`s also become the LAPS stream (section 8.6). Running cadence (in extensions) and
positions are not used.

### 8.3 GPX

One observation per `trk` (part = index). `trk/type`, case-insensitive: running, run =
RUNNING; cycling, biking, ride = CYCLING; swimming, swim = SWIMMING; tennis = TENNIS;
strength_training = STRENGTH; anything else or absent = OTHER. `trk/name` is
`metadata.name` (untrusted). Every track point needs a time.

| GPX | Canonical |
|---|---|
| first point time | `timing.startTimeUtc` |
| last point time minus first | `timing.elapsedSeconds` |
| sum of great-circle distances between consecutive points of each segment (haversine, Earth radius 6,371,008.8 m) | `distance.meters` |
| heart rate from `TrackPointExtension` (Garmin v1 or v2 namespaces): mean weighted by the time to the next point, rounded; maximum | `cardiovascular.averageHr`, `cardiovascular.maxHr` |
| cycling: cadence from `TrackPointExtension`, weighted mean and maximum | `performance.cadence` in RPM |
| points | time series (section 8.5) |

Speed, elevation gain and running cadence are not derived from GPX (noisy or ambiguous
units).

### 8.4 CSV activity template 1.0.0

The header row must equal, exactly (a UTF-8 byte-order mark is ignored):

```
start_time_utc,utc_offset,sport,subtype,elapsed_seconds,moving_seconds,distance_meters,elevation_gain_meters,average_hr,max_hr,rpe,name,comments
```

- One row is one activity (part = row index). Comma separator, UTF-8, no NUL bytes; at most
  the activity limit of rows.
- `start_time_utc` (ISO 8601 with `Z`) and `sport` (canonical code) are required; an empty
  cell means the value is not provided. `subtype` uses canonical codes; numbers use a dot as
  decimal separator.
- The rows are the athlete's own declarations: their observations are ATHLETE_ENTRY
  observations of a FILE_UPLOAD source (intentional spec change, P6), so `rpe` and
  `comments` are accepted as subjective fields.
- The same template is used for export (prompt-injection spec section 4). The export puts a
  single quote before formula-like text (including UTC offsets such as `-03:00`); the importer
  removes one leading quote when the rest of the cell starts with `=`, `+`, `-`, `@`, tab or
  carriage return, so an export can be imported again. The cell stays data either way.

Strength sets get their own template with the strength domain. Deferred from P10 to P12
(execution capture): in P10 the athlete enters sets with `coach strength log` (strength spec
section 5).

### 8.5 Time series

The time series of a session (offset from start in seconds, cumulative distance in metres,
heart rate, speed in m/s, cadence in the unit of `performance.cadence`, power in watts,
altitude in metres) is stored as a content-addressed blob in the athlete's blob store and
linked as the TIME_SERIES stream (contract
[../contracts/time-series.schema.json](../contracts/time-series.schema.json)). Positions are
not copied into it (data minimisation); they stay only in the RAW file. The normaliser runs
the DISTANCE_GPS_JUMP rule on the distance samples (activities spec section 5.1).

### 8.6 Laps, pool lengths and power sensors (rules 1.1.0, P9)

Laps and lengths are stored like the time series: content-addressed blobs in the athlete's
blob store, linked as the LAPS and LENGTHS streams (contracts
[../contracts/laps.schema.json](../contracts/laps.schema.json) and
[../contracts/lengths.schema.json](../contracts/lengths.schema.json)). Offsets are seconds
from the session start. Positions, serial numbers and other device identifiers are never
copied (data minimisation).

| Source | Lap field |
|---|---|
| FIT `lap` inside the session's time window: `start_time` minus session start; TCX `Lap/@StartTime` minus the first lap start | `startOffsetSeconds` |
| FIT `total_elapsed_time`; TCX: the next lap start minus this one (the last lap: unknown) | `elapsedSeconds` |
| FIT `total_timer_time`; TCX `TotalTimeSeconds` | `timerSeconds` |
| FIT `total_distance`; TCX `DistanceMeters` | `distanceMeters` |
| FIT `avg_heart_rate`, `max_heart_rate`; TCX `AverageHeartRateBpm`, `MaximumHeartRateBpm` | `averageHr`, `maxHr` |
| FIT `avg_cadence` plus `avg_fractional_cadence` (running: times 2, steps per minute); TCX `Cadence` (biking, RPM) | `averageCadence`, in the unit of `performance.cadence` |
| FIT `avg_power` | `averagePowerWatts` |
| FIT `intensity`: active, rest, warmup, cooldown, recovery, interval, other; TCX `Intensity`: Active, Resting | `intensity`: ACTIVE, REST, WARMUP, COOLDOWN, RECOVERY, INTERVAL, OTHER |
| FIT `lap_trigger`: manual, time, distance, position_* (POSITION), session_end, fitness_equipment (OTHER); TCX `TriggerMethod`: Manual, Distance, Location (POSITION), Time, HeartRate (OTHER) | `trigger`: MANUAL, TIME, DISTANCE, POSITION, SESSION_END, OTHER |

Pool lengths (FIT `length` messages of a pool swim, inside the session's window):
`startOffsetSeconds`, `elapsedSeconds` (`total_elapsed_time`), `timerSeconds`
(`total_timer_time`), `type` (`length_type`: ACTIVE or IDLE, a rest), `strokes`
(`total_strokes`) and `strokeType` (`swim_stroke`, as in section 8.1). The pool length is the
activity field `environment.poolLengthMeters`, so a correction of it applies to every
length.

Power sensor (FIT only): when a session has power (summary or samples), the file's
`device_info` messages are read for their device type only: ANT+ or BLE `bike_power` is
POWER_METER, ANT+ `fitness_equipment` or BLE `bike_trainer` is SMART_TRAINER (POWER_METER
when both appear), stored as `performance.powerSensor`; absent when the file names neither.
The field is evidence for the cycling power source (cycling spec section 2), never the
source itself: the athlete's declared sensors decide it.

Validation: a lap or length whose elapsed and timer times are both 0 or unknown is left out
(a double press of the lap button), and a time of 0 next to a positive one is UNKNOWN; the
others' offsets never decrease and lie inside the
session, their known durations are above 0 and at most 86,400 s, and counts and values are
non-negative. A lap or length list that breaks these rules is not stored (the session is
still imported); analyses that need it are then UNKNOWN with MISSING_DATA.

### 8.7 Strength sets (rules 1.2.0, P10)

The FIT `set` messages of a session that resolves to STRENGTH, inside its time window (as for
laps), become the SETS stream: a content-addressed blob of the source (contract
[../contracts/sets.schema.json](../contracts/sets.schema.json)), resolved with the other
streams (activities spec section 3, `STREAMS_PREFER_FIT`). Other sports keep no sets, and the
other formats have none.

| FIT `set` | Set field |
|---|---|
| `start_time` minus session start | `startOffsetSeconds` (a set without a start time is left out) |
| `duration` | `durationSeconds` |
| `set_type`: active, rest | `type`: ACTIVE, REST (the rest between sets) |
| `repetitions` | `repetitions` |
| `weight` (kilograms in the file, whatever the display unit) | `weightKg`; 0 is bodyweight only |
| `category` and `category_subtype` (arrays; at most 4 pairs read) | `exerciseId` and `providerExercise` |

Exercises: the parser writes each pair as the file's own code, `category/name` with the
exercise name of the subtype in the SDK profile (`<category>_exercise_name`),
`category/<number>` when the profile names no such subtype, or `category` alone when the
subtype is unknown (names are kept whole, up to 80 characters); categories the profile does
not name (the SDK gives their number) and `unknown` are left out. The parent maps the codes through the
FIT exercise mapping 1.0.0 (`coach.integrations.files.fit_exercises`): only exact
equivalents of catalogue exercises are listed, a loaded variant ("weighted_...") being the
same exercise, since its load is the set's weight. The set names a catalogue exercise only
when every code of the set maps to the same one; otherwise `exerciseId` is UNKNOWN and the
athlete can name it (strength spec section 5). `providerExercise` keeps the first code with
the `fit:` prefix, as provenance: it is never shown as an exercise name. Every name of the
mapping is checked against the SDK profile by a test, so none is invented.

Validation: a duration of 0 or above 86,400 s, repetitions that are not whole numbers from 0
to 1,000 and weights outside 0 to 1,000 kg are UNKNOWN. A set list whose offsets go back or
leave the session is not stored (the session is still imported).

## 9. Ingestion record

Contract: [../contracts/ingestion-record.schema.json](../contracts/ingestion-record.schema.json).
Immutable, one per upload attempt, owner-scoped: received and completed time, sanitised
original filename (untrusted display text), declared MIME type, detected type, size, SHA-256,
malware scan result, outcome (IMPORTED, DUPLICATE, REJECTED, FAILED), reason code and a
code-like detail, the files (archive members) with their RawSource and sessions, the
activities with their deduplication outcome, the record a duplicate refers to, and the
idempotency key.

### 9.1 Provider payloads (P7)

The same pipeline imports provider payloads (garmin spec section 4.1), and each ends in an
ingestion record with `origin` GARMIN_PERSONAL_API (contract 1.1.0, additive), `provider`
and `providerObjectId`:

- stages 1 and 3 (extension and declared MIME type) do not apply; the endpoint states the
  media type. A details record is JSON; a download must be detected as a ZIP.
- stage 5 does not apply: provider payloads are `NOT_APPLICABLE` for the malware scan
  (garmin spec section 6).
- a JSON provider record is decoded in process by the provider's record normaliser
  (garmin spec section 10) and becomes a PROVIDER_RECORD observation naming the provider and
  the record ID; ZIP members are decoded in the sandbox as uploads and become FIT_FILE or
  OTHER_FILE observations of origin GARMIN_PERSONAL_API, without a provider record ID. A CSV
  member is rejected (`CONTENT_MISMATCH`, `PROVIDER_CSV`): only the athlete's own files are
  athlete entries.
- the payload is stored as RAW before any check, rejected payloads included (INT-GAR-009);
  a download that is not a ZIP is stored as `UNKNOWN`. A payload that never arrived whole
  (over the size cap) leaves a REJECTED record (`TOO_LARGE`, `RESPONSE_LIMIT`) without a hash.
- observations take their origin from the stored RawSource: bytes already stored by an
  upload stay an upload's source, so the same file reached through Garmin is a tier-1
  duplicate.
