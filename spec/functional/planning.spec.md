# Training plans, execution and adaptation

Owner of FR-PLN-001 to FR-PLN-008 and FR-ADP-001 to FR-ADP-004. Master spec §42 to §48.
Implemented in Phase P12.

## 1. Phases and content (FR-PLN-001, FR-PLN-008)

Phases: ADAPTATION, BASE, BUILD, SPECIFIC, TAPER, COMPETITION, RECOVERY, TRANSITION.

A plan may combine endurance sessions, intervals, technique work, strength, tennis,
recovery, rest and evaluation sessions. Strength is included per FR-STR-001.

## 2. Immutable versions (FR-PLN-002, FR-PLN-003)

Contract: [../contracts/training-plan.schema.json](../contracts/training-plan.schema.json).

- A plan version's **content** (phases, weeks, workouts, assumptions) never changes after it
  is created. Its `contentHash` is the SHA-256 of the canonical JSON content.
- Any change, including adaptation and rescheduling one workout, creates a new version with
  `parentVersion` set.
- Status transitions:

  | From | To | When |
  |---|---|---|
  | DRAFT | PROPOSED | engine output passed the policy engine |
  | PROPOSED | ACTIVE | athlete activates it (no SAFETY_HOLD) |
  | ACTIVE | SUPERSEDED | another version of the plan is activated |
  | PROPOSED | SUPERSEDED | rejected, or replaced by a newer proposal (`supersededReason`) |
  | ACTIVE | COMPLETED | plan end date passed or goal event done |

- At most **one ACTIVE version** per plan. Activation sets the new version ACTIVE and the
  previous ACTIVE version SUPERSEDED in **one transaction** with optimistic concurrency
  (AT-PLN-002).
- Creating a PROPOSED version needs no confirmation (internal action). Activation is an
  explicit athlete action. Both are audited.

## 3. Training week (FR-PLN-004)

`TrainingWeek`: `weekIndex`, `startDate` (Monday, athlete's local date), `phase`,
`primaryGoal`, `estimatedLoad` (internal load AU and per-sport external load), `days`
(monday to sunday, each a list of planned workouts), `keyWorkouts[]`, `strengthSessions[]`,
`tennisSessions[]`, `recoveryDays[]`, `monitoringMetrics[]` (metric IDs),
`adaptationRules[]`.

## 4. Endurance workouts (FR-PLN-005)

Contract in [../contracts/workout.schema.json](../contracts/workout.schema.json): sport,
objective, duration, distance, warm-up, main set, cool-down, intensity per step (pace,
heart rate, power, RPE), technique focus, stop conditions, evaluation metrics.

Intensity dimensions are used only when supported by reliable athlete data:

| Dimension | Allowed when |
|---|---|
| RPE | always (every step carries an RPE target) |
| Heart rate | a valid zone model from measured or declared anchors exists (an AGE_PREDICTED model does not count, profile spec 1.1) |
| Pace | reliable pace data for the sport and subtype exists (not pool/open-water mixed) |
| Power | MEASURED power capability for the sport (FR-CYC-002) |

Stop conditions always include the safety rule: stop on red-flag symptoms or unusual pain.

## 5. Progression (FR-PLN-006)

There is no universal percentage rule for load increase. Progression uses athlete context:
training history and tolerance of past load increases, recent load, recovery state, phase,
goal priority, execution history and interference. Each week's load change carries its
rationale.

## 6. Workout execution (FR-PLN-007)

`WorkoutExecution`: `plannedWorkoutId` (null for UNPLANNED), `completedActivityIds[]`,
`executionStatus`, `compliance`, `athleteFeedback` (RPE, soreness, pain, feeling, notes).

Matching (proposed, versioned): an activity matches a planned workout on the same training
day with a compatible sport. The athlete can relink manually.

Compliance per dimension: duration ratio, distance ratio, intensity (share of time in the
target range), structure (share of planned steps or intervals completed). Each dimension
not prescribed is UNKNOWN.

| Status | Rule (initial, versioned) |
|---|---|
| COMPLETE | every prescribed dimension within 80% to 120% |
| PARTIAL | at least one prescribed dimension outside the band, workout started |
| SKIPPED | no matching activity by the end of the training day plus a grace period |
| REPLACED | a different sport or workout type was done instead on that day |
| UNPLANNED | an activity without a planned workout (FR-ACT-011) |

## 7. Adaptive loop (FR-ADP-001 to FR-ADP-004)

Triggered after relevant completed workouts, a new recovery assessment, or the weekly
review:

```
PLANNED -> ACTUAL -> DIFFERENCE -> RECOVERY -> INTERPRETATION -> DECISION
```

Decisions (FR-ADP-002): MAINTAIN, PROGRESS, REDUCE, RESCHEDULE, REPLACE, REST.

Evidence rules (initial, versioned):

- One isolated poor workout with otherwise normal recovery: **MAINTAIN**. The plan is not
  reconstructed (FR-ADP-003, AT-ADP-001).
- REDUCE, REST or REPLACE need multiple coherent signals: for example a recovery assessment
  below baseline plus a performance or RPE deviation in two or more sessions, or a pain
  report, or unplanned load creating HIGH interference.
- PROGRESS needs consistent completion at or below target effort and normal recovery.
- Any decision other than MAINTAIN creates a new PROPOSED version (FR-PLN-002), evaluated by
  the policy engine.

Explanation (FR-ADP-004, NFR-EXP-001): every decision other than MAINTAIN, and every load
reduction, records evidence references, interpretation, decision, uncertainty reasons and
confidence (AT-EXP-001), as an AdaptationDecision linked to its insights.
