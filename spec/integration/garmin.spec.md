# Garmin integration

Owner of INT-GAR-005 to INT-GAR-010. Master spec §11 to §13, §67. ADR-001, ADR-012, ADR-015,
ADR-026. Implemented in Phase P7.

Garmin never connects to the LLM. Data flows:
Garmin -> adapter -> capability layer -> validation -> normalisation -> canonical domain ->
repository -> application -> AI coach (through tools).

## 1. Verified provider facts (re-verified 2026-10-26 for P7)

Sources: Garmin Connect Developer Program overview, Personal API page and FAQ, the Personal
API Swagger documentation, and the official `garmin/connect-personal-api-python` repository
(links in section 9). `tools/garmin_openapi.py` re-extracts the Swagger document for the next
re-verification; Garmin's documents are not copied into the repository.

- Garmin offers a **Personal** track (individuals, own data only, "not intended for commercial
  applications serving multiple end users") and Business tracks (Starter, Enterprise).
- Personal: Activity (JSON and .FIT), Health and Women's Health (JSON); **pull only**; Training
  and Courses APIs **not included**.
- Personal authentication: the athlete creates an API key in Garmin Connect Web (expiry 30,
  90, 180 days or one year; shown once; Garmin emails a reminder about a week before expiry).
  The key is exchanged for an access token with
  `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token`
  and header `Authorization: ApiKey <key>`, without a body. The response body **is** the
  token (a JWT, not JSON); the official client reads its expiry from the `exp` claim and
  requests a new token when less than one hour remains. The token lasts 24 hours (or until key
  expiry); there are no refresh tokens. Deleting the key revokes it and its tokens.
- Data calls go to the same host with `Authorization: Bearer <token>` and
  `Accept: application/json` (official client). The Swagger document ("Garmin Connect
  Personal API" 1.0.0, OpenAPI 3.0.1) lists the servers `https://personal.connectapi.garmin.com`
  and `https://personal.connectapi.garmin.cn` and 26 paths, all `GET`.
- Activity endpoints (Swagger):
  - `GET /activitylist-service/activities/search/activities`: offset pagination with `start`
    (default 0) and `limit` (default 99); `startDate` and `endDate` (inclusive, `YYYY-MM-DD`,
    by the activity's **local** start date); `sortOrder` (`asc` or `desc`, default `desc`);
    `excludeChildren` (default true: legs of a multisport activity are not listed); further
    filters. Returns an array of `SummarizedActivityDTO`.
  - `GET /activity-service/activity/{activityId}` (int64): one `ActivitySummaryDTO`;
    documented responses 200, 400, 401, 403, 404, 500.
  - `GET /download-service/files/activity/{activityId}`: "Download an activity file in its
    original format", media type `application/x-zip-compressed`.
- Wellness endpoints state their maximum date ranges: daily summaries one year; sleep
  summaries, Body Battery and hydration 31 days; steps and stress 28 days.
- **Not documented** in any source checked: rate limits, maximum page size, historical depth,
  the error body format.
- The official client's authenticator caches the bearer token in a plaintext file and the
  cache cannot be disabled; it returns activity downloads as bytes held fully in memory, puts
  response bodies into its errors and does not retry (ADR-026).

## 2. Personal-tier adapter (INT-GAR-005)

Configured capabilities:

| Capability | Value | Basis |
|---|---|---|
| accessTier | PERSONAL | configured by the athlete |
| activityRead, healthRead, pullSupport | SUPPORTED | CONFIGURED (Garmin overview) |
| fitDownload | SUPPORTED, verified on first sync | CONFIGURED (Swagger download endpoint), then OBSERVED |
| pushSupport, trainingPublish, coursesPublish | UNSUPPORTED | CONFIGURED |
| historicalData | UNKNOWN until observed | not documented |

The adapter performs reads only. No endpoint is called unless documented in the official
client or the Garmin Swagger documentation (assumption A-06). P7 calls exactly three: the
activity search, the activity details and the activity download (plus the token endpoint);
P11 adds one daily endpoint per recovery metric (section 12).

## 3. Credentials and tokens (INT-GAR-006)

- The athlete stores the API key through a CLI command with hidden input
  (`coach secret set garmin-api-key --expires-on YYYY-MM-DD`). It goes to the
  CredentialProvider (Windows Credential Manager via `keyring`), never to files, `.env`,
  chat, logs or the database (SEC-SEC-001). The database stores only a **handle** and the key
  expiry date entered by the athlete.
- A `TokenBroker` in `coach.integrations.garmin` exchanges the key for an access token and
  keeps the token **in memory only**, re-exchanging it when less than one hour remains (the
  official client's margin), or after half its life when it lives less than two hours (a key
  about to expire yields short tokens), and once after a 401. The official client's
  authenticator and its token cache are not used.
- The token is a JWT whose signature the client cannot verify; its `exp` claim is read only to
  schedule renewal. A response that is not three base64url parts with a numeric `exp` in the
  future is a protocol error (`TOKEN_FORMAT`); a key that is not visible ASCII without
  whitespace is refused before any request (`KEY_FORMAT`). Neither can then reach a header
  where the HTTP library would quote it in an error.
- A key whose recorded expiry date has passed is not sent (`KEY_EXPIRED`); a missing key ends
  the sync before any request (`KEY_MISSING`, not an integration error: Garmin is simply not
  configured).
- Tokens and keys never appear in URLs, logs, errors, telemetry or prompts. The AI coach has
  no path to them (ADR-001; import-linter contract).
- Key expiry is tracked; the athlete is reminded 7 days before (SEC-SEC-004).

## 4. Synchronisation (INT-GAR-007)

- Pull by date windows (configurable, default 7 days) with a persisted cursor (last
  successfully processed window and activity IDs). Interrupted syncs resume from the cursor.
- Initial backfill is conservative (default 8 weeks) until `historicalData` is observed.
- Each provider response is stored byte-exact as RAW before normalisation (INT-GAR-009),
  except daily recovery payloads, which are minimised (section 12, ADR-032).
- Idempotent by `(provider, providerActivityId)` and raw SHA-256 (deduplication spec).
- Recovery categories are fetched only with matching consent (FR-REC-005). Women's Health
  and nutrition data are not fetched (PRIV-CON-004).

### 4.1 Algorithm (P7 precision)

The application `SyncService` runs one provider-agnostic algorithm over the `FitnessProvider`
port (provider-capabilities spec section 1):

1. Preconditions, without any request: consent ACTIVITIES; capabilities `activityRead` and
   `pullSupport` SUPPORTED (else `CAPABILITY_UNAVAILABLE`); a stored, unexpired key; no
   postponement in force (`notBefore`, after a rate limit or a long `Retry-After`).
2. Range. Dates are calendar dates as Garmin filters them (local start date). The range ends
   on the latest calendar date anywhere (`now + 14 h`). It starts on the day after
   `completedThrough` minus one window (the last completed window is re-scanned so activities
   uploaded late are found), or, on the first sync, 8 weeks back; an interrupted window is
   resumed from its start. `--since DATE` re-scans from a date; a date before the backfill
   horizon is refused when `historicalData` is UNSUPPORTED and, while it is UNKNOWN, the
   request is a read probe whose result is recorded (step 6).
3. Each window (7 days, the last one shorter) is listed page by page with `sortOrder=asc`,
   `start` and `limit` (page size 20, the official client's default; at most 50 pages per
   window, and never more than the 1000 IDs the cursor holds). Every page is stored as RAW
   (category `GARMIN_ACTIVITY_LIST`, with the request window); RAW is content-addressed, so
   identical pages (empty ones, typically) share one RawSource that keeps the window of the
   first request. A page shorter than the page size ends the window.
4. Every listed activity that is neither in the cursor nor already linked to an Activity as
   `(garmin, activityId)` is imported:
   1. when `fitDownload` is not UNSUPPORTED and no earlier ingestion record of this
      activity's file exists, its original file (`GARMIN_ACTIVITY_FILE`, ZIP) goes through the
      same pipeline: archive controls, sandboxed parsing, normalisation rules of the files
      spec. HTTP 404 means the activity has no file (manual entries); the first successful
      download records `fitDownload` as OBSERVED SUPPORTED.
   2. its details (`GARMIN_ACTIVITY`, JSON) go through the ingestion pipeline as a provider
      record (files spec section 9).
   3. the ID joins the cursor, which is saved after every activity.
   The file goes first, so an activity linked to its provider record has had both payloads
   handled. A rejected payload opens an IMPORT_FAILED issue and the sync continues; it is not
   retried automatically (a rejected record marks the activity as handled; a rejected or
   imported file is not downloaded again). A payload over the size cap is recorded the same
   way (`TOO_LARGE`, `RESPONSE_LIMIT`) and skipped. Any other provider failure stops the
   sync (step 7).
5. A window is completed when every activity was processed; `completedThrough` advances to
   the window's end, but never past the day before the earliest current calendar date
   (`now - 12 h`).
6. `historicalData` is recorded as OBSERVED SUPPORTED when a window ending before the backfill
   horizon lists at least one activity. An empty answer proves nothing and changes nothing.
7. Success sets the status SYNCED with `lastSuccessAt` (freshness) and clears
   INTEGRATION_ERROR. A provider failure, after the retry policy, keeps the progress made,
   sets the status PENDING with the reason and detail codes, and activates INTEGRATION_ERROR
   (section 5).

The downloaded file and the details record of one activity become one logical Activity
through the deduplication tiers (same start, compatible sport); file observations never
carry the provider record ID, which belongs to provider records only.

## 5. Degradation (INT-GAR-008)

When Garmin is unavailable, the key is expired or revoked, or authentication fails:

- the INTEGRATION_ERROR condition is set with a reason code;
- local history remains usable and file ingestion continues;
- the sync is marked pending and freshness ("last successful sync") is shown;
- no data is fabricated to fill the gap (AT-DEG-001).

## 6. RAW persistence (INT-GAR-009)

JSON and ZIP payloads are stored unmodified with endpoint category, request window and
receive time. Exception (P11, ADR-032): daily recovery payloads carry data of consent
categories other than their metric's, so they are minimised before storage (section 12). ZIP payloads go through the archive controls (SEC-UPL-003) before the FIT
inside is parsed.

Precision (P7): every payload is stored as RAW before it is examined, rejected ones too, so
it can be reprocessed when the rules learn a shape the Swagger document did not show; a
download that is not a ZIP is stored as `UNKNOWN`. The ZIP payload is its own RawSource, and
each member another (as for uploads). Provider payloads are not malware-scanned
(`NOT_APPLICABLE`): they come
authenticated from the provider over TLS, and every parser runs in the sandbox. List pages
are stored but never normalised: they may contain social data (names of people who liked or
commented), which is never read, sent to the AI or exported.

## 7. Throttling (INT-GAR-010)

Rate limits are not documented. The client applies a configurable request budget (default 1
request per second, burst 5) and the retry policy of NFR-REL-002, honouring `Retry-After`.
HTTP 429 sets `PROVIDER_RATE_LIMITED` and postpones the sync.

Precision (P7): the retry policy runs in the application around each provider call (so it
covers any provider, and the fake provider in tests). `Retry-After` (seconds or HTTP date)
sets the minimum wait; when it exceeds the maximum delay (60 s) the sync stops at once and
records `notBefore`, before which no request is made.

## 8. Open questions (resolved in P7 from authoritative sources only)

| ID | Question | Status (2026-10-26) |
|---|---|---|
| G-1 | Data API base URL and authorization header for data calls | Resolved: section 1 (official client, Swagger servers) |
| G-2 | Response schemas (from Swagger and redacted real responses) | Resolved from Swagger for the three activity endpoints; mapping in section 10. Real responses may still differ: unknown or mistyped fields are rejected, never guessed |
| G-3 | Historical depth, rate limits, maximum page size and date range | Partly: list date filter and default page size documented; rate limits, maximum page size and historical depth stay UNKNOWN (conservative defaults, sections 4.1 and 7) |
| G-4 | Content of the activity download ZIP (FIT assumed) | Partly: "original format" as `application/x-zip-compressed`; members are handled as uploads (FIT, TCX, GPX), anything else is rejected with an issue |
| G-5 | Whether the Personal API terms allow processing data with a third-party AI provider; the athlete reviews them before Garmin-sourced data reaches the LLM | Resolved by the athlete's declaration (athlete's review of 2026-10-31): the Personal API data may be processed by a third-party AI provider. Recorded as the athlete's statement; the application has not verified it against Garmin's terms. The P13 gate is lifted on this basis; AI_PROCESSING consent still applies |
| G-6 | Garmin activity types mapped to canonical sports and subtypes (from observed values) | Partly: only the documented type keys are mapped (section 10); others are OTHER with `providerType` kept, and the file's sport wins |
| G-7 | Semantics of proprietary health metrics (treated as PROVIDER_DERIVED) | Partly (P11): Body Battery's daily `charged` is read as a proprietary recovery signal (PROVIDER_DERIVED, never a decision on its own, recovery spec 3); Garmin stress, HRV status and sleep scores are not mapped |
| G-8 | Garmin's per-activity RPE and feel fields (`directWorkoutRpe`, `directWorkoutFeel`) are athlete entries made in Garmin Connect, but only athlete entries in this application may carry subjective fields | Resolved (athlete's review of 2026-10-31): the RPE is mapped (section 10, rules 1.1.0) with lower priority than the athlete's entry in the application. The Swagger documents an integer without a scale; the athlete confirmed the meaning (CR-10, 1 to 10). Only values both plausible encodings read alike are decoded; the first real activity with an RPE settles the encoding. `directWorkoutFeel` has no canonical field and stays unmapped |

Business tiers (OAuth 2.0 reported by third parties, PKCE unconfirmed, push delivery) are out
of V1. If adopted, webhook endpoints require signature verification, a replay window and
idempotent handling (threat model).

## 9. Sources

- https://developer.garmin.com/gc-developer-program/overview/
- https://developer.garmin.com/gc-developer-program/personal/personal-api/
- https://developer.garmin.com/gc-developer-program/personal/personal-faq/
- https://developer.garmin.com/gc-developer-program/personal/swagger-docs/
- https://github.com/garmin/connect-personal-api-python (`constants.py`, `auth.py`,
  `client.py`)
