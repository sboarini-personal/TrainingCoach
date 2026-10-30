# Metrics engine, metric registry and training load

Owner of FR-MET-001 to FR-MET-007. Master spec §25 to §27. ADR-008, ADR-027.

## 1. Calculate in code (FR-MET-001)

All sport calculations (totals, averages, pace, unit conversion, HR drift, strength volume,
aggregations, load, interval statistics) run in deterministic application code
(`coach.metrics`). The LLM receives results with their metadata; it never computes them
(ADR-008, AT-MET-001).

## 2. MetricDefinition registry (FR-MET-002)

Every metric is registered in code with:

| Field | Meaning |
|---|---|
| metricId | stable ID, for example `run.hr_drift` |
| name | human-readable name |
| sport | sport or `ANY` |
| unit | canonical unit |
| method | formula or algorithm description |
| requiredInputs | input fields and their minimum availability |
| minimumDataQuality | lowest input confidence at which the metric is computed |
| calculationVersion | semantic version of the method |
| origin | see section 3 |
| reference | literature or provider documentation, when the method is not original |
| provider, providerMetricName | provider metrics only: the provider and its own name for the metric |
| subjectKinds | the subject kinds a value may describe (section 4); P8 |
| dimension | optional qualifier of the subject: SPORT or ZONE (section 4); P8 (ZONE was HR_ZONE until P9, when power zones joined) |
| crossSport | SPORT dimension only: whether a value may also cover all sports together; P8 |
| aggregation | SUM: a period total over contributions of the metric's single input; P8 |

Changing a formula requires a new `calculationVersion`. Values computed with older versions
are kept.

Registry rules (enforced when the registry is built):

- `metricId` is `<namespace>.<name>` and unique.
- Application metrics (STANDARD_CALCULATION, APPLICATION_DERIVED) use an application
  namespace: `any`, `run`, `cyc`, `swim`, `str`, `ten`, `rec`, `plan`, `goal`. They have a
  calculation in code and no provider.
- Provider metrics (RAW_PROVIDER, PROVIDER_DERIVED) use their provider's name as namespace
  (for example `garmin.`), name the provider and the provider's metric name, and have no
  calculation in code. A provider name can never be an application namespace.
- STANDARD_CALCULATION metrics must cite a reference.
- Every required input declares its name, unit and valid range; out-of-range input is a
  validation error, not an UNKNOWN value.
- (P8) An application metric has exactly one of: a calculation over its named inputs, or an
  aggregation over contributions of its single input. An aggregation is computed for DAY or
  WEEK subjects only.
- (P8) A metric with the SPORT dimension that is not cross-sport (distance, elevation) is
  never computed without a sport (FR-MET-006).

## 3. Metric origin (FR-MET-003, FR-MET-007)

| Origin | Meaning | Example |
|---|---|---|
| RAW_PROVIDER | value measured or recorded by a device or provider | average heart rate from the FIT session |
| PROVIDER_DERIVED | proprietary provider calculation | Garmin training effect, Body Battery |
| STANDARD_CALCULATION | published, non-proprietary method computed by this application | session RPE load |
| APPLICATION_DERIVED | this application's own method | weekly sets per muscle group, feasibility class |

- An application-calculated metric is never labelled as a provider metric (AT-MET-002).
- Provider metrics always carry the provider name and the provider's metric name.
- Proprietary metrics from different providers are never treated as equivalent or merged.

## 4. Derived values (FR-MET-004)

`DerivedMetricValue` (contract
[../contracts/derived-metric-value.schema.json](../contracts/derived-metric-value.schema.json)):
`valueId`, `athleteId`, `metricId`, `calculationVersion`, `subject`, `status`, `value`, `unit`,
`inputs[]`, `missingInputs[]`, `inputConfidence`, `uncertaintyReasons[]`, `computedAt`,
`supersededBy`.

- `subject` is `{kind, key}`: ACTIVITY (UUID), DAY (ISO date), WEEK (ISO week, `2026-W44`),
  EXERCISE (exercise ID).
- (P8, contract 1.1.0) `subject.dimension` optionally qualifies the subject: a sport code
  (`RUNNING`, ...) for the SPORT dimension, `Z1` to `Z10` for the ZONE dimension (heart-rate
  or power zones, by metric). The
  dimension is part of the subject: one current value exists per athlete, metric, kind, key
  and dimension. A metric declares whether it has a dimension; a value of a dimensioned
  metric without a dimension exists only for cross-sport metrics and covers all sports.
