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
