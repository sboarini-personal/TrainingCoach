# Acceptance: running, cycling, swimming, tennis, strength

### AT-CYC-001 — No assumed power meter
- Requirements: FR-CYC-002, FR-CYC-003, FR-CYC-004, FR-PLN-005
- Level: unit and contract. First executable: P9 (analytics), P12 (planning).

Implementation status (P9): the analytics part is executed by
`tests/unit/test_cycling_analyses.py` and `tests/integration/test_cli_metrics.py` (power of
unknown origin or from an undeclared device stays UNKNOWN, no power analysis or zones); the
workout part arrives with planning (P12).

**Given** an athlete with no reliable cycling power meter declared
**When** a ride containing power values of unknown origin is analysed and a cycling workout is planned
**Then** power is labelled UNKNOWN or ESTIMATED, never MEASURED
**And** no power-based analysis, FTP inference or power target is produced
**And** the workout uses RPE and, if valid, heart rate.

### AT-SWM-001 — Pool and open water are not compared directly
- Requirements: FR-SWM-001, FR-SWM-004
- Level: unit. First executable: P9.

Implementation status (P9): executed by `tests/unit/test_trends.py` (pool and open-water
sessions in separate groups, NON_COMPARABLE_SESSION across them).

**Given** a 25 m pool session and an open-water session
**When** a pace trend is computed
**Then** the sessions are not combined in one comparable series
**And** any cross-context statement carries NON_COMPARABLE_SESSION.

### AT-TEN-001 — Tennis counts as load
- Requirements: FR-TEN-001, FR-TEN-003, FR-MET-006
- Level: unit. First executable: P9 (load), P10 (interference).

Implementation status (P9): the load part is executed by `tests/unit/test_tennis.py`
(session RPE load per sport and across sports, HIGH regional load for the listed regions
during 48 hours); re-evaluating planned workouts arrives with interference (P10) and plans
(P12). Implementation status (P10): the interference engine assesses demanding tennis with
hard intervals and heavy legs within 48 hours as VERY_HIGH and tennis with calf and Achilles
intensive work within 24 hours as HIGH (`tests/unit/test_interference.py`); re-evaluating
the planned workouts arrives with plans (P12).

**Given** 120 minutes of demanding tennis (RPE 8)
**When** the next 24 to 48 hours are planned
**Then** the tennis adds session RPE load to the weekly total with sport context
**And** it adds neuromuscular load to calves, soleus, Achilles region, knees, adductors, shoulders and elbows
**And** planned running intervals and heavy lower-body strength in that window are re-evaluated.

### AT-STR-001 — Heavy legs are avoided before hard intervals
- Requirements: FR-STR-007
- Level: unit. First executable: P10.

Implementation status (P10): the assessment is executed by `tests/unit/test_interference.py`
(a planned strength block at RPE 7 the day before planned hard intervals is HIGH; a modifier
lowers it only with stated evidence, recorded with the base level) and end to end on
completed sessions by `tests/integration/test_cli_strength.py`. Not scheduling the heavy
work is the planner's (P12).

**Given** hard running intervals planned tomorrow
**When** strength is planned today
**Then** heavy lower-body work is not scheduled (interference HIGH)
**Unless** the stated evidence (strength history, recovery, phase) justifies it, in which case the evidence is recorded.

### AT-STR-002 — Strength is present in every plan
- Requirements: FR-STR-001, FR-PLN-008
- Level: unit and contract. First executable: P12.

**Given** an athlete with gym access and no contraindication
**When** any plan version is generated
**Then** every week contains at least one strength session
**And** when strength is omitted, the plan states the contraindication or impossibility.