- (P8) An aggregation's inputs are its contributions: the same input name once per
  contributing item, each with its own evidence. It is COMPUTED when every contribution is
  known; when any is UNKNOWN it is MISSING_INPUT (value null), lists the known contributions
  as inputs, names the input as missing and lists the evidence of the unknown contributions
  (`missingContributions`). A period without contributions has no value; a period that lost
  its last contribution has its value retired (NOT_APPLICABLE).
- Each input records its name, the **value used**, its evidence reference (down to normalized
  fields and raw sources) and its confidence. The snapshot makes every value reproducible
  even after the normalized data is rebuilt or corrected.
- `status` explains every UNKNOWN value without inventing a reason:

  | Status | When | value | uncertaintyReasons |
  |---|---|---|---|
  | COMPUTED | all inputs known, quality sufficient | number | the inputs' reasons (an estimated input stays visible) |
  | MISSING_INPUT | a required input is absent or UNKNOWN | null | MISSING_DATA plus the inputs' reasons |
  | BELOW_MINIMUM_QUALITY | lowest input confidence below `minimumDataQuality` | null | the inputs' reasons |
  | NOT_APPLICABLE (P8) | the metric no longer applies to the subject: the activity's sport changed, the zone model has fewer zones or none, the period lost its last contribution | null, no inputs | none |

- NOT_APPLICABLE only supersedes a current value, so the history keeps what was computed
  before; readers treat it as no value. It is replaced again when the metric applies anew.
- `inputConfidence` is the lowest confidence among the inputs (LOW < MEDIUM < HIGH).
- Values are append-only. A recomputation (new data, override, new version) creates a new
  value and sets `supersededBy` on the old one; nothing else of a stored value ever changes.
- At most one **current** (not superseded) value exists per athlete, metric and subject.
- Recomputing with the same calculation version and the same input snapshot returns the
  current value instead of storing a duplicate (idempotent recomputation). The snapshot
  includes the uncertainty reasons and the missing contributions (P8): a change of either
  alone is a new value.
- (P8) An input taken from a corrected field notes its correction in the evidence
  (`override <overrideId>`), so lineage reaches the athlete's correction, not only the
  source that recorded the original value.

## 5. Training load (FR-MET-005, FR-MET-006)

External load (what was done), per sport: duration, distance, elevation, mechanical work
(kJ) when measured power exists, repetitions, sets, volume load (sets x reps x kg).

Internal load (how the athlete responded): session RPE load (AU), time in heart-rate zones,
fatigue and recovery responses.

Rules:

- Cross-sport totals use only sport-independent internal-load units (session RPE AU, time)
  and always keep the per-sport breakdown.
- Distances are **never** summed or compared across sports as equivalent load.
- Tennis contributes session RPE load and regional neuromuscular load (FR-TEN-003).

## 6. Canonical units and conversion

Storage uses canonical units without rounding: metres, seconds, metres per second, beats per
minute, watts, kilograms, degrees Celsius, revolutions or steps per minute. Rounding happens
only at presentation. Conversion functions live in one module and are unit-tested.

## 7. Initial V1 catalogue

Formulas, references and parameters are fixed in the implementing phase and recorded in the
registry. Entries marked "reference to confirm" must not be described as published methods
until a reference is recorded.

