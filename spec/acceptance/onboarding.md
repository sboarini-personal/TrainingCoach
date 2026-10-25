# Acceptance: onboarding, profile, goals, coaching state

All tests are PROPOSED until implemented. "First executable" is the delivery phase in which the
test can run.

Implementation status (P2): AT-STM-001, AT-AVL-001 and AT-CONC-001 are executed by
`tests/unit/test_coaching_state.py`, `tests/unit/test_availability.py` and
`tests/contract/test_repository_contract.py` (in-memory and, since P3, SQLite adapters). AT-ONB-001 is executed for the deterministic question planner
(`tests/unit/test_onboarding_planner.py`); its dialogue part waits for P13.

### AT-ONB-001 — Progressive onboarding without repeated questions
- Requirements: FR-ONB-001, FR-ONB-002
- Level: unit (question planner) and agent evaluation. First executable: P2 (planner), P13 (dialogue).

**Given** a new athlete who granted AI_PROCESSING consent
**When** onboarding starts
**Then** questions arrive in groups of at most 5 related items, in the order of the onboarding spec
**And** a field that is known and within its freshness window is never asked again
**And** an "I don't know" answer is stored as UNKNOWN and not re-asked within its freshness window.

### AT-ONB-002 — No full plan before Definition of Ready
- Requirements: FR-ONB-003
- Level: unit. First executable: P12.

**Given** an athlete without declared weekly availability
**When** a plan is requested
**Then** readiness is NOT_READY listing "weekly availability"
**And** no plan version is created.

### AT-ONB-003 — Provisional plan when preferred inputs are missing
- Requirements: FR-ONB-004
- Level: unit and contract. First executable: P12.

**Given** all REQUIRED inputs and no recent activity history
**When** a plan is generated
**Then** the plan version has `provisional = true`
**And** `assumptions` lists the missing history and how it was handled.

### AT-ONB-004 — Baseline collection with conservative assessments
- Requirements: FR-ONB-005
- Level: unit. First executable: P12.

**Given** fewer than 6 running activities in 28 days and no reliable intensity anchor
**When** onboarding completes
**Then** the lifecycle state is BASELINE_COLLECTION
**And** the proposed evaluation sessions are submaximal unless the athlete opted in
**And** no heart-rate or pace threshold is invented.

### AT-STM-001 — Coaching state transitions
- Requirements: FR-ONB-006
- Level: unit. First executable: P2.

**Given** each lifecycle state
**When** each trigger of the onboarding spec is applied
**Then** only the listed transitions succeed and every other transition fails with an error
**And** conditions (SAFETY_HOLD, DATA_INCOMPLETE, INTEGRATION_ERROR, GOAL_REASSESSMENT, RECOVERY_BLOCK) can be active together with any lifecycle state
**And** every change is audited.

### AT-AVL-001 — Availability precedence
- Requirements: FR-PRF-005
- Level: unit. First executable: P2.

**Given** recurring availability of 90 minutes on Thursdays and an exception for one Thursday with 30 minutes and only swimming allowed
**When** effective availability is resolved for that Thursday and the following one
**Then** the first resolves to 30 minutes, swimming only, and the second to 90 minutes
**And** a weekday without recurring or default availability is not plannable and raises DATA_INCOMPLETE.

### AT-CONC-001 — Concurrent profile update is rejected
- Requirements: NFR-REL-003
- Level: integration. First executable: P2.

**Given** two clients that read profile version 3
**When** both submit an update stating version 3
**Then** the first succeeds with version 4
**And** the second fails with CONCURRENCY_CONFLICT and changes nothing.

### AT-GOL-001 — Feasibility and goal presentation
- Requirements: FR-GOL-003, FR-GOL-004, FR-GOL-005
- Level: unit and agent evaluation. First executable: P12 (engine), P13 (presentation).

**Given** a 10K goal 8 weeks away with a target 6 minutes faster than the recent result
**When** feasibility is evaluated
**Then** current state, gap, available time, required progression and a classification with confidence are produced in code
**And** when the classification is not LIKELY the coach presents desired, realistic and minimum goals
**And** no message guarantees the outcome.
