# Swimming analytics

Owner of FR-SWM-001 to FR-SWM-004. Master spec §30. Implemented in Phase P9.

## 1. Pool vs open water (FR-SWM-001)

Every swim activity has subtype `POOL`, `OPEN_WATER` or UNKNOWN (`null`).

- POOL requires a pool length (from the file or the athlete's resources); otherwise the
  length is UNKNOWN and per-length metrics are not computed.
- UNKNOWN subtype is treated as non-comparable with both POOL and OPEN_WATER.
- Open-water distance comes from GPS and has reliability LOW by default (assumption A-18).

## 2. Pool metrics (FR-SWM-002)

When available: distance, pace per 100 m, lengths and laps, rests, stroke count, stroke rate,
distance per stroke, stroke type, SWOLF.

SWOLF is reported only when it is **actually available and valid**: pool swim, known pool
length, length time and stroke count recorded for the length, single stroke type for the
length. Otherwise SWOLF is UNKNOWN. Provider SWOLF and application-calculated SWOLF keep
their own origin labels (FR-MET-003).

## 3. Analyses (FR-SWM-003)

Consistency (length-to-length pace variability), efficiency (distance per stroke, SWOLF
trend for the same stroke and pool length), pace degradation across a set, and technical
fatigue (stroke count or rate increase at equal pace late in a set).

## 4. Comparability (FR-SWM-004)

Pool and open-water sessions are not compared directly. Pool sessions are compared only for
the same pool length and stroke type. A comparison across contexts requires an explicit
context statement and carries `NON_COMPARABLE_SESSION` (AT-SWM-001).
