# ADR-018: LLM provider abstraction and AI-processing consent

- Status: Accepted
- Date: 2026-10-24
- Requirements: PRIV-CON-003, FR-AGT-003, SEC-SEC-005, SAFE-MED-002

## Context

The AI coach sends personal training and health data to an external LLM provider, a
third-party processor. Models change over time.

## Decision

- `LLMProvider` is an application port. The default adapter targets Anthropic Claude models;
  the model ID is configuration and is recorded on every AgentDecision.
- Sending personal data to the provider requires AI_PROCESSING consent. Without it, the
  deterministic product still works.
- Context is minimised (derived summaries before raw data; no GPS traces; no credentials).
- Secret redaction and red-flag detection run deterministically **before** the LLM call. For
  that reason the primary UI is an in-application chat; an MCP-host UI cannot guarantee
  pre-LLM filtering.
- Whether Garmin's Personal API terms allow third-party AI processing of Garmin data (open
  question G-5) must be confirmed by the athlete before Garmin-sourced data is sent.

## Consequences

- Agent evaluations run against the configured model and are repeated on model changes.

## Verification

- AT-CON-001, AT-SEC-001, AT-SAFE-001.
