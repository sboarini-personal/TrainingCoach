# Architecture

Personal Multisport Performance Coach. Spec version 1.1.0. The specification in `spec/` is
authoritative; this document explains how the code is organised to satisfy it. Decisions are
in [adr/](adr/README.md).

## 1. Context

```
                         ATHLETE
                            |
              +-------------+-------------+
              |                           |
          COACH UI / CLI             FILE UPLOAD
              |                           |
          AI COACH  <--- typed tools      |
              |                           |
        APPLICATION LAYER (use cases, authorization, unit of work, ports)
              |                           |
   +----------+-----------+               |
   |          |           |               |
PLANNING   METRICS     POLICY             |
 ENGINE    ENGINE      ENGINE             |
   +----------+-----------+               |
              v                           v
        CANONICAL DOMAIN MODEL  <---  NORMALISER  <--- FILE PARSERS (sandboxed)
              ^                           ^
              |                           |
          REPOSITORIES                GARMIN ADAPTER <- capability layer <- GARMIN (pull)

Cross-cutting: identity, consent, authorization, secrets, audit, provenance,
data quality, observability, safety.
```

External systems: Garmin Connect (Personal tier, pull-only, outbound HTTPS), LLM provider
(outbound HTTPS, consent-gated), OS secret store, local file system.

## 2. Layers and dependency rule

| Package | Responsibility | May import |
|---|---|---|
| `coach.domain` | entities, value objects, domain services; pure | nothing in `coach` |
| `coach.metrics` | metric registry, deterministic calculations | domain |
| `coach.planning` | plan engine, interference engine, adaptive loop | metrics, domain |
| `coach.policy` | deterministic policy engine | metrics, domain |
| `coach.application` | use cases, authorization chain, ports, unit of work | policy, planning, metrics, domain |
| `coach.integrations` | Garmin adapter, file parsers, normaliser | application (ports), lower layers |
| `coach.infrastructure` | SQLite repositories, blob store, keyring, logging/redaction | application (ports), lower layers |
| `coach.api`, `coach.cli`, `coach.agent` | interface adapters and composition | everything below; `coach.agent` never imports integrations or infrastructure |

Enforced by import-linter contracts in `pyproject.toml` on every CI run:

1. Layered architecture: outer layers depend inward only.
2. The AI agent never imports provider adapters, credentials or storage (ADR-001).

Domain and application code are cloud-neutral (ADR-014). Ports are defined in the
application layer and implemented outside it.

Additionally, `tests/unit/test_architecture_purity.py` checks that `coach.domain`,
`coach.metrics`, `coach.planning` and `coach.policy` import only the standard library and
`coach`, and never use I/O, clock or randomness modules or call `now()`/`today()`. IDs and the
current time are passed in by the application layer.

Domain packages (P2): `common` (errors, time, units, quality, provenance, sport, evidence,
untrusted text, finite numbers), `athlete`, `availability`, `goals`, `consent`, `coaching`,
`onboarding`; `raw` (P3); `activity` (P5); `ingestion` (P6).

P10 additions:

| Package | Content |
|---|---|
| `coach.domain` | `strength.vocabulary` (patterns, muscle groups, body regions, contraindication tags, interference levels), `strength.exercise` and `strength.catalogue` (catalogue 1.0.0, aliases), `strength.sets` (SETS stream), `strength.session` (StrengthSession, reconciliation into StrengthSetResults), `strength.regions` (regions loaded, region names), `strength.progression` (rules 1.0.0), `strength.prescription` (rules 1.0.0); `athlete.profile` exercise aliases |
| `coach.metrics` | `strength` (volume load, hard sets, muscle shares); `str.volume_load`, `str.volume_load_total`, `str.weekly_sets_by_muscle`; dimension MUSCLE |
| `coach.planning` | `interference` (demands of planned and completed sessions, baseline rules 1.0.0, modifiers) |
| `coach.application` | `StrengthService` (set entries, relabels, watch-sets choice, executed sets); engine volume load and `ActivityService.refresh`; `AnalyticsService.strength_week`, `progression`, `interference`; `strength.strengthSessionId` in the EFFECTIVE view; codecs for exercises, sets, strength sessions and the strength block of a workout |
| `coach.integrations.files` | FIT set messages (parser 1.2.0, parser output 1.2.0), FIT exercise mapping 1.0.0, normalisation rules 1.2.0 |
| `coach.cli` | `strength exercises`, `alias`, `sets`, `log`, `relabel`, `watch-sets`, `week`, `progress`, `interference` |

