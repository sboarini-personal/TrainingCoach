# Athlete profile, experience, availability, resources, health constraints

Owner of FR-PRF-001 to FR-PRF-007. Master spec §5 to §7.

General rules:

- Unknown values are `UNKNOWN` (`null`). The system never invents athlete data (FR-PRF-002).
- Every field records its provenance: `ATHLETE_DECLARED`, `PROVIDER` (with provider name) or
  `DERIVED` (with metric ID), plus `updatedAt`.
- Mutable aggregates use optimistic concurrency (NFR-REL-003).
- Updates go through allow-listed commands (SEC-AUTHZ-003): a profile update can set only the
  athlete-editable fields; identity (`athleteId`) and `version` are never part of an update.
- A field that was never recorded is absent; a field recorded as unknown has value `null`
  with its provenance (who recorded it and when). The onboarding planner uses this
  distinction (FR-ONB-002).
- Free-text fields are untrusted: control characters are removed and lengths are capped.

## 1. AthleteProfile (FR-PRF-001)

| Group | Field | Type / unit | Validation |
|---|---|---|---|
| identity | athleteId | UUID | system-assigned |
| identity | displayName | string | 1 to 80 chars |
| physiology | birthDate | date, or `age` integer | age 18 to 100 in V1 (A-17) |
| physiology | heightCm | number, cm | 100 to 250 |
| physiology | weightKg | number, kg | 30 to 250 |
| heartRate | restingHR | integer, bpm | 25 to 120 |
| heartRate | maxHR | integer, bpm | 100 to 230 |
| heartRate | existingZones | list of zone models | see 1.1 |
| heartRate | zoneCalculationMethod | enum | PROVIDER, PERCENT_MAX_HR, HEART_RATE_RESERVE, LACTATE_THRESHOLD_HR, CUSTOM, UNKNOWN |
| preferences | timezone | IANA name | must exist in tz database |
| preferences | metricUnits | boolean | default true |
| preferences | language | BCP 47 tag | default `es` (FR-PRF-007, PROPOSED) |
| devices | garminModel | string | free text, untrusted |
| sensors | sensors[] | `{type, model, reliableForTraining}` | type: HR_STRAP, CYCLING_POWER_METER, CADENCE_SENSOR, RUNNING_POD, SMART_TRAINER, OTHER |

### 1.1 Heart-rate zones

- A zone model is `{zoneModelId, method, source, boundariesBpm[], validFrom}`.
  `boundariesBpm` holds 2 to 11 strictly ascending values between 30 and 230; zone *i* spans
  `[boundariesBpm[i], boundariesBpm[i+1])`.
- Zones come only from the athlete, the provider, or a standard calculation over **measured
  or declared** anchors (maxHR, restingHR, LTHR). The zone `source` values are
  ATHLETE_DECLARED, PROVIDER and CALCULATED_FROM_MEASURED_ANCHORS; there is deliberately no
  value for age-predicted anchors.
- Age-predicted maximum heart rate MUST NOT be used to prescribe intensity. Without reliable
  anchors, intensity is prescribed by RPE (FR-PLN-005).
- When both are known, restingHR MUST be lower than maxHR.

## 2. Sport experience (FR-PRF-003)

Stored independently per sport:

| Sport | Fields |
|---|---|
| Running | yearsExperience, weeklyDistanceKm, longestDistanceKm, recent5K, recent10K, recentHalfMarathon, recentMarathon (each `{timeSeconds, date, source}`) |
| Cycling | yearsExperience, weeklyDistanceKm, usualRideDistanceKm, longestRideKm, ftpWatts `{value, date, source, measured}`, powerMeterAvailable |
| Swimming | yearsExperience, weeklyDistanceM, typicalPace100mSeconds, longestDistanceM, poolLengthM |
| Strength | yearsExperience, sessionsPerWeek, familiarExerciseIds[], approximateLoads[] `{exerciseId, loadKg, reps, date}`, rpeFamiliarity, rirFamiliarity |
| Tennis | sessionsPerWeek, typicalDurationMinutes, typicalRpe, profile (MATCH, PRACTICE, MIXED) |

## 3. Availability and resources (FR-PRF-004)

Availability:

- daysPerWeek (0 to 7), maximumHoursPerWeek, preferredRestDays[] (weekdays),
  dailyTimeWindows (weekday -> list of `{start, end}` local times), doubleSessionsAllowed,
  restrictions (free text, untrusted).

Resources:

- pool `{available, lengthM}`, bicycle, smartTrainer, gym, strengthEquipment[] (BARBELL,
  DUMBBELLS, KETTLEBELLS, BANDS, MACHINES, PULL_UP_BAR, BENCH, BODYWEIGHT_ONLY, OTHER),
  tennisSchedule[] `{weekday, start, durationMinutes, type}`.

## 4. AvailabilityException (FR-PRF-005)

Fields: `date`, `available`, `maximumDurationMinutes`, `allowedSports[]`, `reason`.

- At most one exception per date (updates use optimistic concurrency).
- Effective availability for date D:
  1. the exception for D, if any; else
  2. the recurring availability for D's weekday; else
  3. the default availability (athlete-level defaults). If no default is defined, the day is
     not plannable and DATA_INCOMPLETE is raised.
- Resolution is a pure function (AT-AVL-001).

## 5. Health constraints (FR-PRF-006)

`HealthConstraint`: `id`, `description` (athlete text, untrusted), `affectedRegions[]`,
`affectedSports[]`, `restrictions[]` (NO_IMPACT, NO_OVERHEAD, NO_HEAVY_LOWER_BODY,
MAX_RPE(n), NO_SPORT(sport), OTHER), `declaredAt`, `validUntil`, `source` (ATHLETE,
PROFESSIONAL_ADVICE_DECLARED).

- Constraints are athlete-declared. The system does not diagnose them (SAFE-MED-001).
- The policy engine denies proposals that violate an active constraint (POL-HEALTH-CONSTRAINT,
  AT-POL-001).
- An explicit "no constraints" declaration is stored, so the REQUIRED input is satisfied.

## 6. Language (FR-PRF-007, PROPOSED)

Athlete-facing text (coach messages, reports) uses the athlete's `language`. Specifications,
code and logs are in English (assumption A-14).
