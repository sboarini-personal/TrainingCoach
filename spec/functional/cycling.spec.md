# Cycling analytics

Owner of FR-CYC-001 to FR-CYC-004. Master spec §29. Implemented in Phase P9.

## 1. Inputs (FR-CYC-001)

Analysed when available: distance, duration, speed, heart rate, cadence, elevation, power,
FTP, power distribution, fatigue indicators. Subtypes: ROAD, MOUNTAIN, GRAVEL,
INDOOR_TRAINER, OTHER.

## 2. Measured vs estimated power (FR-CYC-002)

Every power value carries `source`:

| Source | When |
|---|---|
| MEASURED | the athlete declared a power meter or a power-measuring smart trainer as reliable (`sensors[].reliableForTraining`) **and** the activity's power stream is attributable to it |
| ESTIMATED | power is identified as estimated or virtual (calculated from speed or a model) |
| UNKNOWN | the origin of the power values cannot be determined |

Attribution (P9): a source records `power.source` UNKNOWN; the EFFECTIVE view decides it.
Power of a CYCLING activity is MEASURED when its `performance.powerSensor` (files spec
8.6, correctable by the athlete) is POWER_METER and the athlete declares a
CYCLING_POWER_METER with `reliableForTraining`, or SMART_TRAINER and the athlete declares a
reliable SMART_TRAINER. No V1 source identifies estimated or virtual power, so ESTIMATED
is never set by V1 rules; everything else stays UNKNOWN. The sensors are declared with
`coach profile sensor`.

Rules:

- `UNKNOWN` is treated as **not measured**.
- Without a reliable power meter, measured cycling power MUST NOT be assumed (AT-CYC-001):
  no power-based analysis, no power targets in workouts, no FTP inference.
- Estimated power may be displayed with its label and LOW confidence. It is never mixed with
  measured power in trends.

## 3. FTP (FR-CYC-003)

FTP is used only when available with provenance: athlete-declared (with date), provider
value, or a test executed with **measured** power. It is subject to the freshness window of
the onboarding spec. FTP is never estimated from heart rate or speed alone.

## 4. Power analyses (FR-CYC-004)

Only with MEASURED power: power distribution (time in power zones relative to FTP when FTP is
valid), within-session fade, interval power consistency, and mechanical work (kJ).

## 5. Without power

Without measured power, cycling analysis uses duration, distance, speed (with terrain and
wind caveats, `ENVIRONMENT`), heart rate when reliable, cadence and RPE. Speed is not used as
an intensity proxy across different terrain.

## 6. Session metrics (P9, version 1.0.0)

Computed in code for each live CYCLING activity (metrics spec section 10), only when its
power is MEASURED in the EFFECTIVE view (section 2): a ride without measured power has none
of these values (AT-CYC-001). Parameters are initial values (assumption A-18).

| metricId | Origin | Unit | Method |
|---|---|---|---|
| `cyc.work_kj` | STANDARD_CALCULATION | kJ | mechanical work: the sum of power x duration over the intervals of the time series at most 20 s long with a known power, / 1000 (SI: 1 J = 1 W x 1 s) |
| `cyc.power_zone_time` | STANDARD_CALCULATION | s | time in each of 7 power zones relative to FTP (6.1); dimension ZONE `Z1` to `Z7` |
| `cyc.power_fade` | APPLICATION_DERIVED | % | (mean power of the first half - of the second half) / first x 100, halves of the moving time with a known power; rides with at least 1,200 s of it |
| `cyc.interval_power_consistency` | APPLICATION_DERIVED | % | coefficient of variation of the average power of the work laps of a structured interval session (running spec 4.3 rules: a REST or RECOVERY lap, at least 3 work laps, timer times within 15 % of their median) |

Precision (P9, intentional): the catalogue's `cyc.power_distribution` is the set of
`cyc.power_zone_time` values, one per zone, like `any.hr_zone_time`.

Moving time here is every interval of at most 20 s (a coasting second at 0 W counts, as in
average power); an interval without a power value is unknown time.

The week report adds `cyc.work_total` (metrics spec 9.3): the sum of `cyc.work_kj` of the
week's rides with measured power, naming how many rides had none.

### 6.1 Power zones

- FTP applies to a ride whose training day is at most 183 days (the 6-month freshness
  window of the onboarding spec) before or after the FTP's date; otherwise the ride has no
  power zones. An FTP not measured in a test with measured power adds ESTIMATED_VALUE.
- Zones as fractions of FTP (Allen H, Coggan A. Training and Racing with a Power Meter. 2nd
  ed. VeloPress; 2010): Z1 below 0.55, Z2 0.55 to 0.75, Z3 0.75 to 0.90, Z4 0.90 to 1.05,
  Z5 1.05 to 1.20, Z6 1.20 to 1.50, Z7 1.50 and above; each zone includes its lower bound.
- Inputs per zone: `seconds_in_zone` (the stream as evidence) and `ftp_watts` (evidence:
  the athlete profile, field `experience.cycling.ftp`). Coverage below 90 % of the
  intervals with a known power adds MISSING_DATA, as for heart-rate zones.