P9 additions:

| Package | Content |
|---|---|
| `coach.domain` | `activity.segments` (laps and pool lengths), `performance.powerSensor`, observation revisions (`supersedes`, `same_session`), `athlete.settings` (`AnalysisSettings`: zones, reliable power sensors, FTP, usual pool) |
| `coach.metrics` | `analyses` (stream analyses), `running`, `cycling`, `swimming`, `tennis`, `trends` (comparable groups, least-squares trend); catalogue entries per sport; dimensions ZONE and STROKE; qualifying-only period totals |
| `coach.application` | engine: streams read once per recomputation, analyses with reuse, measured power in the EFFECTIVE view, power zones, swimming and tennis; `AnalyticsService.trend`; `ActivityService.renormalise`; `IngestionService.reprocess`; codecs for laps and lengths |
| `coach.integrations.files` | parsers 1.1.0 (FIT lap, length and device types; TCX laps), parser output 1.1.0, normalisation rules 1.1.0, multisport windows |
| `coach.infrastructure` | migration 5 (`source_observation_revisions`) |
| `coach.cli` | `activity reprocess`, `activity correct`, `profile sensor`, `profile ftp`, `analysis trend` |

P6 additions:

| Package | Content |
|---|---|
| `coach.domain` | `ingestion` (limits, immutable ingestion records, CSV templates and formula prefixes); `activity.streams` (time series); IMPORT_FAILED issues; CSV rows as athlete entries |
| `coach.application` | `ingestion` (`IngestionService`: the 13-stage pipeline, duplicates, idempotency keys, secret screening of file text, records and failure issues), `files` (ports `FileFormats`, `Quarantine`, `MalwareScanner`; `DecodedSession`), `csv_export`, codecs for ingestion records and time series, port `IngestionRecordRepository` |
| `coach.integrations.files` | `detection` (extensions, signatures, FIT CRC, XML root scanner), `archive` (in-memory ZIP with limits), `sandbox` (isolated worker process, Job Object or RLIMIT_AS, output limit), `worker`, parsers (`fit_parser` with the Garmin FIT SDK, `tcx_parser`, `gpx_parser`, `csv_parser`, `xmlsafe`), `output` (strict decoding), `normalise` (rules 1.0.0), `formats` (adapter) |
| `coach.infrastructure` | `quarantine` (random names, NOT_SCANNED scanner), `sqlite/ingestion` (migration 4), in-memory ingestion records; data layout gains `quarantine/` |
| `coach.cli` | `consent list/grant/revoke`, `profile timezone`, `import`, `activity list/export`, `issue list/ack` (`cli/activities.py`); runtime wires activities and ingestion |

P5 additions:

| Package | Content |
|---|---|
| `coach.domain` | `activity`: field catalogue (paths, types, groups, correctable fields), source observations, `Activity` and source links, local time and training day, field-level resolution 1.0.0, DataOverride chains and the effective values, deduplication scoring 1.0.0, DataQualityIssue (ADR-024) |
| `coach.metrics` | `quality` (quality rules 1.0.0: expected fields, anomalies, reliability, confidence, reasons), `volume` (weekly volume over ACTIVE activities), activity bindings in `catalog` (`any.srpe_load` inputs and effective-view target) |
| `coach.application` | `activities` (`ActivityService`: ingestion tiers, effective view, corrections with secret screening, merge, keep separate, split, acknowledgements, metric recompute and issue sync; `build_activity`), `contracts/activities`, ports `ActivityRepository`, `ObservationRepository`, `OverrideRepository`; lineage resolves activities down to RAW sources |
| `coach.infrastructure` | `sqlite/activities` (migration 3), in-memory activity, observation and override repositories |

