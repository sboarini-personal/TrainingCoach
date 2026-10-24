# ADR-017: Policy engine is the final gate for AI proposals

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-POL-001, FR-POL-002, SAFE-MED-003, PRIV-CON-002

## Context

Safety, consent, availability, equipment, capability and confirmation constraints must hold
regardless of what the LLM proposes or how it is prompted.

## Decision

A pure, deterministic policy engine (`coach.policy`) evaluates every proposal and action
with deny-overrides semantics before anything is persisted or executed. The AI submits
proposals as data and receives reason codes on denial. No tool bypasses it.

## Consequences

- Policies are unit-testable without an LLM.
- New constraints are added as policies, not as prompt instructions.

## Verification

- AT-POL-001, AT-SAFE-001, AT-CON-001.
