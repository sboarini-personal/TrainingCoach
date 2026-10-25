# ADR-022: SQLite document persistence with the standard library

- Status: Accepted
- Date: 2026-10-25
- Amends: ADR-013 (persistence row)
- Requirements: NFR-REL-003, NFR-REL-004, FR-ACT-002, FR-MET-004, SEC-IDOR-001, SEC-INJ-003

## Context

ADR-013 planned SQLite through SQLAlchemy 2 with Alembic migrations. When P3 designed the
storage, the aggregates turned out to be best stored as their published JSON contracts
(`spec/contracts`): the persisted shape is then exactly the reviewed contract, carries
`schemaVersion`, and is validated by the contract tests. With document storage an ORM or
query builder adds little, while each dependency adds supply-chain surface (threat model) and
install friction in the OneDrive-synced repository (observed in P2).

## Decision

- Use Python's standard `sqlite3` module. One short-lived connection per operation; write
  transactions use `BEGIN IMMEDIATE`; WAL journal, `foreign_keys=ON`, a busy timeout and
  `synchronous=FULL`. Only parameterised SQL (ruff `S608` in CI).
- Store aggregates as contract documents (`document TEXT CHECK (json_valid(document))`) next
  to the indexed key columns the queries need (`athlete_id`, `version`, `sha256`, ...).
  Every query filters by `athlete_id` (ADR-016).
- Optimistic concurrency with a conditional `UPDATE ... WHERE version = ?` (NFR-REL-003).
- Database triggers make RAW sources immutable and derived metric values append-only (only
  the one-time `superseded_by` link may be set); a partial unique index allows one current
  derived value per athlete, metric and subject.
- A small migration runner (ordered versions, SQL checksums, destructive flag, no downgrade)
  replaces Alembic (spec/nfr.spec.md NFR-REL-004).
- Another database (for example PostgreSQL, if the application is ever hosted) is added as a
  separate adapter behind the same ports and the same repository contract tests.

## Consequences

- No new runtime dependency for persistence.
- Ad-hoc relational queries over document fields need SQLite JSON functions or extra indexed
  columns added by migration.
- ADR-013's persistence row is superseded by this ADR; the rest of ADR-013 stands.

## Verification

- Repository contract tests run against the in-memory and SQLite adapters.
- Migration tests (AT-MIG-001) and trigger tests.
