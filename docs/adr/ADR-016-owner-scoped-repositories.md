# ADR-016: Owner-scoped repositories

- Status: Accepted
- Date: 2026-10-24
- Requirements: SEC-AUTHZ-001, SEC-IDOR-001, SEC-IDOR-002

## Context

IDOR bugs appear when one endpoint forgets an ownership check. Checks at the interface layer
are easy to miss when new endpoints, CLI commands or agent tools are added.

## Decision

Repositories require an `OwnerScope` argument on every method that touches athlete-owned
data and always filter by it. No unscoped get-by-id exists for athlete-owned entities. A
foreign resource is indistinguishable from a missing one (`NOT_FOUND`). IDs are UUIDv7 as
defence in depth.

## Consequences

- Cross-athlete operations (none in V1) would need explicit, separately reviewed repositories.

## Verification

- AT-IDOR-001 at repository, application and API levels.
- A test enumerates repository methods and fails if one lacks an `OwnerScope` parameter (P3).
