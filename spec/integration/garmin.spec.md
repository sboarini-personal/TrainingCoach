# Garmin integration

Owner of INT-GAR-005 to INT-GAR-010. Master spec §11 to §13, §67. ADR-001, ADR-012, ADR-015.
Implemented in Phase P7.

Garmin never connects to the LLM. Data flows:
Garmin -> adapter -> capability layer -> validation -> normalisation -> canonical domain ->
repository -> application -> AI coach (through tools).

## 1. Verified provider facts (checked 2026-10-24; re-verify before P7)

Sources: Garmin Connect Developer Program overview, Personal API page and FAQ, and the
official `garmin/connect-personal-api-python` repository (links in section 9).

- Garmin offers a **Personal** track (individuals, own data only, "not intended for commercial
  applications serving multiple end users") and Business tracks (Starter, Enterprise).
- Personal: Activity (JSON and .FIT), Health and Women's Health (JSON); **pull only**; Training
  and Courses APIs **not included**.
- Personal authentication: the athlete creates an API key in Garmin Connect Web (expiry 30,
  90, 180 days or one year; shown once). The key is exchanged for an access token with
  `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token`
  and header `Authorization: ApiKey <key>`. The token lasts 24 hours (or until key expiry);
  there are no refresh tokens. Deleting the key revokes it and its tokens.
- The official Python client documents read-only GET operations for activities (search with
  offset pagination, details, workouts, file download as ZIP) and wellness data (daily
  summaries, sleep, HRV, Body Battery, stress, heart rate, HR zones, SpO2, and others).
- The official client's authenticator caches the bearer token in a plaintext file and the
  cache cannot be disabled; its data client accepts a token string directly.

## 2. Personal-tier adapter (INT-GAR-005)

Configured capabilities:

| Capability | Value | Basis |
|---|---|---|
| accessTier | PERSONAL | configured by the athlete |
| activityRead, healthRead, pullSupport | SUPPORTED | CONFIGURED (Garmin overview) |
| fitDownload | SUPPORTED, verified on first sync | CONFIGURED, then OBSERVED |
| pushSupport, trainingPublish, coursesPublish | UNSUPPORTED | CONFIGURED |
| historicalData | UNKNOWN until observed | not documented |

The adapter performs reads only. No endpoint is called unless documented in the official
client or the Garmin Swagger documentation (assumption A-06).

## 3. Credentials and tokens (INT-GAR-006)

- The athlete stores the API key through a CLI command with hidden input. It goes to the
  CredentialProvider (Windows Credential Manager via `keyring`), never to files, `.env`,
  chat, logs or the database (SEC-SEC-001). The database stores only a **handle** and the key
  expiry date entered by the athlete.
- A `TokenBroker` in `coach.integrations.garmin` exchanges the key for an access token and
  keeps the token **in memory only**, re-exchanging it before expiry. The official client's
  authenticator and its token cache are not used.
- Tokens and keys never appear in URLs, logs, errors, telemetry or prompts. The AI coach has
  no path to them (ADR-001; import-linter contract).
- Key expiry is tracked; the athlete is reminded 7 days before (SEC-SEC-004).

## 4. Synchronisation (INT-GAR-007)

- Pull by date windows (configurable, default 7 days) with a persisted cursor (last
  successfully processed window and activity IDs). Interrupted syncs resume from the cursor.
- Initial backfill is conservative (default 8 weeks) until `historicalData` is observed.
- Each provider response is stored byte-exact as RAW before normalisation (INT-GAR-009).
- Idempotent by `(provider, providerActivityId)` and raw SHA-256 (deduplication spec).
- Recovery categories are fetched only with matching consent (FR-REC-005). Women's Health
  and nutrition data are not fetched (PRIV-CON-004).

## 5. Degradation (INT-GAR-008)

When Garmin is unavailable, the key is expired or revoked, or authentication fails:

- the INTEGRATION_ERROR condition is set with a reason code;
- local history remains usable and file ingestion continues;
- the sync is marked pending and freshness ("last successful sync") is shown;
- no data is fabricated to fill the gap (AT-DEG-001).

## 6. RAW persistence (INT-GAR-009)

JSON and ZIP payloads are stored unmodified with endpoint category, request window and
receive time. ZIP payloads go through the archive controls (SEC-UPL-003) before the FIT
inside is parsed.

## 7. Throttling (INT-GAR-010)

Rate limits are not documented. The client applies a configurable request budget (default 1
request per second, burst 5) and the retry policy of NFR-REL-002, honouring `Retry-After`.
HTTP 429 sets `PROVIDER_RATE_LIMITED` and postpones the sync.

## 8. Open questions (resolved in P7 from authoritative sources only)

| ID | Question |
|---|---|
| G-1 | Data API base URL and authorization header for data calls |
| G-2 | Response schemas (from Swagger and redacted real responses) |
| G-3 | Historical depth, rate limits, maximum page size and date range |
| G-4 | Content of the activity download ZIP (FIT assumed) |
| G-5 | Whether the Personal API terms allow processing data with a third-party AI provider; the athlete reviews them before Garmin-sourced data reaches the LLM |
| G-6 | Garmin activity types mapped to canonical sports and subtypes (from observed values) |
| G-7 | Semantics of proprietary health metrics (treated as PROVIDER_DERIVED) |

Business tiers (OAuth 2.0 reported by third parties, PKCE unconfirmed, push delivery) are out
of V1. If adopted, webhook endpoints require signature verification, a replay window and
idempotent handling (threat model).

## 9. Sources

- https://developer.garmin.com/gc-developer-program/overview/
- https://developer.garmin.com/gc-developer-program/personal/personal-api/
- https://developer.garmin.com/gc-developer-program/personal/personal-faq/
- https://github.com/garmin/connect-personal-api-python
- https://github.com/garmin/fit-python-sdk
