# ADR-007: FitnessProvider abstraction

- Status: Accepted
- Date: 2026-10-24
- Requirements: INT-GAR-001, INT-GAR-002

## Context

Garmin access tiers differ and may change; other providers may be added later. Application
code must not depend on one provider's API.

## Decision

Define `FitnessProvider` and `WorkoutPublisher` as application ports
(`spec/integration/provider-capabilities.spec.md`). Adapters return RAW payloads with metadata;
normalisation is separate. Only Garmin is implemented in V1.

## Consequences

- Provider-specific behaviour stays in `coach.integrations.<provider>`.
- A fake provider is used for integration tests and degradation tests.

## Verification

- Contract tests run the same suite against the fake provider and the Garmin adapter (with
  recorded, redacted fixtures).
- AT-DEG-001.
