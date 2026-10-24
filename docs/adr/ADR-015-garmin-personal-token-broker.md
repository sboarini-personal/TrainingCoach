# ADR-015: Garmin Personal tier: own token broker, in-memory tokens

- Status: Accepted
- Date: 2026-10-24
- Requirements: INT-GAR-005, INT-GAR-006, SEC-SEC-001, SEC-SEC-004

## Context

The Personal tier exchanges a long-lived API key (up to one year) for a 24-hour access token,
without refresh tokens. The official Python client's authenticator caches the bearer token in
a plaintext file that cannot be disabled; its POSIX file permissions do not apply on Windows.
Its data client accepts a token string directly.

## Decision

- Store the API key in the OS secret store via a hidden-input CLI command; store only a handle
  and the expiry date in the database.
- Implement a `TokenBroker` in `coach.integrations.garmin` that calls the documented token
  endpoint and keeps the token in memory, renewing it before expiry.
- Use the official client only as the data transport, passing our token. If response-size caps
  are needed (activity ZIPs are held fully in memory by the client), replace the transport with
  a thin client limited to endpoints documented by Garmin.

## Consequences

- No token ever touches disk.
- A process restart requests a new token (cheap; no refresh tokens exist anyway).

## Verification

- AT-SEC-002 (P7 extension): no token in files, logs or errors after a sync.
- Unit tests for renewal timing with a fake clock.
