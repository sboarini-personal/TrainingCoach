# Acceptance: provider integration

### AT-GAR-001 — No publication without the capability
- Requirements: INT-GAR-002, INT-GAR-003, INT-GAR-004, INT-GAR-005
- Level: contract and unit. First executable: P7.

**Given** Garmin capabilities without `trainingPublish` (Personal tier)
**When** a workout is proposed and publication is requested
**Then** a local structured workout is generated
**And** publication returns CAPABILITY_UNAVAILABLE
**And** no network request is made to any provider.

### AT-DEG-001 — Degraded operation without Garmin
- Requirements: INT-GAR-001, INT-GAR-008, INT-GAR-010, NFR-REL-002
- Level: integration (fake provider). First executable: P7.

**Given** the provider returns errors or rate limits, or the key is expired
**When** a sync runs
**Then** retries follow the retry policy and honour Retry-After
**And** INTEGRATION_ERROR is set with a reason, the sync is marked pending and freshness is reported
**And** local history and file ingestion keep working
**And** no missing data is fabricated.
