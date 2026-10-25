# Onboarding and coaching state

Owner of FR-ONB-001 to FR-ONB-006. Master spec §4, §9, §66.

## 1. Progressive onboarding (FR-ONB-001, FR-ONB-002)

### 1.1 Step 0: consent before any AI conversation

The first step is **deterministic** (no LLM). It explains data use and records consent
decisions (PRIV-CON-001), including `AI_PROCESSING` (PRIV-CON-003). Without `AI_PROCESSING`
consent, onboarding continues through a deterministic form-based flow (CLI or UI) and the AI
coach is unavailable. This step is needed because onboarding answers are personal data and
would otherwise reach the LLM provider before consent exists.

### 1.2 Question groups

Questions are asked in small groups of related items, at most **5 questions per turn**
(configurable). Order, safety-relevant first:

| # | Group | Content | Feeds |
|---|---|---|---|
| G0 | Consent (deterministic, step 0) | consent decisions, including AI_PROCESSING | PRIV-CON-001 |
| G1 | Identity, preferences, body | display name, birth date or age, timezone, units, language, height, weight | FR-PRF-001, FR-PRF-007 |
| G2 | Health and safety screen | declared health constraints, current symptoms (red-flag screen) | FR-PRF-006, SAFE-MED-002 |
| G3 | Primary goal | sport, distance/event, target date, target time, priority | FR-GOL-001, FR-GOL-002 |
| G4 | Current level | experience and recent results for the goal sport first, then other sports; resting and maximum heart rate | FR-PRF-003 |
| G5 | Availability and resources | days, hours, rest days, windows, pool, bike, trainer, gym | FR-PRF-004 |
| G6 | Strength experience | years, sessions/week, familiar exercises, RPE/RIR familiarity | FR-PRF-003, FR-STR-001 |
| G7 | Tennis | sessions/week, duration, typical RPE, match/practice | FR-TEN-002 |
| G8 | Devices and integrations | Garmin model, sensors, Garmin connection (optional) | FR-PRF-001, INT-GAR-001 |

A red flag declared in G2 activates SAFETY_HOLD immediately (SAFE-MED-002); onboarding may
continue but no intensive plan is produced.

### 1.3 Question planner (deterministic)

The next group is chosen in code from profile completeness, not by the LLM. The planner:

1. **First pass:** walks the groups in order (G0 to G8) and selects the first group with a
   pending **REQUIRED** input (section 2). The batch lists that group's pending REQUIRED
   fields first, then its pending PREFERRED fields, up to the batch limit. Related
   questions therefore stay together.
2. **Second pass:** when no REQUIRED input is pending, selects the first group with a pending
   **PREFERRED** input.
3. A field is **pending** when it was never recorded, or its recording is older than its
   freshness window. A field recorded within its window is **satisfied**, whether its value is
   known or was answered as UNKNOWN; validation happens when the value is recorded.
4. Returns the group ID and the field list, or nothing when onboarding data is complete. The
   AI coach phrases the questions; it never chooses which data to request beyond the
   planner's list. G0 is presented by the deterministic consent step, never by the LLM.

Freshness windows (proposed defaults, configurable, assumption A-18):

| Field | Window |
|---|---|
| weightKg | 90 days |
| restingHR, maxHR | 180 days |
| recent race results | 12 months |
| FTP | 6 months |
| availability, resources, equipment | until changed |
| health constraints | until `validUntil`, or re-confirmed every 30 days |

Answers of "I don't know" are stored as UNKNOWN with `askedAt`, and are not asked again
within the same freshness window (FR-ONB-002).

## 2. Definition of Ready (FR-ONB-003, FR-ONB-004)

| Input | Class | Source fields |
|---|---|---|
| AthleteProfile (identity, timezone, birth date or age) | REQUIRED | profile |
| Primary goal with target date | REQUIRED | goal (priority A) |
| Approximate current level for the goal sport | REQUIRED | sport experience or recent activities |
| Weekly availability | REQUIRED | availability |
| Declared health constraints (an explicit "none" counts) | REQUIRED | health constraints |
| Equipment and resources | REQUIRED | resources |
| Strength experience | REQUIRED | strength experience |
| Tennis schedule (an explicit "none" counts) | REQUIRED | tennis experience/schedule |
| Recent activity history | PREFERRED | activities, last 8 weeks |
| Recovery history | PREFERRED | daily recovery or check-ins, last 14 days |
| Garmin integration | PREFERRED | provider connection |

