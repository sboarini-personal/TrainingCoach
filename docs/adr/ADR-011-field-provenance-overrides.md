# ADR-011: Field-level provenance and manual overrides

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-ACT-005, FR-ACT-006, PRIV-ACC-002

## Context

Different sources are best for different fields (provider ID from Garmin, streams from FIT,
RPE from the athlete). A single global source priority either loses data or keeps wrong data.

## Decision

Resolve conflicts per field with versioned rules, storing provenance for each resolved field.
Athlete corrections are append-only DataOverrides applied at read time; originals are never
destroyed.

## Consequences

- Each Activity carries a provenance map.
- Derived metrics depending on a corrected field are recomputed as new values.

## Verification

- AT-RAW-001, AT-ACT-001.
