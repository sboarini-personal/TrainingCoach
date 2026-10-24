# Acceptance: planning, adaptation, policy, explainability

### AT-PLN-001 — Adaptation creates a new version
- Requirements: FR-PLN-002
- Level: unit and integration. First executable: P12.

**Given** plan version 12 is ACTIVE
**When** adaptation occurs
**Then** version 13 is created with status PROPOSED and parentVersion 12
**And** version 12's content and content hash are unchanged
**When** version 13 is activated
**Then** version 12 is SUPERSEDED and version 13 is ACTIVE.

### AT-PLN-002 — Exactly one active version
- Requirements: FR-PLN-003
- Level: integration. First executable: P12.

**Given** an ACTIVE version and two PROPOSED versions
**When** both proposals are activated concurrently
**Then** exactly one activation succeeds, the other fails with CONCURRENCY_CONFLICT
**And** at most one version is ACTIVE at any time.

### AT-ADP-001 — One poor workout does not rebuild the plan
- Requirements: FR-ADP-002, FR-ADP-003
- Level: unit. First executable: P12.

**Given** one workout completed well below target
**And** otherwise normal recovery and recent sessions
**When** the adaptive loop runs
**Then** the decision is MAINTAIN
**And** no new plan version is created.

### AT-EXP-001 — Load reductions are explained
- Requirements: FR-ADP-004, NFR-EXP-001, FR-INS-001, FR-AGT-004
- Level: unit and contract. First executable: P12.

**Given** the system reduces load
**Then** the decision includes evidence references, the reason, the interpretation, uncertainty reasons and confidence
**And** every number in the explanation traces to referenced evidence.

### AT-POL-001 — The policy engine cannot be bypassed
- Requirements: FR-POL-001, FR-POL-002, FR-PRF-006
- Level: unit. First executable: P12.

**Given** a proposal that schedules a workout on an unavailable day, or violates an active health constraint, or includes intensity during SAFETY_HOLD
**When** the agent submits it, including repeated insistence
**Then** the policy engine denies it with reason codes
**And** nothing is persisted as PROPOSED.
