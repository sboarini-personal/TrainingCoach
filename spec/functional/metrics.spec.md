# Metrics engine, metric registry and training load

Owner of FR-MET-001 to FR-MET-007. Master spec §25 to §27.

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
- Each input records its name, the **value used**, its evidence reference (down to normalized
  fields and raw sources) and its confidence. The snapshot makes every value reproducible
  even after the normalized data is rebuilt or corrected.
- `status` explains every UNKNOWN value without inventing a reason:

  | Status | When | value | uncertaintyReasons |
  |---|---|---|---|
  | COMPUTED | all inputs known, quality sufficient | number | the inputs' reasons (an estimated input stays visible) |
  | MISSING_INPUT | a required input is absent or UNKNOWN | null | MISSING_DATA plus the inputs' reasons |
  | BELOW_MINIMUM_QUALITY | lowest input confidence below `minimumDataQuality` | null | the inputs' reasons |

- `inputConfidence` is the lowest confidence among the inputs (LOW < MEDIUM < HIGH).
- Values are append-only. A recomputation (new data, override, new version) creates a new
  value and sets `supersededBy` on the old one; nothing else of a stored value ever changes.
- At most one **current** (not superseded) value exists per athlete, metric and subject.
- Recomputing with the same calculation version and the same input snapshot returns the
  current value instead of storing a duplicate (idempotent recomputation).

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
| `any.distance_total` | STANDARD_CALCULATION | P8 | sum within one sport only |
| `any.duration_elapsed`, `any.duration_moving` | RAW_PROVIDER or STANDARD_CALCULATION | P8 | |
| `any.speed_avg`, `run.pace_avg`, `swim.pace_100m_avg` | STANDARD_CALCULATION | P8 | pace is derived from speed |
| `any.srpe_load` | STANDARD_CALCULATION | P3 | defined in section 8 |
| `any.hr_zone_time` | STANDARD_CALCULATION | P8 | requires a valid zone model |
| `any.weekly_volume_by_sport` | APPLICATION_DERIVED | P8 | |
| `any.training_monotony`, `any.training_strain` | STANDARD_CALCULATION | P11 | reference to confirm (Foster) |
| `run.hr_drift` | STANDARD_CALCULATION | P9 | first-half vs second-half pace:HR; reference to confirm |
| `run.interval_degradation` | APPLICATION_DERIVED | P9 | |
| `cyc.power_distribution` | STANDARD_CALCULATION | P9 | measured power only |
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
