# ADR-021: Coaching state: lifecycle state plus conditions

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-ONB-006, SAFE-MED-002, INT-GAR-008

## Context

The master specification lists a lifecycle (NEW_USER to ADAPTATION) and "additional states"
(SAFETY_HOLD, DATA_INCOMPLETE, INTEGRATION_ERROR, GOAL_REASSESSMENT, RECOVERY_BLOCK). These
additional states can be true at the same time as a lifecycle state and as each other (for
example TRAINING with INTEGRATION_ERROR and RECOVERY_BLOCK).

## Decision

Model exactly one **lifecycle state** with an explicit transition table, plus a set of
**conditions** that overlay it. Conditions have activation and clearing rules and feed the
policy engine. SAFETY_HOLD dominates: it can only be cleared by an explicit athlete action.

## Consequences

- Transitions and conditions are tested independently.
- The interpretation is documented in `spec/functional/onboarding.spec.md` section 4.

## Verification

- AT-STM-001, AT-SAFE-001.
