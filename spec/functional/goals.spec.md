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

## 5. Presenting goals (FR-GOL-004, FR-GOL-005)

When feasibility is not `LIKELY`, or confidence is not HIGH, the coach presents:

- **Desired goal:** the athlete's target.
- **Realistic goal:** the outcome the evidence supports, with confidence.
- **Minimum satisfactory goal:** a conservative outcome worth achieving.

The system **never guarantees outcomes**. Forecasts state confidence and uncertainty reasons.
Wording such as "you will achieve" is prohibited; agent evaluations check it (AT-GOL-001).
