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
  the header revision they reviewed, which is required (`coach plan list` shows it); a
  stale revision fails with CONCURRENCY_CONFLICT and nothing is written. The policy engine
  decides before the transaction (an audited denial) and again inside it, so a safety hold
  arriving meanwhile fails the activation with CONCURRENCY_CONFLICT.
- **One ACTIVE version per athlete.** Activating a version supersedes the athlete's ACTIVE
  version, of the same plan or of another one (ACTIVATED_NEWER_VERSION; the other plan's
  header no longer has an ACTIVE version), so the plan in force is never ambiguous and its
  load is the whole planned load.
- **Proposals need the Definition of Ready.** A proposal names the athlete's own open goal
  (DRAFT or ACTIVE; another athlete's goal is not found). While readiness is NOT_READY no
  version is created (onboarding spec 2, AT-ONB-002); while it is PROVISIONAL only a
  provisional one is.
- **Rejecting.** Only a PROPOSED version is rejected or replaced; an ACTIVE version is
  superseded only by activating another one.
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
**intensive** when its highest prescribed RPE is 7 or more (strength: the harder of the
target RPE and 10 minus the target RIR, else the effort cue EASY 3, MODERATE 5, HARD 8), a
maximal evaluation, or an effort that is not stated; the policy engine uses this.

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

### 5.1 Generator rules 1.0.0 (P12, precision of sections 1 to 5)

`coach.planning.generator` (pure and deterministic: the same inputs give the same content,
workout identifiers included), run by `coach plan generate [--goal]`
(`PlanGenerationService`). The proposal goes through `PlanService`, so the goal, readiness
and every plan policy are checked again; the rules are written so that the policies allow
what they produce. Initial values under A-18.

- **Inputs** (from stored state): the athlete's local today; the goal (the primary one by
  default); readiness and its missing preferred inputs; the lifecycle state; availability,
  resources and the health declaration; strength experience; years of experience in the goal
  sport; its minutes in each of the last 8 complete weeks (Monday to Sunday, ACTIVE activities,
  none without the activities consent); SAFETY_HOLD and RECOVERY_BLOCK with its end.
- **Refused**: NOT_READY (no version, AT-ONB-002); a goal sport other than running, cycling or
  swimming; a goal date before the plan could start; an active constraint excluding the goal
  sport; swimming without pool access; cycling without a bicycle or smart trainer.
- **Kind and dates.** The plan starts on the next Monday (today when it is one). In
  BASELINE_COLLECTION it is a 2-week ADAPTATION block with one submaximal assessment a week
  (`eval.submaximal_<sport>`: 10 minutes easy then 20 minutes at RPE 4 to 5, recording heart
  rate; 30 minutes; never maximal, since no opt-in exists), provisional (FR-ONB-005,
  AT-ONB-004). Otherwise it runs to the week of the target date, at most 26 weeks (a longer
  horizon is stated as an assumption). The target date and the 6 days after carry nothing;
  the day before is a REST day; the goal's week has no long session.
- **Phases** over N weeks: TAPER the last 2 weeks when N is 8 or more, the last week when N
  is 4 to 7; ADAPTATION the first 2 weeks when the history is insufficient (no activity in 8
  weeks, or under a year of experience) and at least 6 weeks remain; the rest is BASE (half,
  rounded up), BUILD (three tenths) and SPECIFIC (the remainder, at least 1); 1 week is
  SPECIFIC, 2 are BUILD and SPECIFIC. A practical rule, not a published standard.
