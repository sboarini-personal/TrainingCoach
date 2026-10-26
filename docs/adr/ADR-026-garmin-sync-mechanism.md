# ADR-026: Garmin sync mechanism: thin transport, application orchestration, shared pipeline

- Status: Accepted
- Date: 2026-10-26
- Requirements: INT-GAR-001, INT-GAR-005 to INT-GAR-010, NFR-REL-002, SEC-SEC-003
- Implements: ADR-007, ADR-012, ADR-015

## Context

P7 connects the Garmin Personal API. ADR-015 kept the official Python client as a possible
data transport, to be replaced if response-size caps were needed. Reading the client
(2026-10-26) showed that it returns downloads as bytes held fully in memory without a cap,
puts response bodies into its errors (`HttpError.response_text`) and offers no retry. The
Swagger
document, published inside the Garmin developer site, states the endpoints, parameters and
response schemas the adapter needs. The spec's port also listed `sync_activities` as an
adapter method.

## Decision

- **Thin transport.** `coach.integrations.garmin` talks HTTP through the standard library
  (`urllib`, default TLS verification, proxies from the environment) behind an
  `HttpTransport` port: redirects are refused, bodies are read under a cap, errors carry
  codes only. No new runtime dependency. Only the documented servers and the three activity
  endpoints plus the token endpoint are reachable.
- **Token broker** as ADR-015: the token lives in memory as a `SecretValue`; it is renewed
  when less than one hour remains and once after a 401.
- **Throttling** in the adapter (token bucket, default 1 request per second, burst 5);
  **retries** in the application around each port call (NFR-REL-002), so every provider and
  the fake provider share them.
- **Orchestration in the application.** `SyncService` implements windows, pagination, the
  cursor, capability observation, degradation and the INTEGRATION_ERROR condition once, over
  the port. Adapters return RAW payloads and nothing else.
- **One ingestion pipeline.** Provider payloads go through the file ingestion pipeline: a
  JSON record through the provider's record normaliser (in process), a downloaded ZIP through
  archive controls and the parser sandbox. Every payload gets an ingestion record and, when
  rejected, an IMPORT_FAILED issue.

## Consequences

- One more code path (ingestion records with a provider origin) instead of a second
  pipeline; contract changes are additive (ingestion record, source observation, raw source).
- The official client is no longer used at all; its endpoint list and Swagger stay the
  reference, re-checked with `tools/garmin_openapi.py`.
- Garmin JSON is parsed in process: the standard JSON parser executes nothing, the payload is
  capped at 4 MiB, and depth errors are rejections.

## Verification

- Contract suite for the port run against the fake provider and the Garmin adapter over a
  fake transport.
- AT-GAR-001, AT-DEG-001, AT-SEC-002 (P7 extension), unit tests for the token broker (fake
  clock), the transport (redirects, caps), throttling and retries.
