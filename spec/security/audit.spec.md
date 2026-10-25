# Audit

Owner of SEC-AUD-001. Master spec §54, §61, §75. ADR-023. Implemented from Phase P4.

## 1. AuditEvent (SEC-AUD-001)

Contract: [../contracts/audit-event.schema.json](../contracts/audit-event.schema.json).

| Field | Content |
|---|---|
| eventId | UUIDv7 |
| sequence | 1, 2, 3, ... with no gaps |
| occurredAt | UTC timestamp |
| actor | `USER`, `AGENT` or `SYSTEM` (V1 has one user per installation, so no actor ID is kept) |
| action | lower-case dotted code, for example `consent.revoked`, `auth.token.rotated` |
| outcome | `SUCCESS`, `DENIED`, `FAILED` |
| athleteRef | salted hash of the athlete ID, or `null` when no athlete is involved |
| target | `{type, ref}`: an upper-case type code and the salted hash of the target ID, or `null` |
| reasonCodes | upper-case codes, for example `CONSENT_REQUIRED`, `INVALID_CREDENTIALS` |
| correlationId | request or job UUID, or `null` |
| previousHash | SHA-256 of the previous event (64 zeros for the first event) |
| hash | SHA-256 of the canonical JSON of all fields above |

## 2. Identifiers are pseudonymous from the start (PRIV-DEL-003)

- Every identifier inside the event is a salted hash: `sha256(salt || id)` in hexadecimal,
  with one random 256-bit salt per installation, created with the audit log.
- The plain athlete ID is kept **outside** the hashed event, in an indexed column used only
  for owner-scoped reads. Privacy deletion sets that column to `null`; the hash chain is not
  affected, and what remains is event type, time, outcome and salted hashes.
- There are no free-text fields: action, types and reason codes are validated codes, so no
  secret, health value or athlete text can enter the audit log.

## 3. Integrity

- Append-only. The database rejects every change to an audit row except setting the plain
  athlete ID to `null` once, and rejects every delete.
- Appends are serialised (write lock) so the sequence and the chain stay linear under
  concurrent writers.
- Verification (`coach audit verify`) recomputes every hash and checks the sequence and the
  links; it reports the first broken sequence number. AT-AUD-001.

## 4. Audited actions

Authentication failures, authorization denials, consent grants, revocations and declines,
profile changes, data overrides, merges and splits, plan proposal, activation and
supersession, external write proposals, confirmations and executions, safety-hold activation
and release, deletion requests and completion, secret storage and rotation (never the value),
token rotation, backups and restores, coaching state transitions.

Codes used from P4: `auth.authenticate`, `auth.authorize`, `credential.stored`,
`credential.rotated` (target type = credential kind, so a token rotation is
`credential.rotated` on `LOCAL_API_TOKEN`), `consent.granted`, `consent.revoked`,
`consent.declined`, `policy.evaluate`.
