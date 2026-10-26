# Acceptance: provider integration

Implementation status (P7): AT-GAR-001 is executed with the Garmin Personal capabilities, the
policy gate and a socket guard that fails on any connection attempt
(`tests/unit/test_workout_publisher.py`). AT-DEG-001 is executed with the fake provider for
unavailability, rate limits and an expired key (`tests/integration/test_degraded_operation.py`)
and end to end through the CLI, SQLite and a scripted HTTP transport
(`tests/integration/test_cli_garmin.py`); the retry policy is also unit-tested
(`tests/unit/test_provider_retry.py`, `tests/unit/test_sync_service.py`). The port contract
runs against the fake provider and the Garmin adapter
(`tests/contract/test_fitness_provider_contract.py`).

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