- **Volume** (the goal sport's weekly minutes): it starts at the mean of the last 4 complete
  weeks when any is above zero, else 90 minutes. The step is the athlete's largest
  week-to-week increase over the last 8 weeks (at least 5 and at most 30 minutes; 10 without
  one), halved (in multiples of 5, at least 5) for a provisional plan, under a year of
  experience or unknown, or an active health constraint for the sport. Loading weeks add the
  step up to the **peak**: the highest recent week plus three steps. Every 4th week is an
  unloading week back to the starting volume. With SAFETY_HOLD or RECOVERY_BLOCK the first 2
  weeks hold the volume. The taper is 65% then 45% of the last loading week (two weeks) or
  55% (one week), intensity and frequency kept: Bosquet L, Montpetit J, Arvisais D, Mujika I.
  Effects of tapering on performance: a meta-analysis. Med Sci Sports Exerc.
  2007;39(8):1358-1365 (PMID 17762369). Each week's primary goal states its minutes and the
  rationale of the change.
- **Sessions.** Endurance sessions: the week's minutes over 45, rounded, from 2 to 5. In order
  of priority, within the available days and the pattern's days a week (one session a day):
  the long session (an easy one in the goal's week), a strength session, the key session (none
  in ADAPTATION; the assessment in a baseline block), easy sessions, a second strength session
  (when two a week were declared, not while tapering). Days are the athlete's: a day is used
  when plannable, available, at least 20 minutes long and allowing the sport. The long session
  goes on the longest day (the latest on a tie), the key one far from it (and not the day
  after the athlete's tennis), strength not the day before the long or key session and
  preferably the day after the key one, easy sessions spread out. Each session fits its day;
  the week fits the pattern's hours (shorter sessions first, then fewer easy ones, then no key
  one).
- **Workouts**, intensity by RPE only (heart-rate, pace and power targets need reliable
  anchors these rules do not read; stated as an assumption, FR-PLN-005, AT-CYC-001): easy and
  long sessions at RPE 3 to 4; the key session is a steady one in BASE (RPE 5 to 6 between an
  easy warm-up and cool-down), intervals in BUILD and TAPER (4 minutes at RPE 7 to 8 with 2
  minutes easy, 3 to 6 times; 3 while tapering) and sustained work in SPECIFIC (8 minutes at
  RPE 6 to 7 with 2 minutes easy, 2 to 4 times). With a safety hold, before a recovery
  block's re-evaluation (any day when its end is unknown) or under a MAX_RPE below 7, the key
  session is the steady one; MAX_RPE caps every target.
- **Strength** (strength spec 1 and 4): 45 minutes (30 while tapering); the first session of
  the week works the lower body, a pull and the core, the second the upper body and the core.
  Each exercise slot takes the first catalogue exercise the athlete can do (equipment rules
  1.0.0; no HIGH_IMPACT or OVERHEAD exercise; without RPE or RIR familiarity, only exercises
  against the body's resistance or with a declared load). Target RPE 6 in ADAPTATION and
  TAPER, 7 otherwise, 6 at most during a recovery block, 5 for a lower-body exercise under
  NO_HEAVY_LOWER_BODY or one loading an affected region, capped by MAX_RPE; RIR is 10 minus
  that RPE for an athlete familiar with RIR only; otherwise EASY (below RPE 5) or MODERATE.
  Strength is not planned during a safety hold, under a constraint restricting strength, or
  under a MAX_RPE below 3, and the plan says so (AT-STR-002). A heavy lower-body session that
  the interference rules (strength spec 7) assess as HIGH or VERY_HIGH with another planned
  session is replaced by the upper-body session (AT-STR-001); each strength session states
  the worst level it is part of.
- **The athlete's tennis**: each declared slot becomes a tennis session (expected RPE 7 for a
  match or an unknown type, 5 for practice) unless an availability exception marks the date
  unavailable, a health constraint restricts tennis, or it is hard while intensity is paused
  (then the plan says so: if the athlete plays, the session is recorded and counts as load).
  Planned sessions do not use the tennis days.
- **Assumptions** state the baseline block, each missing preferred input and how it is
  handled, a horizon longer than 26 weeks, a health constraint with a free-text restriction,
  intensity by RPE only, and what was left out.
- **Versions.** A goal keeps one plan: a new generation is a new version whose parent is the
  ACTIVE version (else the latest), replacing a pending proposal.

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

Execution rules 1.0.0 (P12, precision; `coach.planning.execution`, `coach plan executions
[--week]`, `coach plan link ACTIVITY (WORKOUT|none)`):

- **Read model.** Executions are computed on read from the ACTIVE plan version, the
  athlete's ACTIVE activities (EFFECTIVE view, so corrections count) and the athlete's links;
  only the links are stored ([../contracts/execution-link.schema.json](../contracts/execution-link.schema.json),
  one per activity, a newer one replacing it, audited as `plan.execution_linked`). Athlete
  feedback is the activity's own: its session RPE is shown (`subjective.rpe`), and soreness and
  pain come from the activity and the daily check-in.
- **Links.** A link names one of the athlete's activities and a non-REST workout of the ACTIVE
  version in the same training week (Monday to Sunday), or none (the activity is UNPLANNED); a
  workout may take several linked activities. A link to a workout no longer in the ACTIVE
  version is ignored (the rules match the activity again).
- **Matching.** Each planned workout, in its day's order, takes the earliest activity left
  that day of a compatible sport (its sport; STRENGTH and TENNIS by type; a recovery session
  without a sport takes OTHER). A workout still without one takes the earliest activity left
  that day of another sport: REPLACED. REST workouts take nothing: an activity on a rest day is
  UNPLANNED (AT-UNP-001); every activity left is UNPLANNED.
- **Statuses.** COMPLETE when every measured ratio is within 80% to 120%, else PARTIAL; with
  nothing measured the done workout is COMPLETE (its compliance says nothing was measured).
  SKIPPED once the day after the training day has ended (a grace day for late uploads);
  before that the workout is PLANNED (a status this precision adds for workouts not yet due).
- **Compliance 1.0.0.** The duration ratio (elapsed time over planned time) and, when the
  workout states a distance, the distance ratio; an unknown value is no ratio, never zero.
  Intensity (time in the target range) and structure (planned steps completed) are not
  measured: rules 1.0.0 prescribe RPE, which has no time in range; they stay UNKNOWN.

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

Adaptation rules 1.0.0 (P12, precision; `coach.planning.adaptation`, `coach plan adapt
[--trigger]`, `coach plan decisions`; thresholds are initial values under A-18):

- **Inputs** (from stored state): the ACTIVE version; the executions and their activities of
  the last 14 days (section 6); the recovery assessment in force (today's, else yesterday's)
  and the prescriptions in force today; the check-ins of the last 7 days; availability; the
  coaching conditions.
- **Rules**, the first that applies decides:
  1. SAFETY_HOLD: MAINTAIN; nothing is adapted until the athlete releases the hold.
  2. A blocking prescription (REST, ACTIVE_RECOVERY) with planned workouts that conflict before
     its `reevaluateAfter`: REST (a REST prescription; those days become rest days) or REDUCE
     (their intensity is taken out; the athlete's hard tennis is taken out too).
  3. Pain of 4 or more of 10 reported in the last 3 days: REDUCE: intensity out of the next 3
     days, and strength light (RPE 5, at most 2 sets) where it loads a region the pain
     location names exactly (region rules 1.0.0), or everywhere when it names none. The
     explanation suggests a professional evaluation if the pain lasts more than 7 days or
     worsens (safety spec 5), never a diagnosis. Nothing to change: MAINTAIN.
  4. Unplanned load of the last 2 days (UNPLANNED executions; completed strength without its
     sets is taken as possibly heavy) assessed by the interference rules (strength spec 7)
     with the planned sessions of the next 3 days: HIGH or VERY_HIGH moves an endurance
     session with intensity to a free, available day of its week at least 2 days after that
     load (RESCHEDULE) or takes its intensity out (REPLACE), and makes heavy legs light.
     Assessed but not severe: MAINTAIN, with the load as evidence (AT-UNP-001).
  5. Two or more deviated sessions in 14 days (below 80% of the planned duration, or a
     session RPE 2 or more above the workout's highest target) with a recovery assessment of
     REDUCE or REST: REDUCE, the intensity of the next 7 days taken out.
  6. Four or more planned workouts of the last 14 days done, all COMPLETE, none deviated, a
     NORMAL recovery assessment and no pain in 7 days: PROGRESS.
  7. Otherwise MAINTAIN: one deviated session (AT-ADP-001), several without a recovery
     signal (watched), or no signal.
- **Outcome.** Every run records an AdaptationDecision
  ([../contracts/adaptation-decision.schema.json](../contracts/adaptation-decision.schema.json)):
  trigger, decision, reason codes, evidence, an interpretation whose numbers come from the
  evidence (durations, RPE, dates, ratios of the referenced activities, counts of the workouts
  named), uncertainty, confidence, the workouts changed, the plan and versions. MAINTAIN
  creates no version (FR-ADP-003). REST, REDUCE, RESCHEDULE and REPLACE propose a new version
  of the ACTIVE content (parent: the ACTIVE version; replacing a pending proposal; unchanged
  workouts keep their identifiers, so links and executions follow them; provisional when the
  readiness is PROVISIONAL); PROGRESS proposes a version regenerated from the updated history
  (section 5.1). The proposed version's generator names the decision (`decisionId`). Every
  proposal goes through the goal, readiness and plan-policy checks; the athlete activates or
  rejects it.
- **Lifecycle.** A run moves TRAINING to ANALYSIS and then to TRAINING (MAINTAIN) or ADAPTATION
  (a proposal); activating the adapted version or rejecting it returns to TRAINING. Each run
  is audited (`plan.adaptation_decided`, with the decision and reasons) and emitted
  (`plan.adaptation.decided`).