| metricId | Origin | Phase | Notes |
|---|---|---|---|
| `any.distance_total` | APPLICATION_DERIVED (P8 decision) | P8 | sum within one sport only; section 9.3 |
| `any.duration_elapsed`, `any.duration_moving` | APPLICATION_DERIVED (P8 decision) | P8 | period totals; section 9.3 |
| `any.elevation_gain_total` | APPLICATION_DERIVED | P8 | added in P8: external load (section 5); one sport only |
| `any.srpe_load_total` | STANDARD_CALCULATION | P8 | added in P8: cross-sport internal load (section 5) |
| `any.speed_avg`, `run.pace_avg`, `swim.pace_100m_avg` | STANDARD_CALCULATION | P8 | pace is derived from speed; section 9.1 |
| `any.srpe_load` | STANDARD_CALCULATION | P3 | defined in section 8 |
| `any.hr_zone_time` | STANDARD_CALCULATION | P8 | requires a valid zone model; section 9.2 |
| `any.weekly_volume_by_sport` | APPLICATION_DERIVED | P8 | the week report of section 9.4, not a stored value |
| `any.training_monotony`, `any.training_strain` | STANDARD_CALCULATION | P11 | reference to confirm (Foster) |
| `run.hr_drift` | APPLICATION_DERIVED (P9 decision, running spec 4) | P9 | first-half vs second-half speed:HR of a steady run |
| `run.interval_degradation` | APPLICATION_DERIVED | P9 | running spec 4.3 |
| `run.pace_variability`, `run.efficiency_hr` | APPLICATION_DERIVED | P9 | added in P9: pacing stability and efficiency inputs (FR-RUN-003, FR-RUN-004); running spec 4 |
| `cyc.power_distribution` | STANDARD_CALCULATION | P9 | measured power only; registered as `cyc.power_zone_time` per zone (cycling spec 6) |
| `cyc.work_kj`, `cyc.work_total`, `cyc.power_fade`, `cyc.interval_power_consistency` | STANDARD_CALCULATION (work), APPLICATION_DERIVED (others) | P9 | added in P9: measured power only (cycling spec 6) |
| `swim.swolf_avg` | RAW_PROVIDER or STANDARD_CALCULATION | P9 | valid pool lengths only |
| `str.volume_load` | STANDARD_CALCULATION | P10 | sets x reps x kg |
| `str.weekly_sets_by_muscle` | APPLICATION_DERIVED | P10 | primary muscles count 1, secondary 0.5 (proposed) |
| `ten.session_load` | STANDARD_CALCULATION | P9 | session RPE |
| `garmin.training_effect_aerobic`, `garmin.training_effect_anaerobic` | PROVIDER_DERIVED | P7 | Garmin-specific |
| `garmin.body_battery`, `garmin.hrv_status`, `garmin.stress` | PROVIDER_DERIVED | P7 | consent-gated |

Names of proprietary third-party metrics (for example trademarked power-load indices) are not
used for application calculations.

## 8. Session RPE load (`any.srpe_load`, version 1.0.0)

The first registered metric, implemented in P3 as the reference implementation of the
registry and derived-value model (AT-MET-001).

| Field | Value |
|---|---|
| Origin | STANDARD_CALCULATION |
| Sport | any |
| Unit | AU (arbitrary units) |
| Method | session RPE (CR-10) multiplied by session duration in minutes |
| Inputs | `rpe`: CR-10 rating reported for the whole session, 0 to 10; `session_duration_seconds`: total session duration, greater than 0 and at most 86,400 |
| Minimum data quality | MEDIUM |
| Reference | Foster C, Florhaug JA, Franklin J, et al. A new approach to monitoring exercise training. J Strength Cond Res. 2001;15(1):109-115 |

Which activity field supplies the session duration (elapsed time by default) is decided by the
metrics engine in P8 and recorded in the input's evidence reference.

Decision (P8): `timing.elapsedSeconds`. Foster's session duration is the whole session,
warm-up, rests and cool-down included, which the elapsed time measures; the moving time
would leave the rests out. The field is recorded in the evidence of the
`session_duration_seconds` input.

## 9. Metrics engine (P8)

The engine (`coach.metrics` and `coach.application.analytics`) computes the metrics below
deterministically from the EFFECTIVE activity view (corrections applied). Every value is a
derived value with its input snapshot (section 4). Parameters are initial values to review
with real data (A-18).

Precision (P8, intentional spec decision): the catalogue proposed RAW_PROVIDER or
STANDARD_CALCULATION for the duration and distance entries. A value recorded by a device is
an activity field with its own provenance (origin RAW_PROVIDER), never re-registered as an
application metric in an application namespace (section 2). The registered entries are the
period totals of section 9.3, whose arithmetic is trivial but whose selection rules (which
activities, which training day, which sport) are this application's: APPLICATION_DERIVED.

### 9.1 Per-activity metrics

Computed after every change to a live activity (with `any.srpe_load`), for ACTIVITY
subjects. An input names the activity fields that may supply it, in order: the first known
one is used and recorded in the input's evidence; using a later one adds ESTIMATED_VALUE. An
out-of-range field value becomes UNKNOWN with SENSOR_QUALITY; a field with an anomaly adds
SENSOR_QUALITY. The input confidence is the activity's confidence.

