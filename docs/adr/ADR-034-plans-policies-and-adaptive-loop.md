# ADR-034: Plans, plan policies, execution and the adaptive loop

- Status: Proposed (pending the athlete's review)
- Date: 2026-10-31
- Requirements: FR-ONB-003 to FR-ONB-006, FR-GOL-001 to FR-GOL-003, FR-POL-001, FR-POL-002,
  FR-PLN-001 to FR-PLN-008, FR-ADP-001 to FR-ADP-004, FR-STR-005, FR-INS-001, FR-RPT-001,
  NFR-EXP-001, SAFE-MED-003
- Related: ADR-008 (calculate in code), ADR-031 (interference), ADR-033 (recovery assessment
  and report data)

## Context

P12 turns the athlete's declarations, history and recovery into training plans and keeps
them adapted. The spec fixed the shape: immutable plan versions, a policy engine that the AI
cannot bypass, a Definition of Ready, an execution model and an adaptive loop with six
decisions. It left the numbers and several mechanisms open:

- how a plan version is identified and protected against change;
- how activation stays consistent when the athlete reviews one revision and another arrives;
- which inputs a plan policy reads, and what "unknown" means for each;
- how a deterministic generator sets volume, progression and the taper without athlete data
  in the repository;
- how an activity is matched to a planned workout, and when a workout counts as skipped;
- how the loop decides without overreacting to one poor session (AT-ADP-001) or hiding a
  needed reduction;
- how pain that is not a red flag restricts load without a diagnosis.

The AI coach (P13) must later explain these decisions only from numbers computed here.

## Decision

- **Plan versions.** A plan is a `TrainingPlan` header (version numbers and the ACTIVE
  version, optimistic concurrency) and immutable `PlanVersion`s. A version's content has a
  SHA-256 hash of its canonical JSON (sorted keys, no whitespace, whole numbers as integers),
  checked on every read. Activation names the revision the athlete reviewed (AT-PLN-002),
  re-checks the policies inside the transaction and advances the lifecycle in it. At most
  one version is ACTIVE per athlete; an ACTIVE version leaves that state only when another is
  activated. A proposal needs the athlete's own open goal and the Definition of Ready: none
  while NOT_READY, a provisional one while PROVISIONAL.
- **Plan policies (policy set 1.2.0).** They read a planning context assembled from stored
  state: availability and its weekly pattern, resources, the health declaration, the end of a
  recovery block, pain restrictions and the athlete's today. Reasons are codes with
  identifiers, never athlete text, and UNKNOWN never allows. SAFETY_HOLD denies any
  activation and every intensive workout. The athlete's own tennis (a declared slot) is load
  the plan counts but does not place. Equipment rules 1.0.0: a bench is needed in addition,
  the other listed items are alternatives. A strength exercise's effort is the harder of its
  RPE and 10 - RIR.
- **Generator rules 1.0.0** are a pure function of the goal, the athlete's weekly history,
  availability, resources, restrictions and readiness (planning spec 5.1):
  - Phases run to the goal: adaptation, base, build, specific and taper.
  - The starting volume is the mean of the last 4 complete weeks, or 90 minutes without
    history.
  - The step is the largest recent weekly increase, bounded to 5 to 30 minutes and halved
    when caution applies. The peak is the highest recent week plus three steps.
  - Every 4th week unloads. The taper keeps 65% then 45% of the last loading week (55% for a
    one-week taper), intensity and frequency kept, after Bosquet et al. 2007 (planning spec
    5.1). The phase split is a practical rule, not a published standard.
  - Intensity is prescribed by RPE only. Strength is planned every week unless
    contraindicated or impossible, and heavy legs in HIGH interference become upper body.
  - The baseline block plans submaximal assessments with a stated duration and RPE.
- **Execution rules 1.0.0** are a read model, not stored state:
  - A planned workout of the ACTIVE version matches an ACTIVE activity of its day and a
    compatible sport, or the athlete's `ExecutionLink`.
  - It is COMPLETE or PARTIAL by the 80% to 120% band of duration and stated distance, and
    REPLACED by another sport that day.
  - It is SKIPPED after a grace day and PLANNED before it. The rest of the activities are
    UNPLANNED.
  - Matching runs over whole weeks, so a link counts whatever period is read.
- **Adaptation rules 1.0.0** decide in a fixed order (planning spec 7):
  1. a safety hold waits (MAINTAIN);
  2. a blocking prescription gives REST or REDUCE;
  3. pain of 4 or more eases where it hurts;
  4. severe unplanned interference reschedules or replaces;
  5. two or more deviations with a recovery signal reduce;
  6. consistent completion with normal recovery progresses, only in adaptation, base or
     build weeks at least 6 weeks before the goal;
  7. otherwise MAINTAIN.

  A rule that finds something without changing it adds a note and lets the next rules
  decide. Every run is an `AdaptationDecision` with its evidence, an interpretation whose
  numbers come from that evidence, uncertainty and confidence. A decision other than MAINTAIN
  proposes a new version through `PlanService`, so the goal, readiness and every policy are
  checked again. A refused proposal is still recorded (PROPOSAL_REFUSED). Done workouts are
  never adapted.
- **Pain rules 1.0.0** (sport safety spec 5): pain of 4 or more in the last 3 days that names
  a body region exactly restricts hard strength loading that region until 3 days after the
  report (PAIN_REGION_RESTRICTED). The generator plans it light, and the loop uses the same
  thresholds. A suggestion to seek a professional evaluation is the only advice; there is
  never a diagnosis.
- **Plan read models elsewhere.**
  - Pattern rules 1.1.0 evaluate ADHERENCE and PLANNED_VS_ACTUAL from the executions, and
    PLATEAU from the goal sport's goal metric (running and swimming).
  - Report data 1.1.0 fills the plan sections (Planned vs Actual, Deviations, Plan Impact,
    Adherence, Progression toward the A goal, Changes for the following week, and a cautious
    forecast), every number with its evidence.
  - The strength sets CSV template 1.0.0 imports set entries all or nothing and exports the
    executed sets (files spec 8.4).

## Consequences

- Every threshold is an initial value under A-18: the volume start and step bounds, the
  unloading cadence, the taper fractions, the compliance band and grace day, the deviation
  and pain thresholds, the progression gate and the pattern thresholds. Real data may move
  them; a change is a new rules version.
- Plans are only as good as the athlete's declarations: without a recurring weekly pattern or
  a health declaration, the policies deny rather than assume.
- The adaptive loop runs on the athlete's command or on a trigger; it never activates a
  version. Activation stays a human confirmation.
- Plans are not published to Garmin (P14 needs a tier with `trainingPublish`).
- The P13 agent will receive plan versions, executions and adaptation decisions as data. It
  can explain or propose; the policies still decide.