Readiness result (pure function, versioned):

- `NOT_READY`: any REQUIRED input missing. Lists the missing inputs. No plan is created.
- `PROVISIONAL`: all REQUIRED present, some PREFERRED missing. Only a plan with
  `provisional = true` and an explicit `assumptions[]` list may be created.
- `READY`: all REQUIRED and PREFERRED present.

## 3. Baseline collection (FR-ONB-005)

History is **insufficient** for the goal sport when either holds (proposed thresholds,
assumption A-18):

- fewer than 6 activities of that sport in the last 28 days; or
- no reliable intensity anchor: no measured/declared max HR or zones, no race result within 12
  months, and (cycling) no FTP from a measured power source.

Then the athlete enters BASELINE_COLLECTION. The system proposes **conservative assessment
sessions**:

- submaximal by default (for example an easy RPE-guided session with HR recording, or a
  steady session at RPE 4 to 5);
- maximal or threshold tests only when the athlete has relevant experience, no active health
  constraint for that sport, no SAFETY_HOLD, and explicitly opts in;
- no physiological threshold is inferred from age formulas or invented (FR-PRF-002).

## 4. Coaching state machine (FR-ONB-006)

### 4.1 Lifecycle states (exactly one active)

`NEW_USER`, `ONBOARDING`, `BASELINE_COLLECTION`, `GOAL_DEFINED`, `PLAN_READY`, `TRAINING`,
`ANALYSIS`, `ADAPTATION`.

| From | To | Trigger |
|---|---|---|
| NEW_USER | ONBOARDING | Step 0 consent decisions recorded |
| ONBOARDING | BASELINE_COLLECTION | REQUIRED inputs present, history insufficient |
| ONBOARDING | GOAL_DEFINED | REQUIRED inputs present, history sufficient |
| BASELINE_COLLECTION | GOAL_DEFINED | baseline criteria met |
| GOAL_DEFINED | PLAN_READY | a plan version is PROPOSED |
| PLAN_READY | TRAINING | athlete activates the plan |
| TRAINING | ANALYSIS | relevant completed activity ingested, or scheduled weekly review |
| ANALYSIS | TRAINING | decision MAINTAIN |
| ANALYSIS | ADAPTATION | decision other than MAINTAIN |
| ADAPTATION | TRAINING | new version activated, or proposal rejected |
| TRAINING | GOAL_DEFINED | goal achieved, missed or replaced |

Any other transition is invalid and fails with an error.

### 4.2 Conditions (zero or more active, overlaying the lifecycle state)

The master spec lists SAFETY_HOLD, DATA_INCOMPLETE, INTEGRATION_ERROR, GOAL_REASSESSMENT and
RECOVERY_BLOCK as "additional states". They can coexist with a lifecycle state (for example
TRAINING while Garmin is down), so they are modelled as **conditions** (interpretation,
recorded in ADR-021).

| Condition | Activated by | Effect | Cleared by |
|---|---|---|---|
| SAFETY_HOLD | red flag (SAFE-MED-002) | policy denies intensive recommendations and plan activation | explicit athlete action only (SAFE-MED-005) |
| DATA_INCOMPLETE | a REQUIRED input becomes missing or stale | no new full plan; provisional only | input provided |
| INTEGRATION_ERROR | provider failure (INT-GAR-008) | freshness warning; file ingestion continues | successful sync |
| GOAL_REASSESSMENT | feasibility drops to UNLIKELY, target date unreachable, or athlete request | goal review proposed before new plan versions | goal confirmed or changed |
| RECOVERY_BLOCK | active REST or ACTIVE_RECOVERY prescription | planned intensity suspended until `reevaluateAfter` | re-evaluation |

Every transition and condition change is audited (SEC-AUD-001).
