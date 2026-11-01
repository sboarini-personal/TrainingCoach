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

Readiness rules 1.0.0 (P12, precision; `coach.domain.onboarding.readiness`, `coach readiness`):

- **REQUIRED inputs** are judged with the onboarding planner's field states and freshness
  windows; a field recorded as UNKNOWN within its window counts. The fields are:
  - profile: display name, timezone, birth date or age;
  - primary goal: a priority-A goal in DRAFT or ACTIVE, with its target date;
  - current level: the goal sport's experience, or one of its activities in the last 8 weeks;
  - weekly availability: the recurring pattern;
  - health constraints: the declaration, re-confirmed every 30 days;
  - resources, strength experience, and tennis (its experience; 0 sessions a week is an
    explicit "none").
- **PREFERRED inputs** are counted from the data, never declared:
  - recent activity history: an ACTIVE activity in the last 8 weeks;
  - recovery history: a check-in or daily recovery data in the last 14 days;
  - Garmin integration: a stored Garmin key.
- **Lifecycle:** asking for readiness brings the lifecycle forward where the state machine
  allows it. An AI_PROCESSING decision (grant or decline, `coach consent decline`) moves
  NEW_USER to ONBOARDING. With every REQUIRED input present, the history sufficiency of
  section 3 chooses BASELINE_COLLECTION or GOAL_DEFINED, and BASELINE_COLLECTION moves to
  GOAL_DEFINED once the history is sufficient. Every transition is audited
  (`coaching.state_changed`) and emitted (`coaching.state.changed`).
- **Inputs:**
  - `coach profile name`, `coach profile health` (show, none, add, remove, confirm),
    `coach profile resources`, `coach profile experience <sport>` (`--unknown` answers "I do
    not know");
  - `coach availability` (show, set, default, exception, clear);
  - `coach event` (add, list);
  - `coach goal` (add, list, activate, close).
- **Asking for what is missing** (precision, athlete's review of 2026-10-31, decision D-12):
  - When readiness refuses a plan, `coach plan generate` asks in an interactive
    terminal for each missing REQUIRED input it can take as a short answer. Those are
    the profile's name, timezone and birth date; the goal sport's years; the days,
    hours and a usual day's minutes; "none" or a re-confirmation for health; gym and
    equipment; strength years and sessions; and tennis sessions.
  - Each answer is loaded with the very command the athlete would run, so it gets the
    same validation, secret screening and audit. The generation is then tried once
    more.
  - An answer the command refuses is not loaded, and its command is named. Inputs that
    need a whole command (a goal, a health constraint) are named with it.
  - Without an interactive terminal nothing is asked: the refusal lists each missing
    input with its command.

## 3. Baseline collection (FR-ONB-005)

History is **insufficient** for the goal sport when either holds (proposed thresholds,
assumption A-18):

- fewer than 6 activities of that sport in the last 28 days; or
- no reliable intensity anchor: no measured/declared max HR or zones, no race result within 12
  months, and (cycling) no FTP from a measured power source. Age-predicted zones (profile
  spec 1.1) are not an anchor.

Then the athlete enters BASELINE_COLLECTION. The system proposes **conservative assessment
sessions**:

- submaximal by default (for example an easy RPE-guided session with HR recording, or a
  steady session at RPE 4 to 5);
- maximal or threshold tests only when the athlete has relevant experience, no active health
  constraint for that sport, no SAFETY_HOLD, and explicitly opts in;
- no physiological threshold is inferred from age formulas or invented (FR-PRF-002).

Precision (P12): a reliable intensity anchor is any of:

- a known maximum heart rate that is not derived;
- a zone model that is not age-predicted;
- a running race result within 12 months;
- for cycling, an FTP measured within 6 months (it anchors cycling only).

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

DATA_INCOMPLETE (precision, review of 2026-10-31; `coach.application.athlete_inputs`):

- **Reasons.** It is active for either of two reasons:
  - `AVAILABILITY_UNDEFINED`: once availability is declared, a day of the coming 7 days that
    no exception, recurring availability or default makes plannable (profile spec 4);
  - `REQUIRED_INPUT_MISSING`: once onboarding is over, a REQUIRED input that is missing or
    stale (section 2). During onboarding a missing input is expected, not "becomes
    missing".
- **Evaluation.** The reasons are evaluated after every change of the athlete's inputs and
  whenever readiness is assessed. The condition clears with reason `INPUT_PROVIDED` when
  neither holds.
- **Effect.** A generated plan is provisional and says why (generator, planning spec 5.1),
  and `coach readiness` shows the reasons.
