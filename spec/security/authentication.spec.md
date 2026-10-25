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

### 1.1 Local API token (P4)

- 32 random bytes from the operating system's cryptographic generator, URL-safe base64
  encoded (43 characters).
- Stored only in the OS secret store under the handle `local_api_token/<athleteId>` of the
  installation owner ([secrets.spec.md](secrets.spec.md)). Neither the token nor a hash of it
  is stored in the database.
- Every authentication reads the current token from the store and compares SHA-256 digests
  in constant time, so a rotation takes effect immediately in every running process.
- `coach token rotate` replaces the token; the previous token stops working at once. Audited
  as `credential.rotated` with target type `LOCAL_API_TOKEN`.
- A failed authentication returns one generic error that never says why it failed.
- The CLI authenticates with the token it reads from the OS secret store of the user running it.
  If that token is missing, `coach token rotate` issues a new one without the old one: anyone
  who can run commands as that OS user can already read that store, so this adds no access.

## 2. Local API exposure (SEC-AUTH-002)

- The HTTP server binds to `127.0.0.1` only (configurable; any non-loopback bind needs TLS and
  an explicit configuration flag).
- The `Host` header must be `127.0.0.1:<port>` or `localhost:<port>` (DNS-rebinding defence).
- No CORS headers are emitted; cross-origin browser requests are rejected.
- State-changing requests from browsers require the CSRF token (session adapter).
- Error responses never reveal whether a resource exists for another athlete
  (SEC-IDOR-001).

## 3. Rate limiting (SEC-AUTH-003)

- Failed authentication, per client identifier: 5 failures within 60 seconds lock the client
  for 60 seconds; each further lockout doubles the duration, up to 1 hour. A successful
  authentication resets the failure count and the lockout level. While a client is locked
  its credentials are not checked at all.
- API: token bucket per principal and endpoint class, refilled continuously; capacity equals
  the per-minute budget (default 120 requests per minute; upload and sync 10 per minute).
- Limited requests return HTTP 429 and emit `security.rate_limited`; failed authentications
  emit `security.authn.failed` and are audited (`auth.authenticate`, outcome FAILED).

## 4. Principal mapping

`Principal (userId) -> AthleteLink -> athleteId`. In V1 one user maps to one athlete. The
mapping is resolved once per request and becomes the owner scope for authorization
([authorization.spec.md](authorization.spec.md)).

The `IdentityDirectory` stores the links (table `identities`). V1 has a single installation
owner, created by `coach init`. A principal without a link is denied: `AuthorizationError`,
`security.authz.denied`, audited as `auth.authorize` with outcome DENIED.
