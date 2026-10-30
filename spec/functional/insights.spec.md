# Pattern detection and insights

Owner of FR-INS-001 to FR-INS-003. Master spec §40 and §41. Implemented in Phase P11.

## 1. Insight model (FR-INS-001)

Per [../contracts/insight.schema.json](../contracts/insight.schema.json): `insightId`, `kind`
(pattern ID), `observation`, `interpretation`, `hypothesis`, `recommendation`, `evidence[]`
(at least one reference), `confidence`, `uncertaintyReasons[]`, `relationship`,
`generatedAt`, `calculationVersions`, `agentVersion`.

The four statements are always separate:

| Part | Content | Produced by |
|---|---|---|
| Observation | what the data shows, with numbers from derived metrics | code |
| Interpretation | what it probably means for training | AI, constrained by evidence |
| Hypothesis | a possible explanation, explicitly tentative | AI |
| Recommendation | a proposed training action, subject to the policy engine | AI, then policy |

Observations never contain numbers that are not present in the referenced evidence
(FR-AGT-004).

## 2. Pattern catalogue (FR-INS-002)

| Pattern ID | Detects | Main inputs |
|---|---|---|
| PATTERN.PACE_DETERIORATION | pace decline at comparable effort | running metrics |
| PATTERN.HR_DRIFT | cardiovascular drift in steady sessions | `run.hr_drift` |
| PATTERN.POWER_DEGRADATION | power fade in sessions or trends | measured power |
| PATTERN.EXCESSIVE_INTENSITY | too much time at high intensity | zone time, sRPE |
| PATTERN.INSUFFICIENT_EASY | too little low-intensity training | zone time |
| PATTERN.ACCUMULATED_FATIGUE | fatigue building over days | recovery signals, sRPE |
| PATTERN.INSUFFICIENT_RECOVERY | recovery below baseline | recovery assessment |
| PATTERN.MONOTONY | low day-to-day load variation | monotony, strain |
| PATTERN.AGGRESSIVE_PROGRESSION | load rising faster than the athlete's history supports | weekly load history |
| PATTERN.STRENGTH_ENDURANCE_INTERFERENCE | interference events | interference assessments |
| PATTERN.TENNIS_INTERFERENCE | tennis-driven interference | tennis regional load |
| PATTERN.SLEEP_PERFORMANCE | association between sleep and performance | sleep, performance metrics |
| PATTERN.PLATEAU | no improvement over a period | goal-relevant metrics |
| PATTERN.TECHNICAL_EFFICIENCY | efficiency decline (swim, run) | SWOLF, distance per stroke, cadence |
| PATTERN.ADHERENCE | missed or partial workouts | workout executions |
| PATTERN.PLANNED_VS_ACTUAL | systematic deviation from plan | compliance dimensions |

Detection parameters are versioned with the metrics they use.

## 3. Correlation is not causation (FR-INS-003)

- Relationships between variables are reported as `relationship = ASSOCIATION`, with sample
  size and time window. There is no causal relationship value in the contract.
- Hypotheses about causes are labelled as hypotheses.
- Agent evaluations reject causal wording without evidence.

## 4. Confidence

Confidence comes from data quality of the inputs, sample size and consistency of signals.
LOW confidence insights are shown with their uncertainty reasons and never drive major plan
changes alone.

## 5. Pattern rules 1.0.0 (P11, precision)

Detection is code (`coach.planning.patterns`, run by `coach insights detect`); every
threshold is an initial value under A-18, pending review. An observation states only numbers
present in its evidence, or computed in code from them, and says how (a mean, a share, a
ratio, a least-squares trend). Interpretation, hypothesis and recommendation stay null until
the AI coach (P13). An insight equal to a stored one (same pattern, days, observation and
evidence) is not stored again; `coach insights list` shows the stored ones.

| Pattern | Rule (window ending today, athlete's timezone) | Evidence | Confidence |
|---|---|---|---|
| MONOTONY | the last ended week's `any.training_monotony` above 2.0: a practical threshold traced to Foster's work, not stated in his 1998 abstract | monotony and strain values | the values' input confidence |
| AGGRESSIVE_PROGRESSION | the last ended week's session RPE load at least 1.5 times the mean of the four weeks before it (a week without activity counts 0; an unknown week total: not evaluated). Acute:chronic workload ratio, Gabbett TJ, Br J Sports Med 2016;50(5):273-280; a contested index (Impellizzeri and others), so an observation only | the five week totals | at most MEDIUM |
| INSUFFICIENT_RECOVERY | assessment REDUCE or REST on 3 or more of the last 7 days, a safety hold aside | the assessments | their lowest |
| ACCUMULATED_FATIGUE | fatigue 4 or more on 3 or more of the last 5 check-ins of 7 days, the latest included | the check-ins | MEDIUM |
| HR_DRIFT, PACE_DETERIORATION, POWER_DEGRADATION, TECHNICAL_EFFICIENCY | a comparable group's 8-week trend (metrics spec 11) worsening by 5 % or more with MEDIUM or HIGH confidence: `run.hr_drift` rising, `run.efficiency_hr` falling, `cyc.power_fade` rising, `swim.swolf_avg` rising (per stroke); groups are never combined | the group's values | the trend's |
| STRENGTH_ENDURANCE_INTERFERENCE, TENNIS_INTERFERENCE | interference rules 1.0.0 (strength spec 7) in 7 days: combinations of level HIGH or above, and TENNIS_INTERVALS_HEAVY_LEGS of MODERATE or above | the combinations' evidence | MEDIUM; LOW when one is uncertain |
| EXCESSIVE_INTENSITY, INSUFFICIENT_EASY | heart-rate zone time of 14 days, five-zone models, at least 3 sessions and 2 h: zones 4 and 5 above 20 %, zones 1 and 2 below 70 % (informed by Seiler and Kjerland 2006) | the sessions' `cardiovascular.hrZones` | MEDIUM; LOW with an age-predicted model (ESTIMATED_VALUE) |
| SLEEP_PERFORMANCE | ASSOCIATION of the night's sleep duration (provider metric, SLEEP consent) and `run.efficiency_hr` of that training day: Spearman rank correlation at least 0.5 in absolute value over 10 or more runs in 56 days; NON_COMPARABLE_SESSION when the runs span comparable groups | each pair's two values | LOW; MEDIUM from 20 pairs and 0.6 |

- Not evaluated in P11, and reported as such:
  - PLATEAU (`NEEDS_GOAL_METRICS`): goal-relevant metrics arrive with planning;
  - ADHERENCE and PLANNED_VS_ACTUAL (`NEEDS_PLANS`): workout executions and plans arrive in
    P12;
  - any pattern whose data has no consent (`NO_CONSENT`).
- An association names its variables, sample size and window, and its observation ends "An
  association, not a cause." (FR-INS-003). The sleep variable is named
  `<provider>.sleep_duration`.
- Event: `insight.detected` (the pattern ID only).
