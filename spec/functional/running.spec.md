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

## 4. Session metrics (P9, version 1.0.0)

Computed in code for each live RUNNING activity after every change (metrics spec section 10),
from its EFFECTIVE view and streams. Parameters are initial values to review with real data
(assumption A-18). A metric whose session does not qualify (too short, not steady, not a
structured interval session) has no value; a qualifying session whose data is missing is
UNKNOWN with its reason.

| metricId | Origin | Unit | Method |
|---|---|---|---|
| `run.hr_drift` | APPLICATION_DERIVED | % | heart-rate drift of a steady run (4.1) |
| `run.pace_variability` | APPLICATION_DERIVED | % | variability of whole-kilometre splits (4.2) |
| `run.interval_degradation` | APPLICATION_DERIVED | % | speed lost from the first to the last repetitions (4.3) |
| `run.efficiency_hr` | APPLICATION_DERIVED | m/beat | distance covered per heartbeat: (distance / duration) x 60 / average heart rate; duration is the moving time, else the elapsed time (ESTIMATED_VALUE) |

Precision (P9, intentional): the catalogue proposed STANDARD_CALCULATION for `run.hr_drift`
with "reference to confirm". The first-half versus second-half ratio is coaching practice
(Friel's aerobic decoupling, published as coaching articles, which also uses a proprietary
grade-adjusted pace), not a published standard, and the window and steadiness rules below
are this application's; it is registered as APPLICATION_DERIVED. The physiology it reflects
is cardiovascular drift (Coyle EF, González-Alonso J. Cardiovascular drift during prolonged
exercise: new perspectives. Exerc Sport Sci Rev. 2001;29(2):88-92). Speed is not adjusted
for gradient: a run whose halves differ in mean altitude by more than 20 m carries
ENVIRONMENT.

Running power is device-specific and not used in P9 metrics (section 1).

### 4.1 Heart-rate drift (`run.hr_drift`)

- Moving intervals: consecutive samples of the time series at most 20 s apart, moving at
  1 m/s or more (from the cumulative distance, else the speed column).
- Analysis window: the moving intervals after the first 600 s of the session (warm-up). The
  run qualifies when the window has at least 1,200 s of moving time and is steady: split
  into consecutive 300 s blocks of moving time (a last shorter block is dropped), the
  coefficient of variation of the block speeds is at most 10 %.
- The window is cut in two halves of equal moving time. For each half: speed = distance /
  moving time; heart rate = time-weighted mean of the known heart rates.
- Inputs: `first_half_speed_mps`, `first_half_hr_bpm`, `second_half_speed_mps`,
  `second_half_hr_bpm`, each with the stream as evidence. Value = (first efficiency -
  second efficiency) / first efficiency x 100, with efficiency = speed / heart rate.
  Positive means the heart rate rose relative to speed.
- Heart rate known for less than 90 % of a half's moving time adds MISSING_DATA; none at all
  in a half makes it UNKNOWN. Without a time series, a distance or speed column, the run is
  UNKNOWN (MISSING_INPUT).

### 4.2 Pace variability (`run.pace_variability`)

- Splits: the moving time between consecutive whole kilometres of the distance covered in
  moving intervals (4.1), with linear interpolation at each kilometre mark; pauses and
  standing still do not count. The last partial kilometre is not a split.
- The run qualifies with at least 3 splits. Value = population standard deviation / mean of
  the split times x 100. Inputs: `split_mean_seconds`, `split_stdev_seconds`.
- A distance anomaly of the activity (for example DISTANCE_GPS_JUMP) adds SENSOR_QUALITY.

### 4.3 Interval degradation (`run.interval_degradation`)

- Needs the LAPS stream. A session is a structured interval session when it has at least one
  REST or RECOVERY lap and at least 3 work laps (ACTIVE or INTERVAL).
- The repetitions are comparable when every work lap has a known timer time and a distance
  within 15 % of the median work-lap distance; otherwise the session does not qualify.
- With n repetitions and k = max(1, n // 3): `first_reps_speed_mps` is the mean speed
  (distance / timer time) of the first k, `last_reps_speed_mps` of the last k. Value =
  (first - last) / first x 100: positive means the last repetitions were slower.
- The comparison with the planned repetitions arrives with plans (P12).
