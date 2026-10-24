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