| metricId | Sports | Unit | Method | Inputs | Minimum quality |
|---|---|---|---|---|---|
| `any.speed_avg` | RUNNING, CYCLING, SWIMMING | m/s | distance / duration | `distance_meters` (`distance.meters`, above 0 up to 1,000,000); `duration_seconds` (`timing.movingSeconds`, else `timing.elapsedSeconds`; above 0 up to 86,400) | MEDIUM |
| `run.pace_avg` | RUNNING | s/km | duration / (distance / 1000) | as `any.speed_avg` | MEDIUM |
| `swim.pace_100m_avg` | SWIMMING | s/100m | duration / (distance / 100) | as `any.speed_avg` | MEDIUM |

Reference for all three: BIPM, The International System of Units (SI), 9th edition, 2019
(speed as distance travelled per unit time; pace is its reciprocal, scaled). The device's
own average speed stays the field `performance.averageSpeedMps` (and the computed pace
fields of the canonical model); these metrics are comparable across sources because they
use the same definition for every file and provider. A metric applies only to its sports:
after a correction to another sport, its current value is retired (NOT_APPLICABLE, section 4)
and the computed values stay in its history.

### 9.2 Time in heart-rate zones (`any.hr_zone_time`, version 1.0.0)

| Field | Value |
|---|---|
| Origin | STANDARD_CALCULATION |
| Subject | ACTIVITY, dimension ZONE (`Z1` is the lowest zone) |
| Unit | s |
| Zone model | the athlete's zone model (profile spec 1.1) with the latest `validFrom` on or before the activity's training day; without one, nothing is computed. An AGE_PREDICTED model is applied to the training day: its bpm boundaries come from 220 minus the athlete's age on that day; when it gives no zones (age outside 18 to 100), nothing is computed |
| Series | the activity's resolved TIME_SERIES stream, heart-rate column |
| Method | each interval between consecutive samples counts toward the zone of the heart rate at its start, when that heart rate is known and the interval is longer than 0 and at most 20 s (longer gaps are pauses or recording gaps and count nowhere). Zone *k* is `[boundary k, boundary k+1)`; a heart rate at or above the last boundary counts in the top zone; below the first boundary it counts in no zone |
| Inputs | `seconds_in_zone` (the interval sum; evidence: the RAW source that supplied the stream, field `streams.timeSeries`, note the stream's SHA-256; confidence: the activity's), `zone_lower_bpm` and `zone_upper_bpm` (evidence: the zone model, kind `HR_ZONE_MODEL`, field `boundariesBpm[i]`; confidence HIGH). From an AGE_PREDICTED model the boundaries are the applied bpm, with evidence field `boundariesPercentMaxHr[i]` and a note naming the percentage, the age and the predicted maximum; confidence MEDIUM (an estimate the athlete chose, still at the minimum quality) and uncertainty ESTIMATED_VALUE, which every zone value from it carries (ADR-028) |
| Without a heart-rate series | every zone of the model is MISSING_INPUT (`seconds_in_zone`); so is a series whose stored blob is missing or damaged |
| Coverage | intervals of at most 20 s without a heart rate are unknown time (pauses are not); when the known time is below 90 % of known plus unknown time, every zone carries MISSING_DATA (its value is still the measured time) |
| Out of range | more than 86,400 s in one zone: that zone is MISSING_INPUT with SENSOR_QUALITY |
| Zones beyond the model | values of zones the applicable model lacks (or of every zone without a model) are retired (NOT_APPLICABLE) |
| Minimum data quality | MEDIUM |
| Reference | Seiler KS, Kjerland GØ. Quantifying training intensity distribution in elite endurance athletes: is there evidence for an "optimal" distribution? Scand J Med Sci Sports. 2006;16(1):49-56 (time-in-zone method). Age-predicted maximum: Fox SM, Naughton JP, Haskell WL. Physical activity and the prevention of coronary heart disease. Ann Clin Res. 1971;3(6):404-432 (220 minus age; its limits in ADR-028) |

The EFFECTIVE view fills `cardiovascular.hrZones` (model ID and seconds per zone) when every
zone of the model has a current COMPUTED value from that model. A zone change applies when
the metrics are recomputed (`coach metrics recompute`, run automatically after
`coach profile hr-zones`, and after `coach profile birth-date` when an AGE_PREDICTED model
exists).

### 9.3 Period totals (version 1.0.0)

