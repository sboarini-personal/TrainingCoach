# ADR-033: Recovery assessment, pattern detection and report data

- Status: Accepted (athlete's review of 2026-10-31)
- Date: 2026-10-30
- Requirements: FR-REC-002 to FR-REC-004, FR-MET-002, FR-INS-001 to FR-INS-003, FR-RPT-001 to
  FR-RPT-003, FR-AGT-004
- Related: ADR-008 (calculate in code), ADR-031 (interference), ADR-032 (recovery data)

## Context

P11 turns check-ins, daily recovery data and the metrics of P8 to P10 into a daily recovery
decision, insights and the data part of the reports. The spec proposed baseline windows
("7-day vs 28-day"), a major-decision rule, a pattern catalogue and report sections, but
left several choices open:

- what a signal compares;
- which signals are independent;
- how a wearable and the athlete are weighed;
- which patterns can run before plans exist;
- how a report shows what it cannot know yet.

The AI coach (P13) must later write narratives only from numbers computed here.

## Decision

- **Assessment rules 1.0.0.**
  - A provider metric compares the day's reading with the athlete's 28 days before it, from
    7 readings, beyond one standard deviation with a floor per metric. A 7-day rolling mean
    is not used: one low reading would stay "persistent" for a week, against AT-REC-001.
  - Check-in items deviate at their documented anchors, and any pain is a signal.
  - Signals of one physiology count once: the autonomic group (resting HR, HRV, Body
    Battery) and the sleep group (measured sleep, sleep quality).
  - Decisions: three groups mean REST; two groups, one signal on three consecutive days, or
    pain of 7 or more mean REDUCE; one group means MONITOR; a safety hold means REST under
    its own rules.
  - A wearable never cancels the athlete. Both count, so the more conservative wins, and a
    disagreement is reported.
  - An unchanged re-assessment is not stored again.
- **Prescriptions follow from the decisive reason:**

  | Reason | Prescription | Days |
  |---|---|---|
  | three groups | REST | 1 |
  | severe pain | ACTIVE_RECOVERY | 1 |
  | two groups | EASY_SESSION | 2 |
  | persistent signal | REDUCED_VOLUME | 2 |

  A re-assessment of the day withdraws its prescription. RECOVERY_BLOCK follows the
  prescriptions in force and is cleared by re-evaluation (reason `REEVALUATED`).
- **Training monotony and strain** follow Foster 1998 (reference verified). They use the
  seven daily session RPE loads of an ended ISO week, with rest days as 0 and the population
  standard deviation. They have no value when the seven loads are equal.
- **Pattern rules 1.0.0** are pure functions in `coach.planning.patterns`: 13 of the 16
  catalogue patterns. PLATEAU, ADHERENCE and PLANNED_VS_ACTUAL are reported as not evaluated
  until goals and plans exist (P12).
  - Observations are written in code from evidence values, or from numbers computed from
    them and named as such.
  - Interpretation, hypothesis and recommendation stay null until P13.
  - Relationships are ASSOCIATION only (Spearman, with sample size and window).
  - Contested indices (the acute:chronic ratio) are capped at MEDIUM confidence.
  - A finding already stored is not stored again.
- **Report data parts** are read models of sections of items. Every number has evidence,
  enforced by the domain and by the `report-data` contract. A section the data cannot fill
  is not available, with its reason (NEEDS_PLANS, NEEDS_GOAL_METRICS, NARRATIVE, NO_CONSENT,
  NO_DATA), and is never filled with an invented value. Every report states its Confidence
  and its Missing data.

## Consequences

- Every threshold is an initial value under A-18. Real data may move them; a change is a new
  rules version.
- Check-in pain counts as a recovery signal, but restricting load on its region is the
  policy engine's (P12). The P10 strength progression reads only the pain reported on
  activities.
- Recent performance is not a recovery signal in rules 1.0.0.
- The P13 agent receives report-data documents and insights. A number absent from them
  cannot appear in a narrative (FR-AGT-004).
