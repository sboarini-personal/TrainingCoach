# ADR-001: The LLM cannot access provider credentials

- Status: Accepted
- Date: 2026-10-24
- Requirements: SEC-SEC-002, SEC-SEC-003, INT-GAR-006, FR-AGT-001

## Context

The AI coach processes untrusted content and its prompts and outputs leave the process (LLM
provider). Any credential reachable by the agent could be exfiltrated through prompt
injection or appear in provider logs.

## Decision

Credentials (Garmin API key, access tokens, LLM key, local API token) live only in the secret
store and the integration layer. The call path is
`AI coach -> application tool -> provider adapter -> CredentialProvider -> secret store`.
The agent has no tool that reads secrets, and the `coach.agent` package may not import
`coach.integrations` or `coach.infrastructure`.

## Consequences

- Provider actions requested by the agent go through application use cases, which return
  data, never credentials.
- Debugging provider issues uses redacted logs, never prompt inspection of credentials.

## Verification

- import-linter contract "AI agent never touches provider adapters, credentials or storage
  directly" (CI, every commit).
- AT-SEC-002 plants secrets and scans prompts, tool results, logs and the database.
