# ADR-006: External data cannot modify agent instructions

- Status: Accepted
- Date: 2026-10-24
- Requirements: SEC-INJ-001, SEC-INJ-002, FR-AGT-001, FR-AGT-002

## Context

Activity names, descriptions, file metadata, CSV cells and provider strings are attacker-
controllable. Prompt-level instructions alone do not reliably stop prompt injection.

## Decision

Defence is structural:

1. External text reaches the LLM only as delimited, typed, length-capped untrusted fields.
2. Tool permissions are fixed per session in code; tools have no athlete ID, URL, path, SQL,
   shell or secret parameters.
3. Every proposal passes the deterministic policy engine; external writes need human
   confirmation.
4. Heuristic detection of instruction-like text only raises visibility (quality issue).

## Consequences

- A successful injection can at most influence the wording of advice, which the policy engine
  and athlete still gate.

## Verification

- AT-INJ-001, AT-IDOR-002; agent evaluations with injected metadata.
