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
  with secret values.
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
| getAthleteProfile | READ | `fields[]?` | profile with UNKNOWN markers and provenance | consent per category |
| updateAthleteProfile | WRITE_INTERNAL | allow-listed field changes, expected `version` | updated profile | mass-assignment allow-list (SEC-AUTHZ-003) |
| getGoals | READ | `status?` | goals and events | |
| saveGoal | WRITE_INTERNAL | goal fields, expected `version?` | goal | at most one ACTIVE A goal |
| getAvailability | READ | `dateRange` | effective availability per date | |
| getActivities | READ | `dateRange`, `sport?`, `limit` (max 100) | activity summaries with quality | consent ACTIVITIES |
| getActivityDetail | READ | `activityId` | canonical activity (no raw streams), quality, provenance summary | ownership, consent |
| getRecoveryMetrics | READ | `dateRange`, `categories[]` | daily recovery values with provider labels | consent per category |
| getDailyCheckIn | READ | `dateRange` | check-ins | |
| recordSubjectiveFeedback | WRITE_INTERNAL | `activityId?` or `trainingDay`, RPE, soreness, pain, notes | stored feedback; may trigger safety rules | red-flag detection |
| getStrengthHistory | READ | `dateRange`, `exerciseId?` | set results, volume by muscle | |
| getTrainingPlan | READ | `version?` (default ACTIVE) | plan version | |
| proposeTrainingPlan | PROPOSE | plan content or adaptation request, rationale, evidence refs | new PROPOSED version or `POLICY_DENIED` | full policy set |
| getDataQualityIssues | READ | `status?` | issues | |
| requestProviderSync | WRITE_INTERNAL | `window?` | sync job ID and status | capability, rate limit |
| getProviderCapabilities | READ | none | capability snapshot with basis and freshness | |
| proposeGarminWorkout | PROPOSE | `plannedWorkoutId` | local workout proposal; states whether publication is possible | capability |
| publishGarminWorkout | EXECUTE_EXTERNAL | `proposalId` | `CONFIRMATION_REQUIRED` or `CAPABILITY_UNAVAILABLE` (V1 always the latter) | capability, confirmation |
| requestDataDeletion | EXECUTE_EXTERNAL | `scope` | `CONFIRMATION_REQUIRED` with a summary of what will be deleted | destructive confirmation |

Activation of a plan version is **not** an agent tool: it is a human action in the UI or CLI.

## 4. Prohibited tools

The agent never receives tools equivalent to: `executeSQL`, `arbitraryHTTP`, `callAnyURL`,
`executeShell`, `readSecret`, `readEntireDatabase`, `arbitraryCode`, or any tool taking a
file path, URL or athlete ID.
