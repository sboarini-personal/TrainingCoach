# ADR-030: Sport session analytics: stream analyses, measured power, strokes and trends

- Status: Accepted
- Date: 2026-10-30
- Requirements: FR-RUN-001 to FR-RUN-004, FR-CYC-001 to FR-CYC-004, FR-SWM-001 to
  FR-SWM-004, FR-TEN-001 to FR-TEN-003, FR-MET-002 to FR-MET-006
- Amends: ADR-027 (subject dimensions)

## Context

P9 adds the analytics of each sport. The specs fixed what to detect but left methods,
parameters and origins to this phase, and several catalogue entries were marked "reference
to confirm". The analyses read streams (time series, laps, pool lengths; ADR-029), some only
make sense for some sessions (a steady run, a structured interval session, a pool swim), and
cycling power must never be assumed measured (AT-CYC-001).

## Decision

- **Stream analyses** (metrics spec 10): a metric read from a stream records the RAW source
  and the stream's SHA-256 as evidence. A session that does not qualify has no value (one it
  had is retired); a qualifying session with missing data is UNKNOWN. A value of the same
  stream and calculation version is reused without reading the stream again; each stream is
  read at most once per recomputation.
- **Origins, decided with the evidence found**: heart-rate drift and SWOLF are coaching
  practice (Friel's decoupling, swim coaching and device vendors), not published methods, so
  they are APPLICATION_DERIVED with this application's versioned rules; distance per stroke
  (Craig and Pendergast 1979), power zones (Allen and Coggan 2010) and mechanical work (SI)
  are STANDARD_CALCULATION. `ten.session_load` is the session RPE load of tennis sessions,
  not a second metric with the same formula.
- **Measured power** is decided in the EFFECTIVE view: a cycling file must name a power
  meter or smart trainer and the athlete must declare that kind of device reliable
  (`coach profile sensor`); nothing else ever makes power MEASURED, and every power analysis
  needs it. The FTP (`coach profile ftp`) applies to rides within 183 days of its date; an
  FTP not measured in a test is an estimate.
- **Dimensions**: the HR_ZONE dimension becomes ZONE (heart-rate or power zones, by metric),
  and STROKE joins for per-stroke swimming metrics (derived-metric-value 1.2.0, additive).
- **Qualifying-only totals**: the week's mechanical work adds only rides with measured power
  and names how many rides had none, instead of becoming UNKNOWN for every athlete without
  a power meter.
- **Trends are a read model**: per comparable group (sport, subtype, terrain, pool length,
  stroke), an ordinary least-squares slope with its t statistic and a confidence; groups are
  never combined, and a statement across them carries NON_COMPARABLE_SESSION.
  Interpretation stays with insights (P11) and the AI coach.
- **Corrections from the CLI** (`coach activity correct`): the way to record a session's RPE
  (Garmin's is not mapped, G-8), a tennis session's type or a pool length.

## Consequences

- Running analyses cost about 17 ms per hour of time series; a run that does not qualify is
  read again at each full recomputation, since nothing is stored for it.
- Every parameter (warm-up, steadiness, split rules, repetition tolerance, set size, load
  levels, terrain bands, trend thresholds) is an initial value under A-18.
- Known limits, stated in the specs: no gradient adjustment of running speed (halves at
  different altitudes carry ENVIRONMENT), temperature and workout type are not used for
  grouping, provider SWOLF is not mapped, and interval comparison with the plan arrives with
  plans (P12).
- The regional tennis load (levels, regions, 48 h window) is ready for the interference
  engine (P10) and recovery (P11).

## Verification

- Algorithms: `tests/unit/test_running_metrics.py`, `test_cycling_metrics.py`,
  `test_swimming_metrics.py`, `test_tennis.py`, `test_trends.py`.
- Engine and read models: `tests/unit/test_running_analyses.py`,
  `test_cycling_analyses.py` (AT-CYC-001), `test_swimming_analyses.py`, `test_trends.py`
  (AT-SWM-001), `test_tennis.py` (AT-TEN-001, load part).
- End to end: `tests/integration/test_cli_metrics.py`.
