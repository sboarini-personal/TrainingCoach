# Running analytics

Owner of FR-RUN-001 to FR-RUN-004. Master spec §28. Implemented in Phase P9.

## 1. Inputs (FR-RUN-001)

Analysed **only when available**: distance, duration, pace, splits/laps, heart rate, HR
zones, cadence, stride length, running power, elevation, vertical oscillation, ground contact
time, RPE. A missing input makes dependent analyses UNKNOWN with reason `MISSING_DATA`;
nothing is estimated silently.

Running power is device-specific: values from different devices or algorithms are not
compared (FR-MET-007).

## 2. Comparability

Sessions are comparable for trend analysis only when they share subtype (ROAD, TRAIL, TRACK,
TREADMILL) and none of these differ materially: terrain/elevation profile, temperature,
workout type. Otherwise the comparison carries `NON_COMPARABLE_SESSION` or `ENVIRONMENT`.
Treadmill pace and outdoor pace are never compared without that flag.

## 3. Detections

| Detection | Requirement | Inputs | Output |
|---|---|---|---|
| Pace/HR relationship change across sessions | FR-RUN-002 | pace, HR, comparable sessions | trend with confidence |
| HR drift within a steady session | FR-RUN-002 | steady segment, pace, HR | `run.hr_drift` value |
| Pacing stability | FR-RUN-003 | splits | variability metric |
| Interval degradation | FR-RUN-003 | interval laps vs plan | degradation across repetitions |
| Cadence change | FR-RUN-004 | cadence | trend |
| Efficiency change | FR-RUN-004 | pace per HR or per power, comparable sessions | trend |
| Fatigue pattern | FR-RUN-004 | the above plus RPE and recovery | pattern for insights |

Parameters (window sizes, steady-segment criteria, significance thresholds) are defined with
the metric definitions in P9 and versioned. Detections produce DerivedMetricValues and
feed Insights; interpretation is done by the AI coach.