- https://github.com/garmin/fit-python-sdk

## 10. Activity record normalisation rules (`garmin_activity` 1.1.0)

Input: one `ActivitySummaryDTO` (details endpoint). Units are those stated by the Swagger
document. Nothing is inferred that the record does not state; text stays untrusted and is
screened for secrets like file text.

| Canonical field | Garmin field | Rule |
|---|---|---|
| provider record ID | `activityId` | integer; must equal the requested ID (`ACTIVITY_ID_MISMATCH`) |
| `classification.sport`, `classification.subtype` | `activityTypeDTO.typeKey` | `running` RUNNING; `trail_running` RUNNING/TRAIL; `cycling` CYCLING; `road_cycling` CYCLING/ROAD; `swimming` SWIMMING; any other key OTHER (the documented keys only, G-6) |
| `classification.providerType` | `activityTypeDTO.typeKey` | `garmin:<typeKey>` |
| `metadata.name`, `metadata.description` | `activityName`, `description` | untrusted text |
| `timing.startTimeUtc` | `summaryDTO.startTimeGMT` | ISO 8601 without offset, read as UTC; required (`NO_START`) |
| `timing.utcOffset` | `summaryDTO.startTimeLocal` minus `startTimeGMT` | only when a whole number of minutes within ±14 h |
| `timing.originalTimezone` | `timeZoneUnitDTO.timeZone` | only when it is a valid IANA name |
| `timing.elapsedSeconds`, `timing.movingSeconds` | `summaryDTO.elapsedDuration`, `movingDuration` | seconds |
| `distance.meters` | `summaryDTO.distance` | meters |
| `elevation.gainMeters`, `elevation.lossMeters` | `summaryDTO.elevationGain`, `elevationLoss` | meters |
| `cardiovascular.averageHr`, `cardiovascular.maxHr` | `summaryDTO.averageHR`, `maxHR` | bpm, rounded half up |
| `performance.averageSpeedMps`, `performance.maxSpeedMps` | `summaryDTO.averageSpeed`, `maxSpeed` | m/s |
| `performance.cadence` (RUNNING) | `summaryDTO.averageRunCadence`, `maxRunCadence` | steps per minute |
| `performance.cadence` (CYCLING) | `summaryDTO.averageBikeCadence`, `maxBikeCadence` | rpm |
| `performance.power` | `summaryDTO.averagePower`, `maxPower` | watts, source UNKNOWN |
| running dynamics (RUNNING) | `summaryDTO.strideLength` (cm), `verticalOscillation` (cm), `groundContactTime` (ms) | to meters, millimetres, milliseconds |
| `swimming.lengthCount` (SWIMMING) | `summaryDTO.numberOfActiveLengths` | count |
| `training.anaerobicEffect` | `summaryDTO.anaerobicTrainingEffect` | provider metric `garmin`, PROVIDER_DERIVED |
| `training.load.internal.providerLoad` | `summaryDTO.activityTrainingLoad` | provider metric `garmin`, PROVIDER_DERIVED |
| `subjective.rpe` (1.1.0, G-8) | `summaryDTO.directWorkoutRpe` (int32, scale not documented) | 1 to 9 as such; 20 to 100 in tens as a tenth; 10 (RPE 10, or RPE 1 in tens) and any other value stay unknown; a non-integer is a schema rejection |

