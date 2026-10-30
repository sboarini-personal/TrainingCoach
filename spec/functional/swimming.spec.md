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
context statement and carries `NON_COMPARABLE_SESSION` (AT-SWM-001). P9: trends follow
metrics spec 11 (groups by subtype, pool length and stroke type).

## 5. Session metrics (P9, version 1.0.0)

Computed in code for each live SWIMMING activity of subtype POOL with a LENGTHS stream
(files spec 8.6), after every change (metrics spec section 10). Parameters are initial
values (assumption A-18).

- **Pool length**: `environment.poolLengthMeters`, else the pool of the athlete's resources
  (ESTIMATED_VALUE); without either, none of these metrics is computed (section 1).
- **Valid length**: an ACTIVE length with a known duration (timer time, else elapsed time), a
  known stroke count and a stroke type FREESTYLE, BACKSTROKE, BREASTSTROKE or BUTTERFLY
  (MIXED and DRILL lengths are left out: section 2). Strokes are counted as the device
  records them (a watch counts the cycles of the arm that wears it).
- **Set**: consecutive valid lengths of one stroke type, ended by an IDLE length (a rest) or
  a change of stroke. A set qualifies with at least 6 lengths; k = its length count // 3.

Per stroke type (subject dimension STROKE: `FREESTYLE`, `BACKSTROKE`, `BREASTSTROKE`,
`BUTTERFLY`), for a stroke with at least 4 valid lengths:

| metricId | Origin | Unit | Method |
|---|---|---|---|
| `swim.swolf_avg` | APPLICATION_DERIVED | score | mean length time in seconds + mean strokes per length; inputs `mean_length_seconds`, `mean_strokes`, `pool_length_meters` |
| `swim.distance_per_stroke` | STANDARD_CALCULATION | m/stroke | pool length x lengths / strokes; inputs `pool_length_meters`, `lengths`, `strokes`. Reference: Craig AB Jr, Pendergast DR. Relationships of stroke rate, distance per stroke, and velocity in competitive swimming. Med Sci Sports. 1979;11(3):278-283 |
| `swim.pace_variability` | APPLICATION_DERIVED | % | population standard deviation / mean of the length times x 100; inputs `mean_length_seconds`, `stdev_length_seconds` |

Per session, over its qualifying sets:

| metricId | Origin | Unit | Method |
|---|---|---|---|
| `swim.pace_degradation` | APPLICATION_DERIVED | % | (mean time of the last k lengths of the sets - of their first k) / first x 100, both pooled over the qualifying sets; inputs `first_lengths_seconds`, `last_lengths_seconds` |
| `swim.technical_fatigue` | APPLICATION_DERIVED | % | the same comparison of strokes per length, over the qualifying sets whose pace changed by at most 3 % (equal pace); inputs `first_lengths_strokes`, `last_lengths_strokes` |

Precision (P9, intentional): SWOLF comes from coaching practice and device vendors, not
from a published method, so the application's SWOLF is APPLICATION_DERIVED. A provider's
SWOLF stays a provider metric with its own label (FR-MET-003) and is not mapped in P9. The
pool length is an input, so trends compare only sessions of the same pool length and stroke
(section 4).
