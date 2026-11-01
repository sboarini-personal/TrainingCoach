# Policy engine

Owner of FR-POL-001 and FR-POL-002. Master spec §49 and §50. Core in Phase P4 (ownership,
consent), complete in Phase P12.

## 1. Interface (FR-POL-001)

```
evaluate(action: Action, context: PolicyContext) -> PolicyDecision

PolicyDecision = ALLOW
               | DENY(reasons: list[PolicyReason])
               | REQUIRE_CONFIRMATION(reason: PolicyReason, proposalId)
```

- Pure and deterministic: no I/O, no clock reads (time comes from the context), no LLM.
- The application layer assembles the context (profile, availability, consents, conditions,
  capabilities, ownership) and calls the engine before executing or persisting any action.
- Combination is **deny-overrides**: any DENY wins over REQUIRE_CONFIRMATION, which wins over
  ALLOW.
- Every decision records `policySetVersion` and the reasons (codes plus parameters), and
  security-relevant denials emit `security.authz.denied` (NFR-OBS-001).

## 2. Policy catalogue

| Policy | Denies or requires confirmation when |
|---|---|
| POL-OWNERSHIP | the resource does not belong to the acting athlete (SEC-AUTHZ-001) |
| POL-CONSENT | the action processes a data category without valid consent (PRIV-CON-002) |
| POL-SAFETY-HOLD | SAFETY_HOLD is active and the action recommends or activates intensive training (SAFE-MED-003) |
| POL-RECOVERY-BLOCK | RECOVERY_BLOCK is active and the proposal schedules intensity before `reevaluateAfter` |
| POL-AVAILABILITY | a workout falls on an unavailable day or outside time windows (FR-PRF-005) |
| POL-MAX-DURATION | a day's planned duration exceeds the effective maximum duration |
| POL-EQUIPMENT | a workout needs equipment or a resource the athlete lacks |
| POL-HEALTH-CONSTRAINT | a workout violates an active health constraint (FR-PRF-006) |
| POL-CAPABILITY | an action needs a provider capability that is not SUPPORTED (INT-GAR-003) |
| POL-EXTERNAL-WRITE | an external write lacks a valid human confirmation (ADR-005): REQUIRE_CONFIRMATION |
| POL-DESTRUCTIVE | a destructive operation (data deletion) lacks confirmation: REQUIRE_CONFIRMATION |

## 3. Core implemented in P4

`policySetVersion` 1.0.0. An `Action` states its kind (READ, WRITE_INTERNAL, PROPOSE,
EXECUTE_EXTERNAL, DELETE), the owner of the resource it touches, the consent types it needs,
whether it recommends or activates intensive training, and whether a verified human
confirmation is attached. The `PolicyContext` holds the acting athlete, the consent types
currently granted under the active privacy policy version, and the active coaching
conditions. Implemented policies and reason codes:

| Policy | Outcome | Reason code |
|---|---|---|
| POL-OWNERSHIP | DENY when the resource owner differs from the acting athlete | `OWNERSHIP_MISMATCH` |
| POL-CONSENT | DENY for each required consent type that is not granted | `CONSENT_REQUIRED` (parameter `consentType`) |
| POL-SAFETY-HOLD | DENY intensive training while SAFETY_HOLD is active | `SAFETY_HOLD_ACTIVE` |
| POL-EXTERNAL-WRITE | REQUIRE_CONFIRMATION for unconfirmed EXECUTE_EXTERNAL actions | `CONFIRMATION_REQUIRED` |
| POL-DESTRUCTIVE | REQUIRE_CONFIRMATION for unconfirmed DELETE actions | `CONFIRMATION_REQUIRED` |

The remaining policies need the planning model and arrive in P12 (section 5). Confirmation tokens arrive
with the first external write or deletion workflow; until then nothing sets `confirmed`.

Added in P7 (`policySetVersion` 1.1.0): an `Action` also states the provider capabilities it
needs, and the `PolicyContext` holds the capabilities that are SUPPORTED (from the stored
`IntegrationStatus`, never from the caller).

