# Acceptance: data platform


### AT-MIG-001 — Migrations are explicit and never destructive by default
- Requirements: NFR-REL-004
- Level: integration. First executable: P3.

**Given** a database created by the current code
**When** it is opened again
**Then** no migration is re-applied and every stored document keeps its `schemaVersion`
**Given** a pending migration flagged destructive
**When** the database is opened without explicit approval
**Then** opening fails and nothing is changed
**Given** a database whose schema version is newer than the code, or whose applied migration SQL was edited
**Then** opening fails instead of downgrading or continuing silently.
