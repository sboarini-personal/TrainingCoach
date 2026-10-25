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
`onboarding`; `raw` (P3).

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
PROVIDER / FILE --bytes--> RAW (immutable, sha256) --normaliser vN--> NORMALIZED
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
| `derived_metric_values` | append-only metric values with input snapshots | partial unique index: one current value per athlete, metric and subject; trigger allows only the one-time `superseded_by` link |
| `schema_migrations` | applied migrations with SQL checksums | refuses downgrades, edited migrations and unapproved destructive migrations |

RAW bytes live in `<data_dir>/raw/<athlete_id>/<sha256[0:2]>/<sha256>`, written atomically,
read-only, verified on read.

## 5. Key flows

**File ingestion:** upload -> validation -> quarantine -> SHA-256 -> sandboxed parser ->
schema and semantic validation -> normalisation -> deduplication -> one transaction
persisting RawSource, blob, Activity and links -> events.

**Garmin sync:** scheduler -> capability check -> TokenBroker (key from secret store, token in
memory) -> windowed pull -> RAW persistence -> normalisation -> deduplication -> cursor
update. Failures set INTEGRATION_ERROR and never block local features.

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
| A-05 | Garmin facts as verified on 2026-10-24; re-verified before P7 | Rule |
| A-06 | Garmin endpoints, headers and schemas only from the official client, Swagger documentation or real responses | Rule |
| A-07 | Training day = local start date in the activity's timezone; athlete timezone with ESTIMATED_VALUE when unknown | Proposed |
| A-08 | Canonical units metric/SI; display per athlete preference | Proposed |
| A-09 | LLM provider: Anthropic Claude behind `LLMProvider`; AI_PROCESSING consent required | Open, to confirm |
| A-10 | Encryption at rest through OS full-disk encryption (BitLocker) in V1 | Open, to confirm |
| A-11 | Malware scanning through an optional OS scanner hook; unscanned imports flagged | Verify in P6 |
| A-12 | RPE on CR-10; daily check-in on a documented 1 to 5 scale | Proposed |
| A-13 | Only an explicit athlete action releases SAFETY_HOLD | Proposed |
| A-14 | Specs, code and logs in English; athlete-facing language configurable, default Spanish | Proposed |
| A-15 | Women's health and nutrition data not ingested | Proposed |
| A-16 | Deduplication tolerances as in the deduplication spec, versioned | Proposed |
| A-17 | V1 is for adults (18 or older) | Proposed |
| A-18 | Coaching parameters (freshness windows, baseline thresholds, plausibility limits, recovery windows, open-water reliability) are initial, versioned values to be reviewed with real data | Proposed |
| A-19 | At most one ACTIVE priority-A goal | Proposed |
| A-20 | Revoking LONG_TERM_STORAGE starts deletion of data older than the active coaching period after confirmation | Proposed |

Open provider questions G-1 to G-7: `spec/integration/garmin.spec.md` section 8.

## 8. Delivery phases

| Phase | Scope | Status |
|---|---|---|
| P0 | Repository discovery (`docs/discovery/00-discovery-report.md`) | Done |
| P0.5 | Workspace: git, uv project, quality gates, CI | Done |
| P1 | Specification, ADRs, architecture, threat model, privacy, CLAUDE.md | Done |
| P2 | Domain core and shared value objects; athlete, availability, goals, consent, coaching state, onboarding planner; OwnerScope and repository port; secret redaction; P2 contracts and contract tests | Done |
| P3 | Metric registry and session RPE load; derived values; RAW sources and per-athlete blob store; SQLite persistence and migrations (ADR-022); contract-to-domain codecs; lineage; data-directory guard | Done |
| P4 | Local token authentication with lockout and rate limiting; authorization chain and identity directory; credentials in the OS secret store; hash-chained audit log (ADR-023); consent service and gate; policy engine core; chat secret screening; `coach` CLI; threat model v1 | Done |
| P5 | Canonical Activity, quality, deduplication, overrides | Next |
| P6 | File ingestion (FIT, TCX, GPX, CSV, ZIP) with sandbox | |
| P7 | FitnessProvider, capabilities, Garmin Personal adapter, sync, degradation | |
| P8 | Deterministic metrics engine | |
| P9 | Running, cycling, swimming analytics; tennis load | |
| P10 | Exercise catalogue, strength prescription and execution, interference | |
| P11 | Check-in, recovery, patterns, insights, report data | |
| P12 | Policy engine (full), planning, versioning, execution, adaptive loop | |
| P13 | AI coach: tools, prompts, LLM adapter, agent evaluations, UI | |
| P14 | Garmin workout publication | Deferred: needs a tier with `trainingPublish` |
| P15 | Hardening, deletion workflow, observability, end-to-end verification | |

Critical path: P2, P3, P5, P6, P8, P11, P12, P13. P7 can run in parallel with P8 to P10
after P6.
