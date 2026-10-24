# Audit

Owner of SEC-AUD-001. Master spec §54, §61, §75. Implemented from Phase P4.

## 1. AuditEvent (SEC-AUD-001)

| Field | Content |
|---|---|
| eventId | UUIDv7 |
| occurredAt | UTC timestamp |
| actor | `{kind: USER, AGENT, SYSTEM, id}` |
| athleteRef | athlete ID (salted hash after deletion, PRIV-DEL-003) |
| action | code, for example `consent.revoked`, `plan.activated`, `override.created` |
| target | `{type, id}` |
| outcome | SUCCESS, DENIED, FAILED |
| reasonCodes | policy or error codes |
| correlationId | request or job ID |
| previousHash | SHA-256 of the previous event (tamper evidence) |

## 2. Audited actions

Authentication failures, authorization denials, consent grants and revocations, profile
changes, data overrides, merges and splits, plan proposal, activation and supersession,
external write proposals, confirmations and executions, safety-hold activation and release,
deletion requests and completion, secret storage and rotation (never the value),
token rotation, backups and restores, coaching state transitions.

## 3. Rules

- Append-only: no update or delete through the application, except privacy deletion which
  replaces athlete references by salted hashes (PRIV-DEL-003).
- No secrets, health values or free text in audit events: only codes and identifiers.
- Each event includes the hash of the previous event, so gaps or edits are detectable. A CLI
  command verifies the chain.