P4 additions:

| Package | Content |
|---|---|
| `coach.policy` | `engine` (Action, PolicyContext, `evaluate`, policy set 1.0.0) |
| `coach.application` | `authentication`, `authorization`, `identity`, `rate_limit`, `credentials`, `audit`, `consent`, `policy_gate`, `screening`, `events` |
| `coach.infrastructure` | `keyring_credentials`, `events` (log sink), `logs` (redacted file log), `sqlite/identities`, `sqlite/audit` (migration 2) |
| `coach.cli` | `main` (`coach init`, `token rotate`, `secret set/list`, `audit verify`), `runtime` (composition root) |

Request path (CLI now, HTTP API later): read token from the OS store -> `AuthenticationService`
(lockout, constant-time check) -> `AuthorizationService.scope_for` -> application service with
the owner scope -> `PolicyGate` (consents and conditions from storage) -> repositories. Audit
events and observability events are written along the way.

P3 additions:

| Package | Content |
|---|---|
| `coach.metrics` | `registry` (MetricDefinition, namespace and origin rules), `derived` (DerivedMetricValue, `derive`), `catalog` (`any.srpe_load`) |
| `coach.application` | `contracts/` (one codec per aggregate, domain <-> contract document), `ports` (repositories, blob store, clock, ids), `raw_storage`, `derived_metrics`, `lineage` |
| `coach.infrastructure` | `sqlite/` (database, migrations, aggregate, raw-source and derived-value repositories), `blobs` (per-athlete content-addressed files), `datadir` (data directory and synced-folder guard), `memory` (reference implementations) |

## 3. Runtime and deployment (V1)

- Single local process on the athlete's Windows PC: HTTP API bound to loopback, background
  sync worker, CLI. No inbound internet surface.
- Data directory outside synced folders: default `%LOCALAPPDATA%\CoachDeportivo`
  (`COACH_DATA_DIR`), containing SQLite (WAL) and the content-addressed blob store. The
  application refuses a data directory inside OneDrive, Dropbox or Google Drive folders
  (PRIV-STO-001).
- Secrets in Windows Credential Manager through `keyring`.
- The repository (code, specs, synthetic fixtures) stays in its current folder (athlete
  decision D-1) and is pushed to a private GitHub repository (D-3).

## 4. Data architecture

See `spec/functional/activities.spec.md` and ADR-009.

```
PROVIDER / FILE --bytes--> RAW (immutable, sha256) --normaliser vN--> source observations
     --field-level resolution (pure, ADR-024)--> NORMALIZED logical activities
     --(+ DataOverride at read time)--> EFFECTIVE --metrics vN--> DERIVED (append-only)
     --> INSIGHT --> RECOMMENDATION / DECISION   (evidence references down the chain)
```

Logical Activities are separate from their sources (ADR-004); conflicts resolve per field
(ADR-011); plans are immutable versions (ADR-010).

Storage (P3, ADR-022): one SQLite file `<data_dir>/coach.sqlite3`.

| Table | Holds | Integrity |
|---|---|---|
| `aggregates` | mutable aggregates as contract documents (profile, availability, goals, events, consent ledger, coaching state) | owner column on every query; optimistic `version` |
| `raw_sources` | immutable RAW metadata | unique `(athlete_id, sha256)`; trigger rejects every update |
| `derived_metric_values` | append-only metric values with input snapshots | partial unique index: one current value per athlete, metric and subject (the subject key column holds the key and, from P8, its dimension); trigger allows only the one-time `superseded_by` link |
| `schema_migrations` | applied migrations with SQL checksums | refuses downgrades, edited migrations and unapproved destructive migrations |
| `activities` (P5) | logical activities (NORMALIZED) as contract documents; status ACTIVE, PENDING_REVIEW or MERGED | owner column on every query; optimistic `version`; start-time index for candidate search |
| `activity_sources` (P5) | sources of live activities | primary key `(athlete_id, raw_source_id, part)`: a source belongs to at most one live activity |
| `source_observations` (P5) | normalised content of each raw source part | trigger rejects every update; foreign key to `raw_sources` |
| `data_overrides` (P5) | athlete corrections | trigger rejects every update; unique supersession link and one first override per entity and field keep one chain per field |
| `source_observation_revisions` (P9) | later revisions of a source's observation (re-normalisation, ADR-029) | trigger rejects every update; foreign key to `source_observations`; reads use the latest revision |

