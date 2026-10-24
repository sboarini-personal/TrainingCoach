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
