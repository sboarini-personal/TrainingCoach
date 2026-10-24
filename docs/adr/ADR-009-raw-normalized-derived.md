# ADR-009: RAW / NORMALIZED / DERIVED data architecture

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-ACT-002, INT-GAR-009, NFR-TRC-001

## Context

Parsers, normalisers and metric formulas will improve. Without the original data, past
results cannot be recomputed or explained.

## Decision

Three layers: RAW (immutable, content-addressed bytes plus metadata), NORMALIZED (canonical,
rebuildable from RAW by versioned normalisers), DERIVED (append-only metric values with
method, version and inputs). Overrides apply at read time (EFFECTIVE view). Only privacy
deletion removes RAW.

## Consequences

- Storage grows with raw files; retention follows consent (LONG_TERM_STORAGE).
- Lineage queries walk from derived values down to RAW.

## Verification

- AT-RAW-001, AT-LIN-001.
