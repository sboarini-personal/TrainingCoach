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
