# Architecture Decision Records

Format: Context, Decision, Consequences, Verification. ADRs are immutable once Accepted; a
change is a new ADR that supersedes the old one (status `Superseded by ADR-NNN`).

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-llm-no-credential-access.md) | The LLM cannot access provider credentials | Accepted |
| [ADR-002](ADR-002-provider-independent-activity.md) | Canonical Activity model is provider-independent | Accepted |
| [ADR-003](ADR-003-strength-first-class.md) | Strength training is first-class | Accepted |
| [ADR-004](ADR-004-deduplication-enrichment.md) | Activity deduplication and enrichment model | Accepted |
| [ADR-005](ADR-005-human-confirmation-external-writes.md) | Human confirmation for consequential external writes | Accepted |
| [ADR-006](ADR-006-external-data-not-instructions.md) | External data cannot modify agent instructions | Accepted |
| [ADR-007](ADR-007-fitness-provider-abstraction.md) | FitnessProvider abstraction | Accepted |
| [ADR-008](ADR-008-deterministic-calculations.md) | Deterministic calculations outside the LLM | Accepted |
| [ADR-009](ADR-009-raw-normalized-derived.md) | RAW / NORMALIZED / DERIVED data architecture | Accepted |
| [ADR-010](ADR-010-immutable-plan-versions.md) | Immutable versioned training plans | Accepted |
| [ADR-011](ADR-011-field-provenance-overrides.md) | Field-level provenance and manual overrides | Accepted |
| [ADR-012](ADR-012-capability-driven-garmin.md) | Capability-driven Garmin adapter | Accepted |
| [ADR-013](ADR-013-python-stack.md) | Python stack | Accepted |
| [ADR-014](ADR-014-local-first-deployment.md) | Local-first deployment; data and secrets outside synced folders | Accepted |
| [ADR-015](ADR-015-garmin-personal-token-broker.md) | Garmin Personal tier: own token broker, in-memory tokens | Accepted |
| [ADR-016](ADR-016-owner-scoped-repositories.md) | Owner-scoped repositories | Accepted |
| [ADR-017](ADR-017-policy-engine-final-gate.md) | Policy engine is the final gate for AI proposals | Accepted |
| [ADR-018](ADR-018-llm-provider-abstraction.md) | LLM provider abstraction and AI-processing consent | Accepted |
| [ADR-019](ADR-019-parser-sandbox.md) | Sandboxed file parsers | Accepted |
| [ADR-020](ADR-020-toolchain-quality-gates.md) | Toolchain, quality gates and GitHub CI | Accepted |
| [ADR-021](ADR-021-coaching-state-conditions.md) | Coaching state: lifecycle state plus conditions | Accepted |
| [ADR-022](ADR-022-sqlite-document-persistence.md) | SQLite document persistence with the standard library (amends ADR-013) | Accepted |
| [ADR-023](ADR-023-audit-chain-pseudonymous.md) | Hash-chained audit log with pseudonymous identifiers | Accepted |
| [ADR-024](ADR-024-source-observations-resolution.md) | Source observations and pure field-level resolution | Accepted |
| [ADR-025](ADR-025-file-ingestion-mechanism.md) | File ingestion mechanism: sandbox process, FIT SDK, archives and streams | Accepted |
| [ADR-026](ADR-026-garmin-sync-mechanism.md) | Garmin sync mechanism: thin transport, application orchestration, shared pipeline | Accepted |
| [ADR-027](ADR-027-metrics-engine-and-units-of-work.md) | Metrics engine: subject dimensions, totals materialised on read, SQLite units of work | Accepted |
