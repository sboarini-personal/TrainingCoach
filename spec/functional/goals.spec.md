# Goals and events

Owner of FR-GOL-001 to FR-GOL-005. Master spec §8.

## 1. SportEvent (FR-GOL-001)

`eventId`, `name`, `sport`, `date`, `distanceMeters`, `location` (free text, untrusted),
`priority` (A, B, C), `expectedConditions` (free text or structured: terrain, climate,
elevation, open water, pool length).

## 2. Goal (FR-GOL-001, FR-GOL-002)

`goalId`, `athleteId`, `eventId` (optional), `sport`, `distanceMeters`, `targetTimeSeconds`
(optional), `targetDate`, `priority`, `baseline` (immutable snapshot, section 3), `createdAt`,
`status`, `version`.

- Priority: **A** primary, **B** secondary, **C** development or recreational.
- At most **one ACTIVE goal with priority A** at a time; it anchors the training plan
  (interpretation of "primary goal", recorded as assumption A-19). B and C goals are unlimited.
- Statuses: DRAFT, ACTIVE, ACHIEVED, MISSED, ABANDONED, SUPERSEDED.

## 3. Baseline

At goal creation the system stores an immutable baseline snapshot: the evidence of current
level (recent results, relevant derived metrics with IDs, data quality). Later progress is
measured against it.

Precision (P12): the baseline is the current state of the feasibility pipeline
(section 4) at creation, with its evidence and a summary. Without comparable evidence it
says so and has no evidence.

## 4. Feasibility (FR-GOL-003)

Pipeline, each step deterministic and versioned:

1. **Current state:** best evidence of current performance for the goal sport and distance,
   with confidence (from recent results, assessments or derived metrics).
2. **Performance gap:** target minus current, in comparable units (for example seconds over
   the goal distance). If the current state cannot be expressed in comparable units, the gap
   is UNKNOWN.
3. **Available time:** weeks until target date, and available hours per week from
   availability.
4. **Required progression:** gap divided by available training weeks.
5. **Feasibility:** `LIKELY`, `POSSIBLE`, `UNLIKELY` or `INSUFFICIENT_DATA`.

The classification thresholds are an APPLICATION_DERIVED metric with a calculation version.
They are defined in Phase P12 and documented in the metric registry. Classification is
`INSUFFICIENT_DATA` whenever the current state confidence is LOW or the gap is UNKNOWN.

The AI coach may interpret and explain the result, but does not compute it (ADR-008).

Feasibility rules 1.0.0 (P12, precision; `coach.domain.goals.feasibility`,
`coach goal feasibility`; a read model registered as `goal.feasibility` in the metrics
catalogue; thresholds are initial values under A-18):

1. **Current state:** the declared running race result of the last 12 months nearest the
   goal distance (the most recent on a tie), carried to it with Riegel's equation,
   T2 = T1 x (D2 / D1) ^ 1.06 (Riegel PS. Am Sci. 1981;69(3):285-290, PMID 7235349). Its
   evidence is the profile field.
   - Confidence HIGH: the same distance (within 5 %) within 6 months.
   - MEDIUM: half to twice the distance within 6 months, or the same distance within 12
     months.
   - LOW otherwise: an extrapolation, the marathon above all, where the equation is known to
     be optimistic.
2. **Gap:** current minus target time; none when already faster.
3. **Available time:** whole days to the target date in weeks, and the maximum hours a week.
4. **Required progression:** the gap per week, in seconds and as a share of the current time.
5. **Classification:**
   - LIKELY at 0.25 % a week or less, POSSIBLE at 0.5 % or less, UNLIKELY above;
   - at most POSSIBLE with fewer than 2 hours a week (`FEW_HOURS`);
   - INSUFFICIENT_DATA with a LOW confidence or an UNKNOWN gap, with its reason:
     `SPORT_NOT_SUPPORTED` (rules 1.0.0 assess running only), `NO_DISTANCE`,
     `NO_TARGET_TIME` (a goal to finish), `NO_COMPARABLE_RESULT`, `LOW_CONFIDENCE`.
6. **Alternatives:** the realistic goal is the current time improved at the LIKELY rate over
   the weeks available, never faster than the target. The minimum satisfactory goal is the
   current time. Both are shown with the desired one whenever section 5 applies.

## 5. Presenting goals (FR-GOL-004, FR-GOL-005)

When feasibility is not `LIKELY`, or confidence is not HIGH, the coach presents:

- **Desired goal:** the athlete's target.
- **Realistic goal:** the outcome the evidence supports, with confidence.
- **Minimum satisfactory goal:** a conservative outcome worth achieving.

The system **never guarantees outcomes**. Forecasts state confidence and uncertainty reasons.
Wording such as "you will achieve" is prohibited; agent evaluations check it (AT-GOL-001).
