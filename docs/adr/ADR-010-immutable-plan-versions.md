# ADR-010: Immutable versioned training plans

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-PLN-002, FR-PLN-003, FR-ADP-004

## Context

Adaptation changes plans frequently. Overwriting plans loses what the athlete was told and
why, and makes planned-vs-actual comparison unreliable.

## Decision

A plan version's content never changes (content hash recorded). Every change creates a new
version with `parentVersion`. Only status transitions occur on existing versions, with
optimistic concurrency; activation supersedes the previous ACTIVE version in one transaction.

## Consequences

- Workout executions reference the plan version that was active.
- Storage holds every version; this is intended.

## Verification

- AT-PLN-001, AT-PLN-002; contract test requires `contentHash`.
