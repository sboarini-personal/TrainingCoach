# ADR-008: Deterministic calculations outside the LLM

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-MET-001, FR-MET-004, FR-AGT-004

## Context

LLMs are unreliable at arithmetic and can fabricate numbers. Training decisions depend on
correct totals, paces, loads and conversions.

## Decision

"Calculate in code, interpret with AI." All metrics, aggregations, conversions, deduplication
and load calculations run in `coach.metrics` and other deterministic modules with versioned
methods. The LLM receives computed values with metadata and explains them. Agent output is
checked so that numbers trace to tool results.

## Consequences

- Every metric needs a registry entry and tests before the coach can talk about it.
- The layering contract places `coach.metrics` and `coach.domain` below everything else.

## Verification

- AT-MET-001; agent evaluations for fabricated metrics.
- import-linter layering contract.