Not mapped: `trainingEffect` (the Swagger text does not say it is the aerobic effect; the FIT
file states it), pool length (its unit handling is ambiguous in the Swagger text), temperature,
calories, stress, Body Battery, SpO2, respiration, `directWorkoutFeel` and every other field
(G-7, G-8). Rules 1.1.0 apply to activities already synchronised through `coach activity
reprocess` (ADR-029): their records are stored byte-exact (R-27).

Rejections (ingestion record and IMPORT_FAILED issue, files spec section 9): not a JSON object
or not UTF-8 (`SCHEMA_INVALID`, `JSON`); a mapped field with the wrong JSON type
(`SCHEMA_INVALID`, `FIELD_TYPE`); a negative or non-finite number (`SEMANTIC_INVALID`,
`NEGATIVE_VALUE`); a start before 1990 or more than a year ahead (`SEMANTIC_INVALID`,
`START_OUT_OF_RANGE`); a value outside the canonical range (`NORMALIZATION_FAILED`,
`FIELD_VALUE`). A JSON payload is at most 4 MiB and is parsed in process (the standard JSON
parser executes nothing; depth errors are rejections).

## 12. Daily recovery payloads (`garmin_daily` 1.0.0, P11)

Re-verified against the Swagger document on 2026-10-30 (`tools/garmin_openapi.py`). One
documented endpoint per recovery metric, each requested only with that metric's consent
(recovery spec section 5), so a payload is never stored without it:

