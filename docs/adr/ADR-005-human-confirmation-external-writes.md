# ADR-005: Human confirmation for consequential external writes

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-POL-001, FR-POL-002, INT-GAR-002, PRIV-DEL-001

## Context

The AI coach can be wrong or manipulated. Actions with external or irreversible effects
(publishing workouts, deleting data, changing external settings, sharing data) must stay under
human control.

## Decision

Split PROPOSE from EXECUTE. Reads, internal analysis and creating PROPOSED plan versions need
no confirmation. EXECUTE operations require a confirmation token issued only by a human UI or
CLI action, bound to one proposal, one athlete and an expiry. The agent never receives these
tokens. Plan activation is a human action, not an agent tool.

## Consequences

- Some flows need an extra athlete step.
- External operations are idempotent per proposal.

## Verification

- Policy POL-EXTERNAL-WRITE and POL-DESTRUCTIVE unit tests.
- Tool contract: no agent tool issues confirmations (tools.spec.md).
- AT-GAR-001, AT-DEL-001.