| metricId | Origin | Unit | Contribution | Dimension | Sports |
|---|---|---|---|---|---|
| `any.duration_elapsed` | APPLICATION_DERIVED | s | `timing.elapsedSeconds` | SPORT, cross-sport | all |
| `any.duration_moving` | APPLICATION_DERIVED | s | `timing.movingSeconds` | SPORT, cross-sport | all |
| `any.distance_total` | APPLICATION_DERIVED | m | `distance.meters` | SPORT only | RUNNING, CYCLING, SWIMMING |
| `any.elevation_gain_total` | APPLICATION_DERIVED | m | `elevation.gainMeters` | SPORT only | RUNNING, CYCLING |
| `any.srpe_load_total` | STANDARD_CALCULATION (Foster 2001: weekly load as the sum of session loads) | AU | the current `any.srpe_load` value (evidence: the derived value) | SPORT, cross-sport | all |
| `cyc.work_total` (P9) | STANDARD_CALCULATION (SI: work adds up) | kJ | the current `cyc.work_kj` value; only rides that have one (measured power) contribute, and the report names how many rides had none | SPORT only | CYCLING |

A total is computed only for the sports it applies to: a tennis or strength week has no
distance total (not an UNKNOWN one), and OTHER, which groups unrelated activities, has none
either.

- Subjects: DAY (ISO date) and WEEK (ISO week, Monday to Sunday) of the activity's training
  day. Contributions: ACTIVE activities (EFFECTIVE view) of the period, of the sport when the
  subject has one.
- Aggregation SUM with `math.fsum` (exact, order-independent), with the semantics of section
  4: one UNKNOWN contribution makes the total MISSING_INPUT; no contribution, no value, and
  a stored total of the period that is no longer produced (its sport or the whole period lost
  its contributions) is retired when the period is read.
- Confidence: the lowest of the contributions (the activity's confidence; the derived
  value's input confidence for session RPE load). Uncertainty: the contributions' reasons,
  SENSOR_QUALITY for a field with an anomaly, ESTIMATED_VALUE when a training day is
  estimated. Minimum data quality LOW.
- Materialised when requested: reading a period computes its totals; an unchanged snapshot
  returns the stored value, a changed one supersedes it (section 4). Totals never go stale
  for a reader, whatever changed in between.

### 9.4 Week report (`any.weekly_volume_by_sport`)

A read model, not a stored value: for one ISO week, per sport, the number of activities and
the period totals of 9.3 (value and status, plus the sum of the known contributions and the
number of unknown ones, so a partial total is shown as such and never as the total); across
sports, only the cross-sport totals (time and session RPE load, FR-MET-006). Distances and
elevation are never summed across sports. External load (duration, distance, elevation) and
internal load (session RPE load) are reported separately (FR-MET-005). Mechanical work needs
measured power: from P9 `cyc.work_total` for rides with measured power; time in zones per period arrives with
recovery and insights (P11).

### 9.5 Reads, consent and recomputation

- Analytics reads (week report, activity metrics) require the ACTIVITIES consent
  (activities spec section 9) and are owner-scoped.
- `coach metrics recompute` recomputes the per-activity metrics of every live activity
  (idempotent: unchanged snapshots store nothing), for example after a zone change or for
  activities imported before a metric existed. It works in units of work of 50 activities
  (ADR-027) and reuses the zone times stored for the same stream and zone model instead of
  reading the series again.

## 10. Sport session analyses (P9)

Per-activity metrics of the sport specs (running 4, cycling 6, swimming 5, tennis 4) are
computed with the per-activity metrics of section 9.1, after every change to a live
activity and by `coach metrics recompute`.

- A metric that reads only activity fields is a binding as in 9.1. A metric that reads a
  stream (TIME_SERIES, LAPS or LENGTHS) records, for each input taken from it, the RAW
  source that supplied the stream as evidence (field `streams.timeSeries`, `streams.laps`
  or `streams.lengths`, note the stream's SHA-256) and the activity's confidence.
- Each metric applies to its sports and qualification rules. A session that does not
  qualify has no value, and a value it had is retired (NOT_APPLICABLE, section 4); a
  qualifying session whose data is missing is UNKNOWN (MISSING_INPUT) with its reason.
- A stream analysis reuses the numbers of its current value when that value used the same
  stream and calculation version, and recomputes the value with the activity's current
  confidence and reasons (idempotent store); otherwise it reads the stream again.
- Parameters are versioned with each metric (calculationVersion) and are initial values
  to review with real data (A-18).

