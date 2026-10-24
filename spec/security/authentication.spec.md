# Authentication

Owner of SEC-AUTH-001 to SEC-AUTH-003. Master spec §59, §61. Implemented in Phase P4.

## 1. Authentication is mandatory (SEC-AUTH-001)

Every interface that exposes private athlete data (HTTP API, UI, agent, CLI commands that
read or change athlete data) requires an authenticated principal. "It only listens on
localhost" is **not** authentication.

`Authenticator` is an application port:

| Adapter | Phase | Mechanism |
|---|---|---|
| `LocalTokenAuthenticator` | P4 (V1) | 256-bit random API token generated at installation, stored in the OS secret store, sent as `Authorization: Bearer`; the CLI reads it from the store |
| `LocalSessionAuthenticator` | P13 (if a browser UI is built) | passphrase login creating an HttpOnly, Secure, SameSite=Strict session cookie with CSRF token |
| OIDC adapter (for example Microsoft Entra ID) | only if hosted later | standard OIDC with PKCE |

Tokens are compared in constant time, can be rotated by a CLI command, and are never logged.

## 2. Local API exposure (SEC-AUTH-002)

- The HTTP server binds to `127.0.0.1` only (configurable; any non-loopback bind needs TLS and
  an explicit configuration flag).
- The `Host` header must be `127.0.0.1:<port>` or `localhost:<port>` (DNS-rebinding defence).
- No CORS headers are emitted; cross-origin browser requests are rejected.
- State-changing requests from browsers require the CSRF token (session adapter).
- Error responses never reveal whether a resource exists for another athlete
  (SEC-IDOR-001).

## 3. Rate limiting (SEC-AUTH-003)

- Failed authentication: exponential lockout after 5 failures per minute per client.
- API: per-principal request budget (default 120 requests per minute); upload and sync
  endpoints have lower budgets (default 10 per minute).
- Limited requests return HTTP 429 and emit `security.rate_limited`.

## 4. Principal mapping

`Principal (userId) -> AthleteLink -> athleteId`. In V1 one user maps to one athlete. The
mapping is resolved once per request and becomes the owner scope for authorization
([authorization.spec.md](authorization.spec.md)).
