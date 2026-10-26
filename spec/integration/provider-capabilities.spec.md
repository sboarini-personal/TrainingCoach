# Provider abstraction and capabilities

Owner of INT-GAR-001 to INT-GAR-004. Master spec §11, §12, §50. ADR-007, ADR-012.
Implemented in Phase P7.

## 1. FitnessProvider (INT-GAR-001)

Application port. Domain and application code depend on it, never on a concrete provider.

```
interface FitnessProvider:
    get_capabilities() -> ProviderCapabilities
    get_activities(window: DateRange, cursor: SyncCursor | None) -> Page[ProviderActivityRef]
    get_activity_details(ref: ProviderActivityRef) -> RawPayload   # summary JSON and/or file bytes
    get_recovery_metrics(window: DateRange, categories: set[RecoveryCategory]) -> list[RawPayload]
    get_user_profile() -> RawPayload
    sync_activities(window: DateRange) -> SyncResult              # orchestrates the calls above
```

- Adapters return **RAW payloads** plus metadata; normalisation happens in the normaliser,
  never inside the HTTP client.
- Provider errors map to typed errors: `PROVIDER_UNAVAILABLE`, `PROVIDER_AUTH_FAILED`,
  `PROVIDER_RATE_LIMITED`, `CAPABILITY_UNAVAILABLE`, `PROVIDER_PROTOCOL_ERROR`.
- Only one provider (Garmin) is implemented in V1. Another provider implements the same
  port.

### 1.1 Precision (P7)

The port as implemented in P7 (the master interface above is the target):

```
interface FitnessProvider:
    provider: str                                     # "garmin"
    get_capabilities() -> ProviderCapabilities        # configured snapshot, no request
    get_activities(window: DateRange, offset: int, limit: int) -> ActivityPage
                                                      # refs plus the page's RawPayload
    get_activity_details(ref) -> RawPayload | None    # provider record (JSON); None: gone
    get_activity_file(ref) -> RawPayload | None       # original file (ZIP); None: no file
```

- `sync_activities` is not an adapter method: the application `SyncService` implements the
  orchestration once over the port (garmin spec section 4.1), so adapters stay free of
  storage, normalisation and retries, and every provider gets the same cursor, retry and
  degradation behaviour.
- Pagination is an offset with a limit (the only pagination Garmin documents); the cursor is
  the application's.
- `get_recovery_metrics` and `get_user_profile` are added with their first consumers
  (recovery in P11, profile import with onboarding). P7 collects no data that nothing reads
  (data minimisation, PRIV-CON-004).
- `RawPayload`: provider, endpoint category, media type, bytes, provider object ID, request
  window. The normaliser of provider records is a separate component of the integration
  package, run by the ingestion pipeline (files spec section 9).
- `ProviderError(code, detail, retryAfter?)`: `code` is one of the five codes above, `detail`
  an upper-case code (garmin spec section 11), `retryAfter` seconds when the provider sent
  `Retry-After`. `PROVIDER_UNAVAILABLE` and `PROVIDER_RATE_LIMITED` are transient.
- A fake provider (`tests/fake_provider.py`) implements the port for contract, integration
  and degradation tests; the same contract suite runs against the Garmin adapter over a fake
  HTTP transport with synthetic payloads built from the Swagger schemas.

## 2. WorkoutPublisher (INT-GAR-002)

```
interface WorkoutPublisher:
    propose_workout(workout: PlannedWorkout) -> WorkoutProposal      # local, no external effect
    publish_workout(proposal_id, confirmation: ConfirmationToken) -> PublishResult
```

- `propose_workout` never contacts the provider.
- `publish_workout` requires a confirmation token issued by a human action (POL-EXTERNAL-WRITE)
  and the `trainingPublish` capability (POL-CAPABILITY). It is idempotent per proposal.
- V1 ships `LocalWorkoutPublisher`: `publish_workout` always returns
  `CAPABILITY_UNAVAILABLE` without any network call (AT-GAR-001).

Precision (P7): until the planning model exists (P12), a proposal holds the workout as its
contract document (`urn:coach:schema:workout:1`), checked for its identity fields; the
proposal ID is derived from the athlete, the workout ID and the document's SHA-256, so
proposing the same workout twice gives the same proposal. Publication asks the policy engine
first: POL-CAPABILITY denies it (`CAPABILITY_UNAVAILABLE`, parameter `trainingPublish`)
before confirmation is even considered, so a missing capability is never turned into a
confirmation request. `LocalWorkoutPublisher` holds no HTTP client at all.

## 3. Capability model (INT-GAR-003)

`ProviderCapabilities`: `provider`, `accessTier` (PERSONAL, STARTER_BUSINESS, ENTERPRISE,
UNKNOWN), and per capability `{value: SUPPORTED | UNSUPPORTED | UNKNOWN, basis: CONFIGURED |
OBSERVED, observedAt?, source}` for: `activityRead`, `healthRead`, `fitDownload`,
`pullSupport`, `pushSupport`, `historicalData`, `trainingPublish`, `coursesPublish`.

Feature gating:

| Feature | Required capability |
|---|---|
| Activity synchronisation | activityRead and pullSupport |
| Recovery metrics synchronisation | healthRead (plus consent) |
| FIT enrichment from the provider | fitDownload |
| Historical backfill | historicalData |
| Webhook endpoint | pushSupport |
| Workout publication | trainingPublish |
| Course publication | coursesPublish |

## 4. Never assumed (INT-GAR-004)

- Capabilities come from configuration (documented tier capabilities, with source and date)
  or from runtime observation. They are never inferred from the provider's name or a
  similar provider.
- `UNKNOWN` never enables a write. A read with UNKNOWN capability may be attempted once
  as a probe; the result is recorded as OBSERVED (`integration.capability.observed`).
- The current capability snapshot is stored in `IntegrationStatus` and shown to the athlete.
- If a feature's capability is not SUPPORTED, the feature degrades gracefully (for example,
  the workout stays local).

## 5. IntegrationStatus (P7 precision)

One per athlete and provider (`urn:coach:schema:integration-status:1`, owner-scoped,
versioned): `provider`, `capabilities` (the snapshot of section 3, with basis, source and
`observedAt`), `syncState` (NEVER_SYNCED, SYNCED, PENDING), `reason` and `detail` of the last
failure (codes), `lastAttemptAt`, `lastSuccessAt` (freshness), `notBefore` (no request before
it, after a rate limit), and the `cursor`: `completedThrough` (date), the `window` in
progress and the activity IDs already processed in it.

- A configured capability is re-applied from the adapter's configuration on every sync; an
  OBSERVED value always wins over a CONFIGURED one, and the change emits
  `integration.capability.observed`.
- SYNCED and PENDING carry the reasons shown to the athlete (`coach garmin status`); SYNCED
  has no reason.
