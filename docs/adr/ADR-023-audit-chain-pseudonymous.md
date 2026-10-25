# ADR-023: Hash-chained audit log with pseudonymous identifiers

- Status: Accepted
- Date: 2026-10-25
- Requirements: SEC-AUD-001, PRIV-DEL-003, PRIV-DEL-001

## Context

The audit log must be tamper-evident (each event hashes the previous one) and must survive
privacy deletion, after which only event type, time, outcome and a salted hash of the athlete
may remain. Replacing athlete IDs inside already-hashed events would break the chain, and
re-hashing the chain after deletion would destroy the tamper evidence.

## Decision

- Hashed events never contain plain identifiers: the athlete and every target ID are stored as
  `sha256(salt || id)` with a per-installation random salt kept in the database.
- The plain athlete ID lives in a separate, non-hashed column used only for owner-scoped
  reads. Deletion sets it to `null`; the chain stays valid.
- Events have no free text. Actor is a kind only (one user per installation in V1).
- Database triggers reject deletes and every update except nulling the plain athlete ID.
- Appends take the database write lock; the sequence has no gaps.

## Consequences

- Looking up audit events for a specific entity means computing its salted hash.
- Pseudonymity rests on the random part of UUIDv7 identifiers (74 bits) plus the salt; the
  salt is not a secret against someone holding the database file.
- A multi-user deployment would need actor references, pseudonymised the same way.

## Verification

- AT-AUD-001: tampering with an event, its order or its link is detected; forgetting an
  athlete keeps the chain valid.
