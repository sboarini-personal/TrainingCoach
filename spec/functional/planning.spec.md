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

**Precisions (P12):**

- **Plan header.** Each plan has one header
  ([../contracts/plan-header.schema.json](../contracts/plan-header.schema.json)) holding the
  latest and the ACTIVE version numbers. Creating a version and activating one update the
  header with optimistic concurrency (its `version` is the storage revision) in the same
  transaction as the versions, so version numbers are never given twice and two activations
  made from the same reading cannot both succeed (AT-PLN-002). The athlete activates with
  the header revision they reviewed (`coach plan list` shows it); a stale revision fails with
  CONCURRENCY_CONFLICT and nothing is written.
- **Revision.** A version's `revision` (storage revision) changes only with its status; its
  content never changes.
- **Canonical hash.** The canonical JSON has sorted keys, no whitespace, UTF-8, and whole
  numbers written without a fraction (so 4 and 4.0 hash alike after a JSON round trip). The
  hash is recomputed whenever a version is read; a mismatch refuses the read (tamper or
  corruption detection).
- **Several proposals.** Several versions may be PROPOSED at once (AT-PLN-002). A proposal
  is SUPERSEDED with REPLACED_BY_NEWER_PROPOSAL only when a newer proposal names it as the
  one it replaces (the generator and the adaptive loop do this); otherwise it stays PROPOSED
  until the athlete activates or rejects it. A new version keeps the plan's goal.
- **Activation reason.** Optional free text, screened for disclosed secrets before it is
  stored (SEC-SEC-005).

## 3. Training week (FR-PLN-004)

`TrainingWeek`: `weekIndex`, `startDate` (Monday, athlete's local date), `phase`,
`primaryGoal`, `estimatedLoad` (internal load AU and per-sport external load), `days`
(monday to sunday, each a list of planned workouts), `keyWorkouts[]`, `strengthSessions[]`,
`tennisSessions[]`, `recoveryDays[]`, `monitoringMetrics[]` (metric IDs),
`adaptationRules[]`.

Precision (P12): `keyWorkouts`, `strengthSessions`, `tennisSessions` and `recoveryDays` are
derived from `days`, never set separately. A recovery day is a day whose planned workouts are
all REST or RECOVERY (a day with nothing planned is free, not a recovery day). A workout is
**intensive** when its highest prescribed RPE is 7 or more (strength: the target RPE, else
10 minus the target RIR, else the effort cue EASY 3, MODERATE 5, HARD 8); the policy engine
and the interference rules use this.

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
Precision (P12): the rule is one canonical text, "Stop at once on red-flag symptoms (chest
pain, fainting, unusual breathlessness) or unusual pain."; an endurance workout without it is
refused.

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