Data quality issues are aggregates in `aggregates` (kind `data_quality_issue`).

Units of work (P8, ADR-027): a multi-step command runs as one SQLite batch, one
connection and one transaction, with a savepoint per write. Activity commands, period
reports and metric recomputation use it.

| `ingestion_records` (P6) | one immutable record per import attempt, upload or provider payload (origin, outcome, reason, hashes, files, activities) | trigger rejects every update; unique idempotency key per athlete; indexes by hash and time |

Integration statuses (P7) are aggregates in `aggregates` (kind `integration_status`): the
capability snapshot, sync state with reason codes, freshness, `notBefore` and the cursor. The
coaching state with its conditions is kind `coaching_state`.

Time series, laps and pool lengths (P9) and strength sets (P10) are content-addressed JSON
blobs in the athlete's blob store, referenced from observations. Strength sessions (P10) are
aggregates in `aggregates` (kind `strength_session`), one per activity. Uploads wait in `<data_dir>/quarantine/` under random names only while they are
processed.

RAW bytes live in `<data_dir>/raw/<athlete_id>/<sha256[0:2]>/<sha256>`, written atomically,
read-only, verified on read.

## 5. Key flows

**File ingestion:** upload -> validation -> quarantine -> SHA-256 -> sandboxed parser ->
schema and semantic validation -> normalisation -> deduplication -> one transaction
persisting RawSource, blob, Activity and links -> events.

**Garmin sync (P7, ADR-026):** `coach garmin sync` -> consent and capability checks (no
request) -> `SyncService` splits the range into 7-day windows -> per window, search pages
through the `FitnessProvider` port (Garmin adapter: TokenBroker with the key from the secret
store and the token in memory, request budget, thin HTTPS transport without redirects) ->
each page stored as RAW -> for each new activity: original file (ZIP) then details record
(JSON) through the ingestion pipeline (archive controls and sandbox for the file, the
`garmin_activity` normaliser for the record) -> deduplication links both to one activity ->
cursor saved after every activity. Retries with backoff and `Retry-After` wrap every port
call. Failures keep the progress, mark the sync PENDING, set INTEGRATION_ERROR and never block
local features.

**Metrics (P8, ADR-027):** every activity change recomputes the per-activity metrics of
its EFFECTIVE view (session RPE load, speed and pace, time in heart-rate zones from the
time series and the athlete's zone model, an age-predicted one applied to the
training day as a labelled estimate, ADR-028); reading a day or a week materialises its
totals (per sport, and across sports for time and session RPE load only); the week report
shows external and internal load separately. Values carry their input snapshot down to
activities, derived values and RAW sources.

**Sport analytics (P9, ADR-029, ADR-030):** the same recomputation reads each stream once
and runs the sport analyses (running drift, pace variability, intervals and efficiency;
cycling work, power zones, fade and interval consistency on MEASURED power only; pool
swimming per stroke and per set; tennis regional load); `coach analysis trend` groups a
metric's sessions into comparable groups and fits a trend per group, never across groups.
`coach activity reprocess` re-reads stored RAW with newer normalisation rules and adds
observation revisions.

**Strength (P10, ADR-031):** a FIT strength session's set messages become its SETS
stream (exercises mapped to the catalogue only when exactly equivalent); the athlete's
`coach strength log`, `relabel` and `watch-sets` change the activity's StrengthSession; the
executed sets are reconciled from both on every read and change, and the change recomputes
the activity's volume load. `coach strength week` materialises hard sets per muscle group
and the volume load; `progress` and `interference` are read models of rules 1.0.0, with
reason codes and evidence.

