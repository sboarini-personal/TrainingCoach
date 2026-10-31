# Reporting

Owner of FR-RPT-001 to FR-RPT-003. Master spec §51. Deterministic parts in Phase P11,
narrative in Phase P13.

## Structure of every report

Each report has two parts:

1. **Data part:** computed in code (metrics engine, compliance, recovery assessment,
   insights). Every number has an evidence reference.
2. **Narrative part:** written by the AI coach from the data part only. It contains no
   number that is absent from the data part (FR-AGT-004).

Every report includes **Confidence** and **Missing data** sections. Periods follow the
athlete's timezone; weeks run Monday to Sunday by training day.

## Post-activity report (FR-RPT-001)

Summary, Planned vs Actual, What Went Well, Deviations, Insights, Recovery, Plan Impact,
Confidence, Missing Data.

## Weekly report (FR-RPT-002)

Adherence, volume by sport, intensity distribution, strength by muscle group, tennis load,
recovery, trends, alerts, progression toward the A goal, changes for the following week.

## Monthly report (FR-RPT-003)

Performance evolution, comparison with the previous period, consistency, training load,
recovery, strength progression, risks, cautious forecast, recommendations.

The forecast states its assumptions, confidence and uncertainty reasons and never promises an
outcome (FR-GOL-005).

## Data part 1.0.0 (P11, precision)

`coach report activity <id>`, `coach report week [--week]`, `coach report month [--month]`,
each with `--json` (contract
[../contracts/report-data.schema.json](../contracts/report-data.schema.json)). Reports are
read models: computed when asked, never stored.

- Every item is a number with at least one evidence reference (enforced by the domain and
  the contract), a code or text, or unknown (null).
- A section the data cannot fill yet is **not available**, with its reason, never filled
  with an invented value:
  - `NEEDS_PLANS`: Planned vs Actual, Deviations, Plan Impact, Adherence, Changes for the
    following week while there is no ACTIVE plan (P12: filled from the plan, below);
  - `NEEDS_GOAL_METRICS`: Progression toward the A goal and Cautious forecast without a
    primary goal, and the forecast without a current estimate and a target time (a forecast
    never promises an outcome, FR-GOL-005);
  - `NARRATIVE`: What Went Well, Recommendations (the AI coach, P13);
  - `NO_CONSENT`: activity sections without the ACTIVITIES consent;
  - `NO_DATA`: nothing to report in the period.
- **Confidence** is the lowest confidence of the report's numbers (LOW without any), with the
  union of their uncertainty reasons, plus MISSING_DATA when data is missing. **Missing
  data** lists the sections not available and the items whose value is unknown.
- Insights appear only while the consents of their data are granted (insights spec 5).
- **Post-activity:**
  - Summary: sport, training day, the recorded totals, average heart rate and RPE (field
    evidence), and the activity's current derived values;
  - Insights whose days include the training day;
  - Recovery: the assessment in force for the training day.
- **Weekly (ISO week):**
  - Volume by sport: per sport the activities and period totals, and the cross-sport
    totals;
  - Intensity distribution: zone time of five-zone models, per zone;
  - Strength by muscle group: hard sets and volume load;
  - Tennis load: sessions, `ten.neuromuscular_load` and session RPE load;
  - Recovery: the assessment of each day;
  - Trends: monotony and strain of an ended week, and trend and load insights;
  - Alerts: active conditions and the other insights.
- **Monthly (calendar month):**
  - Performance evolution: the comparable-group trends of `run.efficiency_hr`,
    `run.hr_drift` and `cyc.power_fade` over the 5 weeks to the month's end;
  - Comparison with the previous period: activities and the sums of the daily session RPE
    load and elapsed time, this month and the month before (unknown when a day's total is);
  - Consistency: sessions, training days, the longest gap between training days (only
    with two training days or more);
  - Training load: the session RPE load, monotony and strain of the weeks the month
    touches;
  - Recovery: days per assessment status;
  - Strength progression: volume load per week;
  - Risks: the month's insights.

Data part 1.1.0 (P12, precision): the sections that needed plans and goals.

- **Post-activity** (from the ACTIVE plan's executions, planning spec 6):
  - Planned vs Actual: the activity's execution status, the planned workout, and the
    duration and distance done as a share of the plan (evidence: the activity and the
    execution, `WORKOUT_EXECUTION` with the planned workout's ID);
  - Deviations: each measured share outside 80 % to 120 %, and a replacement;
  - Plan Impact: the adaptation decisions whose evidence names the activity, with the
    version they proposed.
- **Weekly:**
  - Adherence: the planned workouts due, how many were complete, partial, skipped and
    replaced, the unplanned activities, and the share done as planned (evidence: the plan
    version and the executions); NO_CONSENT without the activities consent;
  - Progression toward the A goal: the primary goal's feasibility (goals spec 4):
    classification, the date assessed, the target time (the athlete's, as text), the
    current estimate for the goal distance and the progression needed per week (evidence:
    the profile's race result), and the reasons;
  - Changes for the following week: the adaptation decisions of the week other than
    MAINTAIN, with their interpretation and the version proposed.
- **Monthly** Cautious forecast: the realistic and minimum satisfactory goals of the
  feasibility rules (evidence: the race result), the classification, and the statement
  that it is an estimate from the athlete's data, never a guarantee of the outcome.
