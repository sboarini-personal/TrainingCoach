# Authorization and IDOR prevention

Owner of SEC-AUTHZ-001 to SEC-AUTHZ-003, SEC-IDOR-001, SEC-IDOR-002. Master spec §59, §62,
§65. ADR-016. Primitives in Phase P2, complete in Phase P4.

## 1. Authorization chain (SEC-AUTHZ-001)

Every operation validates, in the application layer:

```
AUTHENTICATED USER -> ATHLETE IDENTITY -> RESOURCE OWNERSHIP -> REQUESTED RESOURCE
```

The interface layer only authenticates. Ownership is enforced by the application layer and
the repositories, so it cannot be skipped by a new endpoint, CLI command or agent tool.

`AuthorizationService.scope_for(principal)` turns an authenticated principal into an
`OwnerScope` (P4). Application services receive that scope; they never take an athlete ID
from request input.

## 2. Owner-scoped repositories (SEC-IDOR-001)

- Repository methods take an `OwnerScope` (the authenticated athlete) as a required argument.
  There is **no** repository method that loads an athlete-owned entity by ID alone.
- Queries always filter by `athlete_id = scope.athlete_id`.
- A resource that exists but belongs to another athlete is reported exactly like a missing
  one (`NOT_FOUND`, HTTP 404). The response does not reveal existence.
- A lookup by ID cannot tell a missing resource from a foreign one (by design: the query is
  owner-scoped). The interface layer reports both as `NOT_FOUND` and emits
  `security.lookup.not_found` with the requested type and ID (P13).
- `security.authz.denied` is emitted for authorization-chain failures and policy denials,
  never with the other athlete's data.
- AT-IDOR-001: athlete A requesting athlete B's activity ID is rejected at the API, the
  application and the repository level.

## 3. Identifiers (SEC-IDOR-002)

Entity IDs are UUIDv7 (RFC 9562): not sequential and not guessable in practice. This is
defence in depth only; the ownership check is the control.

## 4. Agent tool permissions (SEC-AUTHZ-002)

- Tools are classified READ, WRITE_INTERNAL, PROPOSE, EXECUTE_EXTERNAL
  ([../contracts/tools.spec.md](../contracts/tools.spec.md)).
- The agent session grants READ, WRITE_INTERNAL (allow-listed fields) and PROPOSE.
  EXECUTE_EXTERNAL and destructive operations always require a human confirmation token that
  the agent cannot obtain.
- Tool inputs never include `athleteId` (FR-AGT-002).

## 5. Mass assignment (SEC-AUTHZ-003)

- Every command has an explicit input model listing the fields a caller may set. Unknown
  fields are rejected (`additionalProperties: false` equivalent), not ignored.
- System-managed fields (`id`, `athleteId`, `version`, provenance, status, timestamps,
  computed metrics, consent state) can never be set through a generic update.

## 6. Administration

V1 has no administrator role and no administrative API. Maintenance (backup, restore,
migrations, token rotation) is done through CLI commands run by the athlete's OS user, and is
audited.
