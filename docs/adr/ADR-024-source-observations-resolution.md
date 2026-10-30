# ADR-024: Source observations and pure field-level resolution

- Status: Accepted
- Date: 2026-10-25
- Requirements: FR-ACT-002, FR-ACT-005, FR-ACT-007, FR-ACT-008, NFR-REL-001

## Context

A logical Activity can have several sources (Garmin record, FIT file, manual entry) that
arrive in any order (ADR-004), and conflicts are resolved per field (ADR-011). Every link,
merge or split must re-resolve the activity from all of its sources. Re-parsing RAW bytes each
time would tie deduplication to the parsers and be slow; keeping only the resolved activity
would lose the values of the sources that lost a field, so a later split or a better source
could not be handled correctly. The result must not depend on arrival order.

## Decision

- Each part of a RawSource is normalised once into an immutable **source observation**
  (contract `source-observation`, key `(rawSourceId, part)`): the field values that source
  provides, keyed by canonical field path, with source paths, stream references and anomalies
  the normaliser found.
- The stored Activity (NORMALIZED) is a **pure function of its linked observations**:
  resolution rules 1.0.0 rank sources per field group and break ties by raw SHA-256, never by
  arrival. It is rebuilt on every link, merge and split.
- The EFFECTIVE view applies DataOverrides, recomputes quality and fills current derived
  values at read time, with the same contract; it is never stored.
- An index of the sources of live activities lets a source belong to at most one live
  activity; the database enforces it. A merge leaves a MERGED record for lineage.
- After every change the service recomputes activity-bound metrics and synchronises quality
  issues. These steps are idempotent, and a retried command repeats them.

## Consequences

- Activities can be rebuilt at any time from observations; re-normalising RAW with a newer
  normaliser produces new observations (normaliser versions join the key in P6; implemented in
  P9 as observation revisions, ADR-029).
- Per-source values are stored next to the resolved activity (bounded duplication).
- There is no unit of work across repositories: activities changed together (merge, split)
  are saved in one transaction, and follow-up work left undone by a crash is repaired by the
  next change or a retried import (threat model R-18).

## Verification

- Property-based tests: resolution and ingestion are independent of arrival order,
  importing twice equals importing once, enrichment never adds a source's totals.
- AT-ACT-001 (with synthetic observations until P6 and P7), AT-RAW-001.
