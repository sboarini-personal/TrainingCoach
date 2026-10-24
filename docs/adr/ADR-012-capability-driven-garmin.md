# ADR-012: Capability-driven Garmin adapter

- Status: Accepted
- Date: 2026-10-24
- Requirements: INT-GAR-003, INT-GAR-004, INT-GAR-005

## Context

Garmin access tiers expose different APIs and delivery modes (verified 2026-10-24: the
Personal tier is pull-only and has no Training or Courses API). Assuming features leads to
fabricated behaviour.

## Decision

Every Garmin feature is gated by a capability with a basis (CONFIGURED from documentation,
OBSERVED at runtime, or UNKNOWN). UNKNOWN never enables a write. Unsupported features degrade
gracefully (for example, workouts stay local).

## Consequences

- V1 ships without workout publication (`LocalWorkoutPublisher`).
- Moving to a business tier is a configuration and adapter change, not a domain change.

## Verification

- AT-GAR-001, AT-DEG-001.
