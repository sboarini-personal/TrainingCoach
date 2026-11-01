# Agent tool contracts

Contract for the tools exposed to the AI coach. Requirements: FR-AGT-001, FR-AGT-002,
SEC-AUTHZ-002, SEC-INJ-002, FR-POL-002. Master spec §50, §65. Input and output JSON Schemas
are added per tool in the implementing phase (P13) and covered by contract tests.

## 1. Common rules

- **No `athleteId` parameter.** Every tool runs in the owner scope of the authenticated
  session.
- Inputs are validated against explicit schemas; unknown fields are rejected.
- Outputs are structured JSON. Every number carries a unit and an evidence reference; every
  external text field is wrapped as untrusted (SEC-INJ-001). Data quality and missing-data
  information are included.
- Each call is logged as an observability event with tool name, outcome and duration, never
  with secret values (`agent.tool.called`: `tool`, `outcome`, `duration_ms`).
- **Output shapes (tools 1.0.0, P13.2).** A call answers `{"ok": true, "data": {...}}` or
  `{"ok": false, "error": {"code": ..., ...details}}`; error details are codes and
  identifiers, never messages. A number is a quantity
  `{"value": n | null, "unit": "...", "evidence": [{"kind", "id", "field"?}]}` (a list of
  numbers is `"values"`); identifiers, positions and counts of listed items (version numbers,
  set numbers, counts) are plain integers. External text is `{"untrusted_text": "..."}`:
  control characters removed, at most 500 characters, longer text cut with ` [truncated]`.
  Text written in code (plan objectives, adaptation interpretations, insight observations,
  feasibility baselines) is sent as it is. Unknown stays `null`, and what is missing is said
  (`unknown`, `consentMissing`, `notAvailable`, `reason`).
- **Evidence of declarations.** A declared value is its own evidence: kinds `ATHLETE_PROFILE`
  (field named), `GOAL`, `SPORT_EVENT`, `AVAILABILITY` (P13.2).
- **Limits.** A period is at most 92 days (`from` after `to` and longer periods are
  `VALIDATION_ERROR` with `reason` `FROM_AFTER_TO` or `PERIOD_TOO_LONG`); a list at most 100
  items, with `count` and `truncated`. A tool that needs the athlete's today answers
  `VALIDATION_ERROR {reason: TIMEZONE_REQUIRED}` while the timezone is not set.
- **Consent.** The pipeline requires AI_PROCESSING before any call (agent spec 4). Tools that
  read activities (activities, details, issues, strength, executions, adaptation decisions,
  the activity sections of reports) also require ACTIVITIES (`CONSENT_REQUIRED`); daily
  recovery metrics and the wearable signals of an assessment are sent only for granted
  categories (RECOVERY, SLEEP, HRV, PROVIDER_RECOVERY_METRICS). Access without consent stays
  the athlete's right outside the AI (D-9); sending data to the AI is processing.
- **Minimisation.** The profile sends an age, never the birth date; activity details never
  send streams (GPS positions above all), only which streams exist; provider record IDs and
  file hashes are not sent.
- **Reads change nothing.** A READ tool never writes (the readiness read does not advance the
  onboarding lifecycle).
- Tool kinds:

  | Kind | Meaning | Confirmation |
  |---|---|---|
  | READ | reads owner-scoped data | none |
  | WRITE_INTERNAL | changes internal data through allow-listed fields | none (audited) |
  | PROPOSE | creates a proposal (plan version, workout proposal); no external effect | none |
  | EXECUTE_EXTERNAL | external write or destructive operation | human confirmation token; the agent cannot obtain it |

## 2. Typed errors

`NOT_FOUND`, `VALIDATION_ERROR`, `CONSENT_REQUIRED {consentType}`, `POLICY_DENIED {reasons[]}`,
`CONFIRMATION_REQUIRED {proposalId}`, `CAPABILITY_UNAVAILABLE {capability}`,
`CONCURRENCY_CONFLICT`, `PROVIDER_UNAVAILABLE {freshness}`, `RATE_LIMITED`.

## 3. Catalogue