**Adaptive loop:** activity ingested -> workout execution matching -> metrics -> recovery
assessment -> insights -> decision (code thresholds plus AI interpretation) -> policy engine
-> new PROPOSED plan version (if not MAINTAIN) -> athlete activation.

**Chat message:** consent check -> secret redaction -> red-flag detection -> context
assembly with untrusted-data wrapping -> LLM with typed tools -> output validation -> policy
engine for proposals -> AgentDecision audit.

## 6. Cross-cutting concerns

| Concern | Mechanism | Spec |
|---|---|---|
| Identity | local token or session authenticator, principal to athlete mapping | security/authentication.spec.md |
| Authorization | owner-scoped repositories, application-layer chain | security/authorization.spec.md, ADR-016 |
| Consent | append-only ledger, enforcement at ingestion, reads, agent context, policy | security/privacy.spec.md |
| Secrets | CredentialProvider over the OS store, `SecretValue` wrapper, redaction | security/secrets.spec.md |
| Audit | hash-chained append-only events | security/audit.spec.md |
| Provenance and lineage | per-field provenance, evidence references | nfr.spec.md NFR-TRC-001 |
| Data quality | quality object on every record, issues visible to the athlete | functional/activities.spec.md |
| Observability | structured events, redaction filter | nfr.spec.md |
| Safety | red-flag detection, SAFETY_HOLD condition, policy denial | safety/sport-safety.spec.md |

## 7. Assumptions register

| ID | Assumption | Status |
|---|---|---|
| A-01 | One athlete in V1; model supports several with strict ownership; no multi-tenant infrastructure | Open, to confirm |
| A-02 | Local-first deployment on the athlete's PC; cloud optional later | Accepted (ADR-014) |
| A-03 | Python stack | Resolved (ADR-013) |
| A-04 | Garmin access through the Personal tier | Open, to confirm |
| A-05 | Garmin facts as verified on 2026-10-24; re-verified before P7 | Re-verified 2026-10-26, including the Swagger document (garmin spec section 1) |
| A-06 | Garmin endpoints, headers and schemas only from the official client, Swagger documentation or real responses | Rule |
| A-07 | Training day = local start date in the activity's timezone; athlete timezone with ESTIMATED_VALUE when unknown | Proposed |
| A-08 | Canonical units metric/SI; display per athlete preference | Proposed |
| A-09 | LLM provider: Anthropic Claude behind `LLMProvider`; AI_PROCESSING consent required | Open, to confirm |
| A-10 | Encryption at rest through OS full-disk encryption (BitLocker) in V1 | Open, to confirm |
| A-11 | Malware scanning through an optional OS scanner hook; unscanned imports flagged | Decided in P6: no scanner adapter in V1, every upload NOT_SCANNED (ADR-025) |
| A-12 | RPE on CR-10; daily check-in on a documented 1 to 5 scale | Proposed |
| A-13 | Only an explicit athlete action releases SAFETY_HOLD | Proposed |
| A-14 | Specs, code and logs in English; athlete-facing language configurable, default Spanish | Proposed |
| A-15 | Women's health and nutrition data not ingested | Proposed |
| A-16 | Deduplication tolerances and weights as in the deduplication spec (rules 1.0.0), versioned | Proposed |
| A-17 | V1 is for adults (18 or older) | Proposed |
| A-18 | Coaching parameters (freshness windows, baseline thresholds, plausibility limits, expected fields and reliability rules of quality rules 1.0.0, recovery windows, open-water reliability, the metric input ranges and the 20 s sample gap of time in zones from P8, and the P9 sport analytics parameters: warm-up, steadiness, splits, repetition tolerances, power zone fractions and FTP window, set sizes, tennis load levels, terrain bands and trend thresholds, and the P10 strength parameters: hard-set thresholds, heavy session
size, calf-intensive size, long-run duration, hard run and swim RPE, region tables, FIT
exercise mapping, progression RPE overshoot) are initial, versioned values to be reviewed with
real data | Proposed |
| A-19 | At most one ACTIVE priority-A goal | Proposed |
| A-20 | Revoking LONG_TERM_STORAGE starts deletion of data older than the active coaching period after confirmation | Proposed |

