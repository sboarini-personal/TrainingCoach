# ADR-028: Age-predicted heart-rate zones as a labelled estimate

- Status: Accepted
- Date: 2026-10-29
- Requirements: FR-PRF-001, FR-PRF-002, FR-MET-004, FR-PLN-005
- Amends: profile spec section 1.1 (P1), which made age-predicted anchors unrepresentable

## Context

The athlete works with five zones defined as percentages of maximum heart rate (Z1 51-58 %,
Z2 59-70 %, Z3 71-80 %, Z4 81-90 %, Z5 90-100 %) and asked for the maximum to be 220 minus
age, so that the zones follow their age.

The profile spec allowed zones only from the athlete, the provider, or a calculation over
measured or declared anchors, deliberately without an age-predicted source, and forbade
using an age-predicted maximum to prescribe intensity. The reason stands: 220 minus age
(Fox, Naughton and Haskell 1971) was not derived from a study sample (Robergs and Landwehr,
J Exerc Physiol Online 2002;5(2):1-10), and an individual maximum commonly differs from it
by 10 bpm or more, enough to move a session into another zone. Tanaka, Monahan and Seals
(J Am Coll Cardiol 2001;37(1):153-156) proposed 208 minus 0.7 times age, which fits groups
better, but it is still a population estimate.

The conflict was raised with the athlete, who chose (2026-10-29) a labelled estimate over two
alternatives: zones over a declared maximum (no rule change), and 220 minus age as a reliable
anchor for prescription as well (removes a safety rule; not recommended).

## Decision

- A zone model may have source AGE_PREDICTED, method PERCENT_MAX_HR, and hold
  `boundariesPercentMaxHr` (2 to 11 ascending integers, 30 to 100) instead of bpm.
- It is applied per training day: maximum = 220 minus the athlete's age that day (from the
  birth date; a declared age alone is not enough, and a profile with such a model must have
  a birth date); boundary = `ceil(p * maximum / 100)`, the smallest whole bpm at or above
  *p* %. Ages outside 18 to 100 (A-17) give no zones. The formula is the one the athlete
  asked for, not Tanaka's.
- Time in zones computed from it carries ESTIMATED_VALUE; its boundary inputs have
  confidence MEDIUM (the metric's minimum, so the values are still computed) and evidence
  naming the percentage, the age and the predicted maximum. The CLI shows the values and the
  model as estimates.
- The model is never a reliable intensity anchor (`HeartRateZoneModel.reliable_anchor` is
  false): the planner (P12) and the onboarding readiness rules ignore it, and intensity
  without a reliable anchor stays prescribed by RPE. The MUST NOT on prescription is kept.
- Contract `athlete-profile` 1.1.0 (additive: a new source value and an optional field).

## Consequences

- The zones move with the athlete's age without re-entering them; activities of past years
  use the age the athlete had then.
- Changing the birth date recomputes the metrics (`coach profile birth-date`).
- Zone distributions from this model are descriptive; insights (P11) and the AI coach (P13)
  must present them as estimates.
- A measured maximum (test or race) later replaces the estimate with a new model and
  `validFrom`; older activities keep the model of their date.

## Verification

- `tests/unit/test_athlete_profile.py` (validation, application per day, birth date
  required, not a reliable anchor), `tests/unit/test_activity_metrics.py` (estimated
  values, evidence, recomputation after a birth date change),
  `tests/integration/test_cli_metrics.py` (commands), contract tests (schema 1.1.0).
