# Tennis

Owner of FR-TEN-001 to FR-TEN-003. Master spec §36. Implemented in Phase P9.

## 1. Classification (FR-TEN-001)

Tennis is `recreational = true` **and** `countsAsTrainingLoad = true`. It is never excluded
from workload because it is recreational. Recurring tennis from the athlete's schedule
appears in plans as planned `TENNIS` sessions; extra tennis is an UNPLANNED activity
(FR-ACT-011).

## 2. Capture (FR-TEN-002)

Per session, where possible: duration, heart rate (average, maximum, zone time), RPE (CR-10),
type (MATCH, PRACTICE, UNKNOWN), court surface (HARD, CLAY, GRASS, CARPET, OTHER), soreness,
pain (flag, intensity, location). Missing values remain UNKNOWN; RPE is requested from the
athlete after the session.

## 3. Load (FR-TEN-003)

- Internal load: session RPE load (`ten.session_load`) and heart-rate zone time when
  available. Tennis load is included in weekly totals with sport context (FR-MET-006).
- Regional neuromuscular load: each session adds load to calves, soleus, Achilles region,
  knees, adductors, shoulders and elbows. The weighting is qualitative (LOW, MODERATE, HIGH)
  from duration, RPE and type (matches higher than practice), and is versioned.
- Regional load feeds the interference engine (FR-STR-007) and recovery assessment.

Example (AT-TEN-001): 120 minutes of demanding tennis contributes to total load and to
neuromuscular load for the following 24 to 48 hours. Planned running intervals and heavy
lower-body strength in that window are re-evaluated.

## 4. Session metrics (P9, version 1.0.0)

- **Session RPE load**: `ten.session_load` is the session RPE load of a TENNIS activity,
  `any.srpe_load` (metrics spec 8, Foster 2001). Precision (P9, intentional): it is not
  registered as a second metric with the same formula, which would store two values of the
  same quantity; the week report already shows it per sport (TENNIS) and across sports
  (FR-MET-006). Without the athlete's RPE it is UNKNOWN; RPE is recorded with
  `coach activity correct <id> subjective.rpe <0-10>`.
- **Regional neuromuscular load** (`ten.neuromuscular_load`, APPLICATION_DERIVED, unit
  level: 1 LOW, 2 MODERATE, 3 HIGH), assumption A-18:
  - inputs: `session_duration_seconds` (`timing.elapsedSeconds`), `rpe`
    (`subjective.rpe`), `match` (1 for MATCH, 0 for PRACTICE; an unknown type counts as 0
    and adds MISSING_DATA);
  - level by duration: under 45 minutes LOW, 45 to 90 minutes MODERATE, over 90 minutes
    HIGH; an RPE of 7 or more raises it one level, an RPE of 3 or less lowers it one level;
    a match raises it one level; the level stays between LOW and HIGH;
  - the regions it loads are fixed for tennis: calves, soleus, Achilles region, knees,
    adductors, shoulders and elbows, for the 48 hours after the session ends
    (`TENNIS_REGIONS`, `REGIONAL_LOAD_HOURS`); the interference engine (P10) and recovery
    (P11) read them. Without duration or RPE the level is UNKNOWN.

Example (AT-TEN-001): 120 minutes of tennis at RPE 8 is HIGH (over 90 minutes, RPE raises a
level that is already the highest) and adds 960 AU of session RPE load to the week.