| Metric (consent) | Endpoint | Days per request | RAW category | Value read |
|---|---|---|---|---|
| RESTING_HR (RECOVERY) | `GET /wellness-service/wellness/dailyHeartRate?date=` (DailyHeartRateDTO) | 1 | `GARMIN_DAILY_HEART_RATE` | `restingHeartRate`; `calendarDate.localDate`, when present, must be the requested date |
| HRV_NIGHTLY (HRV) | `GET /hrv-service/hrv/daily/{fromDate}/{toDate}` (HrvDailySummaryDTO) | 28 (none documented: conservative) | `GARMIN_HRV` | `hrvSummaries[].lastNightAvg` by `calendarDate` |
| SLEEP_DURATION (SLEEP) | `GET /sleep-service/sleep/dailySleepSummariesByDate?startDate&endDate` (DailySleepDTO[]) | 31 (documented) | `GARMIN_SLEEP` | `sleepTimeSeconds` (naps excluded) by `calendarDate` |
| BODY_BATTERY_CHARGED (PROVIDER_RECOVERY_METRICS) | `GET /wellness-service/wellness/bodyBattery/reports/daily?startDate&endDate` (BodyBatteryDailyReportDTO[]) | 31 (documented) | `GARMIN_BODY_BATTERY` | `charged` by `date` |