| Tool | Kind | Input (summary) | Output (summary) | Policies |
|---|---|---|---|---|
| getAthleteProfile | READ | `sections[]?` (basics, physiology, devices, experience, resources, health) | profile with UNKNOWN markers and provenance; an age, not the birth date | |
| updateAthleteProfile | WRITE_INTERNAL | `expectedVersion` and allow-listed fields: `heightCm`, `restingHr`, `maxHr`, `metricUnits`, `language` | new profile version and the fields changed | mass-assignment allow-list (SEC-AUTHZ-003); `CONCURRENCY_CONFLICT {currentVersion}` |
| getGoals | READ | `status?` | goals (with baseline evidence) and events | |
| saveGoal | WRITE_INTERNAL | `sport`, `targetDate`, `priority`, `distanceMeters?`, `targetTimeSeconds?`, `eventId?` | a DRAFT goal and the command that activates it | the athlete activates (at most one ACTIVE A goal) |
| getGoalFeasibility | READ | `goalId?` (default: the primary goal) | feasibility now: classification, confidence, reasons, current level, alternatives | |
| getReadiness | READ | none | Definition of Ready, missing inputs, history sufficiency, lifecycle; changes nothing | |
| getAvailability | READ | `from?`, `to?` (default: today and 13 days) | effective availability per date and the weekly declaration | |
| getActivities | READ | `from?`, `to?` (default 14 days), `sport?`, `limit` (max 100) | activity summaries with quality | consent ACTIVITIES |
| getActivityDetail | READ | `activityId` | canonical activity (no raw streams), quality, corrected fields, source summary, metrics, open issues | ownership, consent ACTIVITIES |
| getRecoveryMetrics | READ | `from?`, `to?` (default 14 days), `metrics[]?` | daily recovery values with provider labels; missing consent categories | consent per category |
| getDailyCheckIn | READ | `from?`, `to?` (default 7 days) | check-ins | |
| getRecoveryStatus | READ | `day?` (default today) | assessment with signals, prescriptions in force, coaching conditions | wearable signals by consent |
| getInsights | READ | `from?`, `to?` (default 28 days) | detected patterns with evidence (associations only) | consent per pattern |
| recordSubjectiveFeedback | WRITE_INTERNAL | daily check-in (`trainingDay?`, the 1-5 scales, `pain` 0-10, `painLocation`, `painOnset`, `notes`) and/or `activityId` with `activityRpe` 0-10 | the check-in and red flags; the activity RPE as a correction | red-flag detection; values the athlete stated only |
| getStrengthHistory | READ | `from?`, `to?` (default 28 days), `exerciseId?` | set results, hard sets by muscle, volume load | consent ACTIVITIES |
| getTrainingPlan | READ | `planId?`, `number?` (default ACTIVE), `from?`, `to?`, `details?` | plan version and its workouts in the period | |
| getPlanExecutions | READ | `from?`, `to?` (default 14 days) | planned vs completed with compliance | consent ACTIVITIES |
| getAdaptationDecisions | READ | `limit?` | the adaptive loop's decisions with evidence | consent ACTIVITIES |
| getReportData | READ | `kind`, `activityId` / `week` / `month` | report data part (sections, items with evidence) | consent per section |
| proposeTrainingPlan | PROPOSE | `mode` GENERATE (`goalId?`) or ADAPT | new PROPOSED version (and how the athlete activates it), the adaptation decision, or `POLICY_DENIED` | full policy set; the content is written by the rules, never by the model |
| getDataQualityIssues | READ | `status?` (OPEN, ALL), `limit?` | issues | consent ACTIVITIES |
| requestProviderSync | WRITE_INTERNAL | none | outcome and counts of the activity and recovery syncs | capability, rate limit, consent per category; `PROVIDER_UNAVAILABLE {reason: NOT_CONNECTED}` |
| getProviderCapabilities | READ | none | capability snapshot with basis and freshness | |
| proposeGarminWorkout | PROPOSE | `plannedWorkoutId` (ACTIVE plan) | whether publication is possible (TRAINING_PUBLISH state); the workout stays local | capability |
| publishGarminWorkout | EXECUTE_EXTERNAL | `proposalId` | `CONFIRMATION_REQUIRED` or `CAPABILITY_UNAVAILABLE` (V1 always the latter) | capability, confirmation |
| requestDataDeletion | EXECUTE_EXTERNAL | `scope` (ALL, ACTIVITIES, RECOVERY, CHECKINS, PLANS, CONVERSATIONS) | always `CONFIRMATION_REQUIRED {scope, confirmedBy: ATHLETE_ONLY}`; the deletion workflow is P15 | destructive confirmation |
| flagSafetyConcern | WRITE_INTERNAL | `redFlags[]` (red flag codes) | SAFETY_HOLD active | safety spec 2, source 3 (fail-safe); only the athlete releases it |

Activation of a plan version is **not** an agent tool: it is a human action in the UI or CLI.

Precision (P13.4): the coach asks and the code decides. `proposeTrainingPlan` runs the
deterministic generator or the adaptive loop (planning spec), so a version's content is
never written by the model; a refused proposal is `POLICY_DENIED` with its reasons. Each
significant decision a tool takes part in (plan proposal, adaptation, load reduction,
safety response) is recorded by the pipeline as an `AgentDecision` with the turn's tool
calls, its evidence and the answer the athlete saw; if the model fails after the tool
acted, the decision is still recorded, with a fixed explanation. A red flag reported
through `recordSubjectiveFeedback` or `flagSafetyConcern` adds the fixed safety notice to
the reply. EXECUTE_EXTERNAL tools stop before any effect.

## 4. Prohibited tools

The agent never receives tools equivalent to: `executeSQL`, `arbitraryHTTP`, `callAnyURL`,
`executeShell`, `readSecret`, `readEntireDatabase`, `arbitraryCode`, or any tool taking a
file path, URL or athlete ID.