| Policy | Outcome | Reason code |
|---|---|---|
| POL-CAPABILITY | DENY for each needed capability that is not SUPPORTED (UNKNOWN included) | `CAPABILITY_UNAVAILABLE` (parameter `capability`) |

## 4. AI cannot bypass (FR-POL-002)

- AI proposals (plans, workouts, adaptations, tool calls) are data submitted to the engine.
  The AI has no tool that skips evaluation, and no tool that issues confirmations.
- A denied proposal is returned to the agent with reason codes so it can revise and explain;
  it is never persisted as PROPOSED.
- Confirmation tokens are issued only to a human UI or CLI action and are bound to one
  proposal, one athlete and an expiry (default 15 minutes). The agent never sees them.

AT-POL-001 verifies that a proposal violating availability, a health constraint or
SAFETY_HOLD is denied even when the agent insists.

## 5. Plan policies (P12, `policySetVersion` 1.2.0)

An `Action` may carry the plan content it proposes or activates (`plan`) and whether it
activates a plan (`activatesPlan`). For such an action the `PolicyContext` also holds the
**planning context**, assembled by the application from stored state, never from the caller:
the athlete's local today, the effective availability (profile spec 4), the declared
resources and health constraints, the regions recent pain restricts (pain rules 1.0.0),
and the end of the recovery block (the latest
`reevaluateAfter` of the blocking prescriptions in force today, recovery spec 4). A plan
proposal is evaluated before anything is written; a denied one is never persisted, its
denial is audited (`policy.evaluate`, DENIED, with the acting actor: SYSTEM, AGENT or USER)
and emitted (`security.authz.denied`). An activation is evaluated again against the current
state.