- Dates are the athlete's calendar days as Garmin dates them. HTTP 204 (documented for HRV),
  an empty body or a 404 (not documented) means no data.
- The normaliser reads nothing else. A payload that is not valid JSON or not shaped as the
  document describes is `PROVIDER_PROTOCOL_ERROR` `DAILY_FORMAT`, a resting heart rate
  `calendarDate` that is not a `ConnectDate` naming the requested day included. A value that
  is absent, not a number (an integer too large for a float included), a sleep time of 0 s
  (a night without measured sleep) or outside the
  metric's range (resting HR 25 to 150 bpm, HRV 1 to 300 ms, sleep 0 to 86,400 s, Body
  Battery 0 to 100) is left out; a day stated twice with different values is left out.
- **Minimised RAW (ADR-032, precision of INT-GAR-009).** The payloads hold more than the
  value read, and part of it belongs to other consent categories (Swagger, 2026-10-30): the
  sleep summary carries sleep stress, average heart rate, an HRV adjustment, SpO2, breathing
  disruption and a free-text note; the daily heart rate carries abnormal heart rate values;
  the Body Battery report carries sleep and stress events. A payload is therefore decoded in
  memory and never stored: the RAW kept is a canonical document of the values read with the
  request window (contract `recovery-payload`; category: the endpoint's plus `_MINIMISED`).
- A response that is unreadable (`DAILY_FORMAT`), over the size cap (`RESPONSE_TOO_LARGE`) or
  another HTTP 4xx (`HTTP_4XX`) stores nothing, is counted as unreadable and the sync goes on
  with the next window and metric. Any other failure ends the sync PENDING and is recorded
  in the integration status like an activity failure, with the postponement a rate limit
  asks for (section 7).
- The integration status keeps, per metric, the last day requested: the next sync starts
  there less two settling days, so days without data are not asked again. The activity and
  recovery syncs of one `coach garmin sync` share one adapter (one token, one request
  budget); the recovery metrics are synchronised even without the ACTIVITIES consent.

## 11. HTTP transport and error mapping (P7 precision)

- Only the documented servers are allowed; the global server is the default. Every request
  uses TLS with certificate verification and a 30 s timeout. Redirects are never followed:
  the Authorization header must not reach another host.
- Response bodies are read with a cap: 4 MiB for JSON, the ZIP upload limit (64 MiB) for
  downloads.
- Errors carry codes only: never the response body, a header, the URL query or a credential.

| Situation | Error | Detail | Retried |
|---|---|---|---|
| timeout, network or TLS failure, a body cut off before its announced length | `PROVIDER_UNAVAILABLE` | `TIMEOUT`, `NETWORK` | yes |
| HTTP 408, 5xx | `PROVIDER_UNAVAILABLE` | `HTTP_408`, `HTTP_5XX` | yes, honouring `Retry-After` |
| HTTP 429 | `PROVIDER_RATE_LIMITED` | `HTTP_429` | yes, honouring `Retry-After` |
| HTTP 401 on a data call | token renewed once, then `PROVIDER_AUTH_FAILED` | `HTTP_401` | no |
| HTTP 401 or 403 from the token endpoint | `PROVIDER_AUTH_FAILED` | `KEY_REJECTED` | no |
| HTTP 403 on a data call | `PROVIDER_AUTH_FAILED` | `HTTP_403` | no |
| HTTP 404 on a download or details call | the activity has no file, or no longer exists: skipped and counted | | no |
| other HTTP 4xx | `PROVIDER_PROTOCOL_ERROR` | `HTTP_4XX` | no |
| HTTP 3xx | `PROVIDER_PROTOCOL_ERROR` | `REDIRECT` | no |
| body over the cap | `PROVIDER_PROTOCOL_ERROR` | `RESPONSE_TOO_LARGE` | no; for one activity's payload it is recorded as rejected and skipped (4.1) |
| a header value that is not printable ASCII | `PROVIDER_PROTOCOL_ERROR` | `REQUEST_INVALID` | no request sent |
| list page not an array of objects with an integer `activityId` | `PROVIDER_PROTOCOL_ERROR` | `LIST_FORMAT` | no |
| more pages than allowed in one window | `PROVIDER_PROTOCOL_ERROR` | `PAGINATION` | no |
| token response not a valid JWT | `PROVIDER_PROTOCOL_ERROR` | `TOKEN_FORMAT` | no |
| recorded key expiry passed | `PROVIDER_AUTH_FAILED` | `KEY_EXPIRED` | no request made |
| stored key not visible ASCII without whitespace | `PROVIDER_AUTH_FAILED` | `KEY_FORMAT` | no request made |
