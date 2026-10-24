# ADR-003: Strength training is first-class

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-STR-001, FR-STR-002, FR-STR-003, FR-STR-004, FR-STR-007

## Context

Endurance-oriented tools often treat strength as an optional accessory, which loses the
interference and progression information the coach needs.

## Decision

Strength has its own controlled vocabularies (movement patterns, muscle groups), an exercise
catalogue with canonical IDs, structured prescriptions, execution capture per set, history-
based progression and an interference engine that sees all modalities. Plans include strength
unless contraindicated or impossible, and say why when omitted.

## Consequences

- Strength data has its own entities (StrengthSession, StrengthSetResult, Exercise).
- The planner must reason about interference across modalities.

## Verification

- Schema enforces vocabularies and prescription fields (contract tests).
- AT-STR-001, AT-STR-002.
