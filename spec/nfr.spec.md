# Non-functional requirements

Owner of NFR-REL-001 to NFR-REL-005, NFR-OBS-001, NFR-OBS-002, NFR-EXP-001, NFR-TRC-001,
NFR-TRC-002.

## Reliability

### NFR-REL-001 Idempotent ingestion and commands

- Activity imports, provider synchronisation, file ingestion, external publication (when it
  exists) and retried commands MUST be idempotent.
- Imports use deterministic keys: SHA-256 of the raw bytes, and `(provider, providerObjectId)`
  for provider records (see [integration/deduplication.spec.md](integration/deduplication.spec.md)).
- State-changing API commands accept an `Idempotency-Key` header. The same key with the same
  payload returns the original result; the same key with a different payload is rejected.
  Keys are retained for at least 24 hours.

### NFR-REL-002 Retry strategy and no silent data loss

- Transient provider and network failures are retried with exponential backoff and jitter:
  base 1 s, factor 2, maximum 5 attempts, maximum delay 60 s, honouring `Retry-After`. All
  values are configurable.
- Non-transient failures (validation, authorization, 4xx except 408/429) are not retried.
- A failure is never swallowed: it produces an observability event, and when it affects
  athlete data, a DataQualityIssue or IntegrationStatus change visible to the athlete.

### NFR-REL-003 Optimistic concurrency

- Mutable aggregates (AthleteProfile, Availability, ConsentLedger, Activity enrichment,
  TrainingPlan status transitions, DataOverride chains, CoachingState) carry an integer
  `version`.
- Updates state the expected version. A mismatch fails with `CONCURRENCY_CONFLICT`; the change
  is never silently overwritten (AT-CONC-001).

### NFR-REL-004 Schema versioning and migrations

- Every persisted contract carries `schemaVersion` (semantic versioning).
- Readers accept any minor version of the major version they support.
- Additive changes bump the minor version. Breaking changes bump the major version and require
  an explicit migration strategy, rollback strategy and protection of RAW data.
- Destructive migrations never run automatically; they need human approval (master spec §81).

Database migration rules (P3, ADR-022):

- Migrations are an ordered list with consecutive version numbers, a name, the SQL, and a
  `destructive` flag. Applied migrations are recorded with the SHA-256 of their SQL.
- Opening a database applies pending non-destructive migrations, each in its own transaction.
- A pending destructive migration (dropping or deleting anything) is refused unless the caller
  passes explicit approval (AT-MIG-001). A test checks that any migration that drops an object (`DROP ...`)
  or deletes rows (`DELETE FROM`) is flagged destructive.
- A database whose recorded schema version is newer than the code, or whose applied migration
  SQL differs from the code (checksum mismatch), is refused: no downgrade, no silent edit.

### NFR-REL-005 Backup and restore (PROPOSED)

- A command exports a consistent backup of the database and blob store to a location the
  athlete chooses, and restores it. Backups contain athlete data and inherit privacy rules.

## Observability

### NFR-OBS-001 Structured events

Structured (JSON) events are emitted for at least:

| Area | Events |
|---|---|
| Integration | `integration.sync.started`, `.succeeded`, `.failed`, `integration.capability.observed` |
| Ingestion | `ingestion.file.received`, `.rejected`, `.parsed`, `.failed`, `ingestion.duplicate` |
| Activities | `activity.created`, `activity.corrected` (field path only) |
| Deduplication | `dedup.linked`, `dedup.ambiguous`, `dedup.merged`, `dedup.kept_separate`, `dedup.split` |
| Data quality | `quality.issue.raised`, `quality.issue.resolved` |
| Planning | `plan.generated`, `plan.proposed`, `plan.activated`, `plan.superseded` |
| External writes | `external_write.proposed`, `.confirmed`, `.executed`, `.refused` |
| Consent | `consent.granted`, `consent.revoked`, `consent.declined` |
| Credentials | `credential.stored`, `credential.rotated` (never the value) |
| Deletion | `deletion.requested`, `deletion.completed`, `deletion.failed` |
| Security | `security.authn.failed`, `security.authz.denied`, `security.secret_detected`, `security.rate_limited`, `security.lookup.not_found` |
| Safety | `safety.hold.activated`, `safety.hold.released` |

Events contain identifiers, counts, codes and durations, not health values or free text.

### NFR-OBS-002 Redaction

- A redaction filter is applied to every log record and telemetry export before it is
  written. It removes values of known secret types and high-entropy tokens (see
  [security/secrets.spec.md](security/secrets.spec.md)).
- Secrets MUST never be logged, even at debug level. Verified by AT-SEC-002.

## Explainability

### NFR-EXP-001 Explained plan modifications

Every significant plan modification (any adaptation other than MAINTAIN, and any load
reduction) includes: evidence references, interpretation, decision, uncertainty reasons and
confidence (AT-EXP-001).

## Traceability

### NFR-TRC-001 Lineage

Lineage MUST be queryable from a recommendation to its source:
Recommendation -> Insight -> DerivedMetricValue -> normalized field -> RawSource -> provider
record or file. Lineage records are append-only so past decisions remain explainable after
later corrections (AT-LIN-001).

### NFR-TRC-002 Requirement traceability

- Tests declare covered requirement IDs with `@pytest.mark.req(...)`; unknown IDs fail the run.
- `tools/traceability.py` generates the requirement -> acceptance test -> test matrix.
- Traceability is maintained through specs and tests, not through comments in source code.
