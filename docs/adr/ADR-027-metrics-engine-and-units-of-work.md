# ADR-027: Metrics engine: subject dimensions, totals materialised on read, SQLite units of work

- Status: Accepted
- Date: 2026-10-29
- Requirements: FR-MET-001 to FR-MET-006, NFR-REL-001, NFR-TRC-001
- Amends: ADR-022 (SQLite access)

## Context

P8 adds the deterministic metrics engine (metrics spec section 9). Three questions came up:

1. A derived value had one subject (activity, day, week, exercise) and one number. Per-sport
   weekly totals and time in each heart-rate zone need one more qualifier, and the rule that
   distances are never summed across sports (FR-MET-006) must be enforceable.
2. Period totals change whenever any activity of the period changes, including a correction
   that moves an activity to another training day. Recomputing them eagerly after every
   change would have to know the old period as well as the new one.
3. ADR-022 opened one SQLite connection per operation and committed each write on its own.
   Measured on Windows with 1,000 synthetic activities: about 4 ms per connection and 29 ms
   per commit (`synchronous = FULL`), so recomputing the metrics took 184 s, and an activity
   command left partial work behind when a later step failed (threat model R-18).

## Decision

- **Subject dimension.** `MetricSubject` gains an optional dimension: a sport code or a zone
  code `Z1` to `Z10`. A metric declares the subject kinds it describes, its dimension, and
  whether a SPORT metric may also cover all sports (`crossSport`). Distance and elevation are
  not cross-sport, so a value across sports cannot even be built. Storage keeps the dimension
  inside the subject key column, so no migration is needed and one current value still
  exists per athlete, metric and subject. Contract `derived-metric-value` 1.1.0 (additive).
- **Aggregations.** A metric has either a calculation over named inputs or an aggregation
  (SUM) over contributions of its single input; one UNKNOWN contribution makes the total
  UNKNOWN, and the report shows the known part as a lower bound.
- **Materialised on read.** Per-activity metrics are recomputed eagerly after every change
  (as session RPE load already was). Period totals are computed when a period is read and
  stored through the idempotent derived-value path: unchanged snapshots store nothing, a
  changed one supersedes. No total can be stale for a reader.
- **Units of work.** `SqliteDatabase.batch()` runs a multi-step command on one connection and
  one `BEGIN IMMEDIATE` transaction; reads and writes inside join it, each write is a
  savepoint (a failed write rolls back only itself), and the batch commits once or rolls back
  entirely on an exception. The application receives it as the `Batch` port (a no-op for
  in-memory stores). Activity commands (ingest, correct, merge decisions, split, acknowledge),
  period reports and recomputation (50 activities per batch, to keep write locks short)
  use it. A nested batch is a savepoint of the outer one, so a failed inner command rolls
  back only itself.

## Consequences

- The same measurement: recomputation 2.2 s instead of 184 s, an 8-week report 0.3 s
  instead of 8.3 s, ingestion of 1,000 activities 38 s instead of 190 s.
- An activity command is atomic: R-18 no longer applies to activity commands; it still
  applies across commands (an upload stores its RAW bytes before the activity command).
- A batch holds the write lock for its duration; other writers wait up to the busy timeout
  (10 s), which is why recomputation is chunked.
- A value whose metric no longer applies (a sport correction, a smaller or missing zone
  model, a period that lost its activities) is retired by a NOT_APPLICABLE value (contract
  1.1.0); the history keeps what was computed. Values of an older zone model are also
  recognised by their snapshot and ignored on read.
- An independent review (2026-10-29) found these gaps before the first commit, together with
  a crash for more than 24 h in one zone, sparse heart rate reported as complete, snapshots
  that ignored a change of uncertainty alone and series read again on every recomputation;
  all are fixed with tests.

## Verification

- `tests/integration/test_sqlite_database.py` (batch commit, savepoint rollback, nesting),
  `tests/integration/test_activity_storage.py` (an ingest that fails part-way stores nothing).
- `tests/unit/test_metrics_engine.py`, `tests/unit/test_activity_metrics.py`,
  `tests/unit/test_analytics.py`, `tests/contract/test_derived_value_repository_contract.py`
  (the dimension is part of the subject).