Only workouts from the athlete's today on are checked (the past of a plan is history), and
not the ACTIVE version's workouts already done (with activities, from today to the end of
the week, since a link may place an activity on a later day of its week; execution rules
1.0.0, planning spec 6): they are history too, not prescriptions (review of P12.8: squats
done before a pain report made the pain policy refuse the loop's own easing). Without the
activities consent nothing counts as done, so the check stays strict. REST needs no check. Reasons carry the workout's `day` and `workoutId` and, where relevant, an
`exerciseId`, a `constraintId` or limits; never athlete text. They come in a fixed order:
safety hold, recovery block, availability and duration, equipment, health.

| Policy | Outcome | Reason code (parameters) |
|---|---|---|
| POL-SAFETY-HOLD | DENY any plan activation while SAFETY_HOLD is active | `SAFETY_HOLD_ACTIVE` |
| POL-SAFETY-HOLD | DENY each intensive workout (planning spec 3) while SAFETY_HOLD is active | `SAFETY_HOLD_ACTIVE` (day, workoutId) |
| POL-RECOVERY-BLOCK | DENY each intensive workout before the block's re-evaluation date, or any intensive workout when that date is unknown, while RECOVERY_BLOCK is active | `RECOVERY_BLOCK_ACTIVE` (day, workoutId, reevaluateAfter) |
| POL-AVAILABILITY | DENY a workout on a day that is not plannable (no exception, pattern or default) | `DAY_NOT_PLANNABLE` |
| POL-AVAILABILITY | DENY a workout on an unavailable day (an exception or a preferred rest day) | `DAY_UNAVAILABLE` |
| POL-AVAILABILITY | DENY a workout whose sport the day does not allow | `SPORT_NOT_ALLOWED` (sport) |
| POL-AVAILABILITY | DENY more than one session on a day unless double sessions are allowed | `DOUBLE_SESSION_NOT_ALLOWED` (day, sessions) |
| POL-AVAILABILITY | DENY a week with more training days than the recurring pattern allows | `TOO_MANY_TRAINING_DAYS` (weekStart, trainingDays, maximumDays) |
| POL-AVAILABILITY | DENY a plan placing workouts when the recurring weekly pattern is unknown (its weekly limits cannot be checked) | `WEEKLY_PATTERN_MISSING` |
| POL-MAX-DURATION | DENY a day whose planned duration exceeds its maximum (the windows' total length when the day has windows) | `DAY_DURATION_EXCEEDED` (day, plannedMinutes, maximumMinutes) |
| POL-MAX-DURATION | DENY a week over the recurring maximum hours | `WEEK_DURATION_EXCEEDED` (weekStart, plannedHours, maximumHours) |
| POL-MAX-DURATION | DENY a workout whose duration is not stated (it cannot be checked) | `DURATION_UNKNOWN` |
| POL-EQUIPMENT | DENY a strength exercise the athlete's equipment does not allow (equipment rules 1.0.0, strength spec 2), or one not in the catalogue | `EQUIPMENT_MISSING`, `EXERCISE_UNKNOWN` (exerciseId) |
| POL-EQUIPMENT | DENY swimming without declared pool access, cycling without a bicycle or a smart trainer | `POOL_REQUIRED`, `BICYCLE_REQUIRED` |
| POL-HEALTH-CONSTRAINT | DENY any plan when the health declaration is missing (never assumed to be "none") | `HEALTH_NOT_DECLARED` |
| POL-HEALTH-CONSTRAINT | DENY hard strength work loading a region recent pain restricts (pain rules 1.0.0, safety spec 5) | `PAIN_REGION_RESTRICTED` (exerciseId, region) |
| POL-HEALTH-CONSTRAINT | DENY what an active constraint's restrictions name (below) | `SPORT_RESTRICTED`, `RPE_ABOVE_LIMIT`, `IMPACT_RESTRICTED`, `OVERHEAD_RESTRICTED`, `HEAVY_LOWER_BODY_RESTRICTED`, `REGION_RESTRICTED` (constraintId, exerciseId, maxRpe, region) |

Precisions:

- **Unknown never allows.** No availability makes every day not plannable; no resources
  allow only bodyweight exercises and no swimming or cycling; an unstated duration or effort
  is denied where it would have to be checked.
- **Activation and SAFETY_HOLD.** Planning spec 2 allows activation only without SAFETY_HOLD,
  so any activation is denied during a hold, whatever the plan's intensity (the stricter
  reading of AT-SAFE-001 and safety spec 3). A proposal without intensive workouts may be
  PROPOSED during a hold and activated after the athlete releases it.
- **Time windows.** Planned workouts have no clock time in V1, so a day with windows allows
  their total length.
- **Sports.** A day's allowed sports restrict workouts of a known sport; a recovery session
  without a sport (mobility) is not restricted by them.
- **The athlete's own tennis.** A tennis session on a weekday of the declared tennis schedule,
  no longer than the declared slot, is the athlete's own commitment; a slot covers one session
  (the shortest slot that fits; any other tennis is placed by the plan and checked like any
  workout). The plan counts the athlete's own tennis as load
  (estimated load, interference, adaptation) but does not place it, so availability and
  duration checks other than an availability exception for that date do not apply to it
  (it is neither a double session nor a training day of the pattern). Safety, recovery and
  health policies apply to it.
- **Health restrictions.** A constraint applies to the sports it lists (all sports when it
  lists none) on the days it is active; NO_SPORT applies to its sport. MAX_RPE(n): a workout
  whose highest target RPE exceeds n, or whose effort is not stated. NO_IMPACT: running and
  tennis, and strength exercises tagged HIGH_IMPACT. NO_OVERHEAD: swimming and tennis, and
  strength exercises tagged OVERHEAD. NO_HEAVY_LOWER_BODY: a lower-body-pattern exercise at a
  hard target (strength spec 8: RIR 4 or less or RPE 6 or more, either one, or the HARD
  cue). Affected regions: a strength exercise loading the region (region rules 1.0.0) at a
  hard target. The RPE of a strength exercise is the harder of its target RPE and 10 minus
  its target RIR (planning spec 3).
  OTHER is free text: code does not check it; the plan generator states it as an
  assumption for the athlete to review.