Provider questions G-1 to G-8 and their status: `spec/integration/garmin.spec.md` section 8.

## 8. Delivery phases

| Phase | Scope | Status |
|---|---|---|
| P0 | Repository discovery (`docs/discovery/00-discovery-report.md`) | Done |
| P0.5 | Workspace: git, uv project, quality gates, CI | Done |
| P1 | Specification, ADRs, architecture, threat model, privacy, CLAUDE.md | Done |
| P2 | Domain core and shared value objects; athlete, availability, goals, consent, coaching state, onboarding planner; OwnerScope and repository port; secret redaction; P2 contracts and contract tests | Done |
| P3 | Metric registry and session RPE load; derived values; RAW sources and per-athlete blob store; SQLite persistence and migrations (ADR-022); contract-to-domain codecs; lineage; data-directory guard | Done |
| P4 | Local token authentication with lockout and rate limiting; authorization chain and identity directory; credentials in the OS secret store; hash-chained audit log (ADR-023); consent service and gate; policy engine core; chat secret screening; `coach` CLI; threat model v1 | Done |
| P5 | Canonical Activity and field catalogue, source observations and field-level resolution (ADR-024), time model, data quality rules and issues, deduplication tiers with merge, keep separate and split, DataOverride corrections and the effective view, weekly volume, activity storage (migration 3) | Done |
| P6 | File ingestion (FIT, TCX, GPX, CSV, ZIP): detection, archives, sandboxed parsers (ADR-025), normalisation rules 1.0.0, time series, ingestion records and failure issues, CSV export, CLI import | Done |
| P7 | FitnessProvider port and fake provider, capability model and POL-CAPABILITY, IntegrationStatus, LocalWorkoutPublisher, Garmin Personal adapter (token broker, thin transport, request budget, `garmin_activity` normaliser 1.0.0), SyncService with windows, cursor, retries and degradation (ADR-026), provider payloads through the ingestion pipeline, CLI `garmin sync` and `garmin status` | Done |
| P8 | Deterministic metrics engine: subject dimensions, aggregations, speed and pace, time in heart-rate zones, period totals materialised on read, week report with external and internal load, recomputation, SQLite units of work (ADR-027), age-predicted zones as a labelled estimate (ADR-028), CLI `load`, `metrics`, `activity show`, `profile hr-zones`, `profile birth-date` | Done |
| P9 | Laps, pool lengths and power sensors from files with re-normalisation (ADR-029); running, cycling, swimming analytics and tennis load as stream analyses (ADR-030); measured power attribution; per-stroke dimension; qualifying-only work total; comparable groups and trends; CLI `activity reprocess`, `activity correct`, `profile sensor`, `profile ftp`, `analysis trend` | Done |
| P10 | Exercise catalogue 1.0.0 and athlete aliases; FIT strength sets (SETS stream, normalisation rules 1.2.0) and StrengthSession reconciliation; volume load and weekly hard sets per muscle group (MUSCLE dimension); progression rules 1.0.0; strength prescription rules 1.0.0 (workout contract 1.1.0); interference engine rules 1.0.0 (ADR-031); CLI `strength` | Done |
| P11 | Check-in, recovery, patterns, insights, report data | Next |
| P12 | Policy engine (full), planning, versioning, execution, adaptive loop | |
| P13 | AI coach: tools, prompts, LLM adapter, agent evaluations, UI | |
| P14 | Garmin workout publication | Deferred: needs a tier with `trainingPublish` |
| P15 | Hardening, deletion workflow, observability, end-to-end verification | |

Critical path: P2, P3, P5, P6, P8, P11, P12, P13. P7 can run in parallel with P8 to P10
after P6.
