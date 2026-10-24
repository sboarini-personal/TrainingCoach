# Phase 0: Repository Discovery Report

| | |
|---|---|
| Specification | Personal Multisport Performance Coach: Master SDD Bootstrap Specification v1.1 |
| Phase | 0 (repository discovery) |
| Date | 2026-10-24 |
| Status | Complete. Code scaffolding waits only on the workspace decisions in §14. |
| Tests executed | None. No code exists. Every test named below is PROPOSED. |

---

## 1. Current state

### 1.1 Repository

Inspected project root `Projects/Coach Deportivo/`.

| Item | Finding |
|---|---|
| Files | None. Directory empty (created 2026-10-24 17:50). |
| Version control | Not a git repository. |
| README / project CLAUDE.md | Absent. |
| Build / package files | Absent. |
| Source code / tests | Absent. |
| Persistence, authentication, agent/LLM integration | Absent. |
| CI/CD, containers, infrastructure-as-code | Absent. |
| Environment configuration, secret references | Absent. No secrets found, nothing to redact. |
| Garmin integration, file-processing code | Absent. |

Classification: **greenfield**. Per spec §83 the SDD and architecture foundation is bootstrapped first. There is nothing to preserve or migrate.

### 1.2 Host environment

| Tool | Version | Relevance |
|---|---|---|
| Python | 3.14.0 | Candidate runtime |
| uv | 0.11.29 | Environment and lockfile manager |
| pip | 25.3 | |
| Node.js / npm | 24.17.0 / 11.13.0 | Alternative runtime; possible future UI tooling |
| .NET SDK | 8.0.426 | Alternative runtime |
| Git | 2.45.1 | VCS (not yet initialised for this project) |
| Docker | 29.5.3 | Available; not required for V1 |
| Java, Go, Azure CLI, pnpm, yarn | Not installed | |

OS: Windows 11 Pro 10.0.26100. Shell: Git Bash.

### 1.3 Workspace location (security-relevant)

The project lives under `%USERPROFILE%\OneDrive - <employer>\...` (path redacted for publication), a OneDrive folder synchronised to an organisational (employer) Microsoft 365 tenant. See risk R-01 in §8.

The parent working directory contains unrelated projects. They were listed by name only and not opened (data minimisation). None relate to this product.

### 1.4 Global conventions

The user-level `~/.claude/CLAUDE.md` only references the graphify skill. No project conventions exist yet; this specification defines them.

---

## 2. Gap analysis

Every capability is absent. The table maps each spec area to its target phase and any external dependency.

| Area (spec §) | Current | Target phase | External dependency |
|---|---|---|---|
| SDD artefacts: /spec, requirement IDs, ADRs, threat model (§62, §71-72, §76-77) | Missing | P1 | None |
| Workspace: VCS, toolchain, lint/type/test, secret scanning | Missing | P0.5 | User decision on location (D-1) |
| Athlete profile, experience, availability, equipment, health constraints (§5-7) | Missing | P2 | None |
| Goals and events (§8) | Missing | P2 model, P12 feasibility | None |
| Consent (§56) | Missing | P2 model, P4 enforcement | None |
| Onboarding and agent states (§4, §9, §66) | Missing | P2 state machine, P13 dialogue | LLM |
| Metric registry, RAW/NORMALIZED/DERIVED, lineage (§17, §25-26, §53) | Missing | P3 | None |
| AuthN, AuthZ, ownership, secrets, audit (§13, §59, §61) | Missing | P2 primitives, P4 complete | OS keystore |
| Canonical Activity, quality, uncertainty, dedup, overrides (§18-24) | Missing | P5 | None |
| File ingestion FIT/TCX/GPX/CSV and parser security (§15-16) | Missing | P6 | Garmin FIT SDK (library) |
| FitnessProvider and Garmin adapter (§11-13) | Missing | P7 | Athlete-created Garmin Personal API key |
| Deterministic metrics engine (§25, §27) | Missing | P8 | None |
| Running, cycling, swimming analytics; tennis load (§28-30, §36) | Missing | P9 | None |
| Exercise catalogue, strength planning and execution, interference (§31-35) | Missing | P10 | None |
| Daily check-in, recovery, patterns, insights (§37-41) | Missing | P11 | None |
| Planning, versioning, execution, adaptive loop, policy engine (§42-50) | Missing | P12 | None |
| Reporting (§51) | Missing | P11-P13 | LLM for narrative only |
| AI coach, typed tools, evals (§54, §60, §65) | Missing | P13 | LLM provider plus athlete consent |
| Garmin workout publication (§11, §50) | Missing | **Not V1 on the Personal tier** (see §7) | Starter or Enterprise tier |
| Privacy controls and deletion (§57-58) | Missing | P4 design, P15 complete | None |
| Observability (§75) | Missing | P2 redaction, P15 complete | None |

Impact on the V1 Definition of Done (§82): "supported Garmin synchronization according to actual capabilities" means pull sync of the athlete's own activities and health data. "Garmin workout publication" drops out of V1 because the Personal tier has no Training or Courses API (verified, §7). AT-GAR-001 (local structured workout, no publication attempt) governs that behaviour.

---

## 3. Requirement map

Initial stable IDs. In P1 the authoritative copy moves to `spec/requirements-index.md`. IDs are never reused or renumbered.

Spec §72 lists categories "such as"; these are added: FR-INS (insights), FR-POL (policy engine), FR-RPT (reporting), FR-AGT (agent), SEC-AUD (audit), PRIV-ACC (access and correction).

`*` in the AT column marks a proposed additional acceptance test (§11).

### FR-ONB: Onboarding

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-ONB-001 | Onboarding collects information progressively in small groups of related questions. | §4 | AT-ONB-001 |
| FR-ONB-002 | Information already available and valid is never requested again. | §4 | AT-ONB-001 |
| FR-ONB-003 | A Definition-of-Ready check gates full plan creation on the REQUIRED inputs of §9. | §9 | AT-ONB-002* |
| FR-ONB-004 | Missing PREFERRED inputs yield only a PROVISIONAL plan with explicit assumptions. | §9 | AT-ONB-003* |
| FR-ONB-005 | Insufficient history enters BASELINE_COLLECTION with conservative assessment sessions; thresholds are never invented. | §9 | AT-ONB-004* |
| FR-ONB-006 | Coaching state machine covering every §66 state, including SAFETY_HOLD, DATA_INCOMPLETE, INTEGRATION_ERROR, GOAL_REASSESSMENT and RECOVERY_BLOCK, with testable transitions. | §66 | AT-STM-001* |

### FR-PRF: Profile, experience, availability

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-PRF-001 | AthleteProfile with identity, physiology, HR settings, preferences, devices and sensors. | §5 | contract |
| FR-PRF-002 | Unknown values are stored as explicit UNKNOWN. Athlete data is never invented. | §5, §23 | contract |
| FR-PRF-003 | Experience is stored independently for running, cycling, swimming, strength and tennis. | §6 | contract |
| FR-PRF-004 | Recurring availability and resources (pool, bicycle, trainer, gym, equipment, tennis schedule). | §7 | unit |
| FR-PRF-005 | AvailabilityException with precedence: exception > recurring > default. | §7 | AT-AVL-001* |
| FR-PRF-006 | Declared HealthConstraints are captured and consumed by the policy engine. | §9, §49 | unit |
| FR-PRF-007 | Preferred language for athlete-facing text (added, see A-14). | none | unit |

### FR-GOL: Goals and events

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-GOL-001 | SportEvent and Goal are separate entities. A Goal optionally references an event. | §8 | contract |
| FR-GOL-002 | Goal priority A (primary), B (secondary), C (development/recreational). | §8 | contract |
| FR-GOL-003 | Feasibility is evaluated as current state, then gap, available time, required progression, feasibility. | §8 | AT-GOL-001* |
| FR-GOL-004 | Where appropriate, desired, realistic and minimum-satisfactory goals are presented. | §8 | agent-eval |
| FR-GOL-005 | Outcomes are never guaranteed. | §8, §10 | agent-eval |

### FR-ACT: Activities and data model

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-ACT-001 | Canonical, provider-independent Activity with schemaVersion. | §18 | contract |
| FR-ACT-002 | RAW (immutable), NORMALIZED and DERIVED layers. | §17 | AT-RAW-001 |
| FR-ACT-003 | UTC persistence plus original local time, source timezone and UTC offset. No ambiguous timestamps. | §19 | AT-TIM-001* |
| FR-ACT-004 | Training day = local date at activity start unless configured otherwise. | §19 | AT-TIM-001* |
| FR-ACT-005 | Conflicts resolved per field. Every resolved field keeps its provenance. | §20 | unit |
| FR-ACT-006 | DataOverride corrections are traceable and never destroy original values. | §21 | AT-RAW-001 |
| FR-ACT-007 | Deduplication on athleteId, provider, providerActivityId, sport, start, duration, distance, file hash. Imports are idempotent. | §22 | AT-ACT-001 |
| FR-ACT-008 | Enrichment never duplicates distance, duration, training load or weekly volume. | §22 | AT-ACT-001 |
| FR-ACT-009 | DataQuality: completeness, reliability, source, anomalies, confidence HIGH/MEDIUM/LOW. | §23 | unit |
| FR-ACT-010 | Uncertainty reasons: MISSING_DATA, SENSOR_QUALITY, NON_COMPARABLE_SESSION, ENVIRONMENT, INSUFFICIENT_HISTORY, ESTIMATED_VALUE. | §24 | contract |
| FR-ACT-011 | Activities without a matching planned workout are UNPLANNED and still count toward load, interference, recovery, analytics and adaptation. | §47 | AT-UNP-001* |

### FR-MET: Metrics and load

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-MET-001 | Deterministic sport calculations run in application code, never in the LLM. | §25 | AT-MET-001 |
| FR-MET-002 | MetricDefinition registry: id, name, sport, unit, method, required inputs, minimum quality, version, origin, reference. | §26 | contract |
| FR-MET-003 | Metric origin is RAW_PROVIDER, PROVIDER_DERIVED, STANDARD_CALCULATION or APPLICATION_DERIVED, and is never mislabelled. | §26 | AT-MET-002* |
| FR-MET-004 | Each derived value keeps method, calculation version, source inputs and minimum data quality. | §25 | AT-MET-001 |
| FR-MET-005 | External and internal load are modelled separately. | §27 | unit |
| FR-MET-006 | Cross-sport load preserves sport context. Distance is never equated across sports. | §27 | unit |
| FR-MET-007 | Proprietary metrics from different providers are not assumed equivalent. | §26 | unit |

### FR-RUN / FR-CYC / FR-SWM: Endurance analytics

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-RUN-001 | Running analytics use only available fields (distance, duration, pace, splits, HR, zones, cadence, stride, power, elevation, vertical oscillation, ground contact time, RPE). | §28 | unit |
| FR-RUN-002 | Detect pace/HR changes and HR drift. | §28 | unit |
| FR-RUN-003 | Detect pacing instability and interval degradation. | §28 | unit |
| FR-RUN-004 | Detect cadence, efficiency and fatigue changes. | §28 | unit |
| FR-CYC-001 | Cycling analytics over available fields. | §29 | unit |
| FR-CYC-002 | Measured and estimated power are always distinguished. Measured power is never assumed without a reliable power meter. | §29 | AT-CYC-001 |
| FR-CYC-003 | FTP is used only when available, with provenance. | §29 | unit |
| FR-CYC-004 | Power distribution and fatigue analysis only with measured power. | §29 | unit |
| FR-SWM-001 | Pool and open-water sessions are always distinguished. | §30 | AT-SWM-001* |
| FR-SWM-002 | Pool metrics: laps, rests, stroke count, stroke rate, distance/stroke, stroke type, SWOLF only where available and valid. | §30 | unit |
| FR-SWM-003 | Consistency, efficiency, pace degradation and technical fatigue. | §30 | unit |
| FR-SWM-004 | Pool vs open-water comparison requires explicit context. | §30 | AT-SWM-001* |

### FR-STR: Strength

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-STR-001 | Strength is first-class and mandatory in planning unless contraindicated or impossible. | §31 | AT-STR-002* |
| FR-STR-002 | Controlled vocabularies for movement patterns and muscle groups. | §31 | contract |
| FR-STR-003 | Exercise catalogue with canonical ids, pattern, muscles, equipment, unilateral flag, difficulty, contraindication tags, substitutions, display aliases. | §32 | contract |
| FR-STR-004 | Prescriptions explain WHAT, WHY, sets, reps, load/RPE/RIR, rest, target muscles, progression, interference. | §33 | contract |
| FR-STR-005 | StrengthSetResult captures actual execution. | §34 | contract |
| FR-STR-006 | Progression decisions use execution history, not only planned workouts. | §34 | unit |
| FR-STR-007 | Interference engine evaluates cross-modality interference with rules plus athlete context. | §35 | AT-STR-001 |
| FR-STR-008 | Strength volume reported by muscle group. | §51 | unit |

### FR-TEN: Tennis

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-TEN-001 | Tennis: recreational = true, countsAsTrainingLoad = true. | §36 | AT-TEN-001 |
| FR-TEN-002 | Capture duration, HR, RPE, match/practice, court surface, soreness, pain. | §36 | contract |
| FR-TEN-003 | Tennis loading of calves, soleus, Achilles region, knees, adductors, shoulders and elbows feeds interference and recovery. | §36 | AT-TEN-001 |

### FR-REC: Check-in and recovery

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-REC-001 | DailyCheckIn with an explicitly documented scale. | §37 | contract |
| FR-REC-002 | Wearable metrics never automatically override athlete-reported condition. | §37 | AT-REC-002* |
| FR-REC-003 | Recovery assessment combines trend, multiple signals, subjective feedback and performance. No major decision from one reading. | §38 | AT-REC-001* |
| FR-REC-004 | RecoveryPrescription (REST, ACTIVE_RECOVERY, EASY_SESSION, REDUCED_VOLUME) with reason, duration, reevaluateAfter. | §39 | contract |
| FR-REC-005 | Recovery inputs are processed only with the corresponding consent. | §38, §56 | AT-CON-001* |

### FR-INS: Patterns and insights

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-INS-001 | Insight separates observation, interpretation, hypothesis and recommendation, with evidence, confidence and uncertainty. | §41 | contract |
| FR-INS-002 | Pattern detection covers the patterns listed in §40. | §40 | unit |
| FR-INS-003 | Correlation is never presented as causation. | §41 | agent-eval |

### FR-PLN / FR-ADP: Planning and adaptation

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-PLN-001 | Phases ADAPTATION, BASE, BUILD, SPECIFIC, TAPER, COMPETITION, RECOVERY, TRANSITION. | §42 | contract |
| FR-PLN-002 | Plans are immutable versions (planId, version, parentVersion, status, schemaVersion). Adaptation creates a new version. | §43 | AT-PLN-001 |
| FR-PLN-003 | At most one ACTIVE version per plan. Activation supersedes the previous ACTIVE version atomically. | §43 | AT-PLN-002* |
| FR-PLN-004 | TrainingWeek with days, key workouts, strength, tennis, recovery days, monitoring metrics, adaptation rules. | §44 | contract |
| FR-PLN-005 | EnduranceWorkout intensity uses only dimensions supported by reliable athlete data. | §45 | AT-CYC-001 |
| FR-PLN-006 | No universal percentage progression rule. Progression uses athlete context. | §42 | unit |
| FR-PLN-007 | WorkoutExecution links planned workouts to activities: COMPLETE, PARTIAL, SKIPPED, REPLACED, UNPLANNED, with compliance on duration, distance, intensity, structure. | §46 | unit |
| FR-ADP-001 | Adaptive loop: planned, actual, difference, recovery, interpretation, decision. | §48 | unit |
| FR-ADP-002 | Decisions: MAINTAIN, PROGRESS, REDUCE, RESCHEDULE, REPLACE, REST. | §48 | contract |
| FR-ADP-003 | One isolated poor workout without supporting evidence never triggers plan reconstruction. | §48 | AT-ADP-001 |
| FR-ADP-004 | Load reductions and plan changes carry evidence, reason, uncertainty and confidence. | §48, §75 | AT-EXP-001 |

### FR-POL / FR-RPT / FR-AGT

| ID | Requirement | Spec | AT |
|---|---|---|---|
| FR-POL-001 | A deterministic policy engine evaluates equipment, calendar, consent, SAFETY_HOLD, maximum duration, provider capability, confirmation, authorization and ownership. | §49 | AT-POL-001* |
| FR-POL-002 | AI recommendations cannot bypass policy decisions. | §49 | AT-POL-001* |
| FR-RPT-001 | Post-activity report. | §51 | contract |
| FR-RPT-002 | Weekly report. | §51 | contract |
| FR-RPT-003 | Monthly report with a cautious forecast. | §51 | contract |
| FR-AGT-001 | The agent uses only the narrow typed tools of §65. Unrestricted tools are prohibited. | §65 | contract |
| FR-AGT-002 | The acting athlete is bound from the authenticated session, never taken from tool arguments. | §59, §65 | AT-IDOR-002* |
| FR-AGT-003 | Significant decisions are stored as AgentDecision (ids, prompt/spec versions, model id, evidence refs, action, explanation). No chain-of-thought is stored. | §54 | contract |
| FR-AGT-004 | Agent output contains no fabricated metrics or tool results. Every number traces to a tool result. | §73 | agent-eval |

### INT-GAR / INT-FIL: Integrations

| ID | Requirement | Spec | AT |
|---|---|---|---|
| INT-GAR-001 | FitnessProvider interface: capabilities, activities, details, recovery, profile, sync. | §11 | contract |
| INT-GAR-002 | WorkoutPublisher interface separating propose() from publish(). | §11, §50 | contract |
| INT-GAR-003 | GarminCapabilities (accessTier plus capability flags). Features are gated by capability. | §12 | AT-GAR-001 |
| INT-GAR-004 | Capabilities are configured or observed, never assumed. UNKNOWN never enables a write. | §12 | AT-GAR-001 |
| INT-GAR-005 | Personal-tier adapter: pull-only, read-only, own account. | §12 | contract |
| INT-GAR-006 | Credential handling is confined to the integration and secret layers. Access tokens live in memory only. | §13 | AT-SEC-002* |
| INT-GAR-007 | Sync is incremental, resumable and idempotent. | §63 | integration |
| INT-GAR-008 | Provider failure degrades gracefully: local history stays usable, freshness is reported, sync is marked pending. | §67 | AT-DEG-001* |
| INT-GAR-009 | Provider payloads are persisted unmodified in the RAW layer. | §17 | integration |
| INT-GAR-010 | Client-side throttling and backoff honouring Retry-After; limits configurable (provider limits are undocumented). | §75 | unit |
| INT-FIL-001 | Ingest FIT, TCX, GPX and CSV. | §15 | integration |
| INT-FIL-002 | Pipeline order: extension, MIME, size, malware, quarantine, SHA-256, safe parser, schema, semantic, normalisation, deduplication, persistence. | §15 | integration |
| INT-FIL-003 | Configurable limits: file size, records, activities per import, parser timeout, memory. | §15 | AT-UPL-001* |
| INT-FIL-004 | Parse failures are recorded as data-quality issues, with no partial persistence. | §75 | integration |

### SEC: Security

| ID | Requirement | Spec | AT |
|---|---|---|---|
| SEC-AUTH-001 | Authentication is required for all private athlete data, including on localhost. | §59 | security |
| SEC-AUTH-002 | The local API binds to loopback, validates the Host header and rejects cross-origin requests (CSRF and DNS-rebinding defence). | §61 | security |
| SEC-AUTH-003 | Rate limiting on authentication and API endpoints. | §61 | security |
| SEC-AUTHZ-001 | The chain user, athlete, ownership, resource is enforced in the application layer for every operation. | §59 | AT-IDOR-001 |
| SEC-AUTHZ-002 | Agent tool permissions follow least privilege; write tools are separate from read tools. | §65 | contract |
| SEC-AUTHZ-003 | Commands accept explicit allow-listed fields only (mass-assignment prevention). | §62 | security |
| SEC-IDOR-001 | Repositories expose no unscoped get-by-id. Every query is owner-scoped. Foreign resources return not-found. | §59 | AT-IDOR-001 |
| SEC-IDOR-002 | Non-enumerable identifiers (UUIDv7) as defence in depth only, never as the control. | §59 | unit |
| SEC-SEC-001 | Secrets are resolved only through a CredentialProvider backed by an OS or managed secret store. Never hardcoded. | §13 | security |
| SEC-SEC-002 | The LLM never receives credential values. | §13 | AT-SEC-002* |
| SEC-SEC-003 | Secrets never appear in logs, URLs, plaintext DB fields, errors, telemetry, prompts or knowledge sources. | §61 | AT-SEC-002* |
| SEC-SEC-004 | Credential rotation supported. Credential expiry is tracked and surfaced. | §61 | unit |
| SEC-SEC-005 | Accidentally disclosed secrets are detected before persistence and before LLM submission, never repeated, and rotation is recommended. | §14 | AT-SEC-001 |
| SEC-SEC-006 | Automated secret scanning on commit. | §61 | CI |
| SEC-INJ-001 | All external content is untrusted data and reaches the LLM only as delimited, typed data. | §60 | AT-INJ-001 |
| SEC-INJ-002 | Data cannot change tool permissions, policies or agent instructions. | §60 | AT-INJ-001 |
| SEC-INJ-003 | Parameterised queries only. | §61 | security |
| SEC-INJ-004 | CSV export neutralises formula-capable prefixes (=, +, -, @). | §16 | AT-CSV-001* |
| SEC-UPL-001 | Extension and content-signature (magic bytes) validation. | §15 | security |
| SEC-UPL-002 | XML parsing with DTD, external entities and network resolution disabled. | §16 | AT-XXE-001* |
| SEC-UPL-003 | Archive handling resists zip bombs, path traversal and unexpected members. | §15 | AT-ZIP-001* |
| SEC-UPL-004 | Parsers run with timeout and resource limits and fail safely. | §15 | security |
| SEC-UPL-005 | Uploaded filenames never determine storage paths; files are stored by content hash. | §15 | security |
| SEC-UPL-006 | Malware-scan hook. Without an available scanner the import is flagged, not silently trusted. | §15 | integration |
| SEC-AUD-001 | Append-only AuditEvent for security, consent, deletion, plan activation, overrides and external writes. | §75 | integration |

### PRIV / SAFE

| ID | Requirement | Spec | AT |
|---|---|---|---|
| PRIV-CON-001 | ConsentRecord: consentType, granted, grantedAt, revokedAt, policyVersion, purpose. | §56 | contract |
| PRIV-CON-002 | Processing that requires consent is blocked when consent is missing or revoked. | §56 | AT-CON-001* |
| PRIV-CON-003 | Consent category for sending data to an external AI provider (added: privacy by design). | §57 | AT-CON-001* |
| PRIV-CON-004 | Data minimisation: Women's Health, nutrition and other unnecessary sensitive data are not ingested by default. | §57 | unit |
| PRIV-ACC-001 | The athlete can inspect and export their data. | §57 | integration |
| PRIV-ACC-002 | The athlete can correct data (through DataOverride). | §57 | AT-RAW-001 |
| PRIV-DEL-001 | The deletion workflow covers every store listed in §58, including raw files, derived data and credentials. | §58 | AT-DEL-001* |
| PRIV-DEL-002 | Provider access is revoked where supported. Otherwise the athlete is told how (a Garmin Personal key is deleted in Garmin Connect). | §58 | integration |
| PRIV-DEL-003 | Retained audit information is minimised. | §58 | unit |
| SAFE-MED-001 | No diagnosis, medication or clinical rehabilitation. | §10 | agent-eval |
| SAFE-MED-002 | Declared red-flag symptoms activate SAFETY_HOLD. | §10 | AT-SAFE-001 |
| SAFE-MED-003 | SAFETY_HOLD is enforced by the policy engine, not only by the prompt. Intensive recommendations are blocked. | §10, §49 | AT-SAFE-001 |
| SAFE-MED-004 | The safety reason is communicated and healthcare evaluation recommended. Dangerous symptoms are never minimised. | §10 | agent-eval |
| SAFE-MED-005 | Leaving SAFETY_HOLD requires explicit athlete action and is audited (see A-13). | none | unit |

### NFR

| ID | Requirement | Spec | AT |
|---|---|---|---|
| NFR-REL-001 | Idempotent ingestion and commands (Idempotency-Key or deterministic keys). | §63 | AT-ACT-001 |
| NFR-REL-002 | Explicit retry strategy. No silent data loss. | §75 | integration |
| NFR-REL-003 | Optimistic concurrency on AthleteProfile, Consent, Activity enrichment, TrainingPlan and overrides. | §64 | AT-CONC-001* |
| NFR-REL-004 | Persisted contracts carry schemaVersion. Migrations are explicit; no automatic destructive migration. | §55, §81 | contract |
| NFR-REL-005 | Backup and restore of the local data store (added). | none | integration |
| NFR-OBS-001 | Structured events for integration, imports, parsing, dedup, quality, plan generation and activation, external writes, consent, deletion and security. | §75 | integration |
| NFR-OBS-002 | Log and telemetry redaction. Secrets are never logged. | §75 | AT-SEC-002* |
| NFR-EXP-001 | Significant plan modifications include evidence, interpretation, decision, uncertainty and confidence. | §75 | AT-EXP-001 |
| NFR-TRC-001 | Lineage Recommendation, Insight, Metric, Normalized, Raw, Source is queryable. | §53 | AT-LIN-001* |
| NFR-TRC-002 | Tests declare requirement IDs; a traceability matrix is generated from them. | §72 | CI |

---

## 4. Proposed architecture

### 4.1 Stack (recommendation; decision D-2)

**Python >= 3.12**, developed on the installed 3.14 with uv.

| Need | Choice | Reason |
|---|---|---|
| FIT decoding | `garmin-fit-sdk` (official Garmin FIT Python SDK) | Provider-maintained decoder, no reverse-engineered parsing |
| Garmin Personal API | `garmin-connect-personal-api-python` (official, Apache-2.0, Python >= 3.12), used as data transport only | Endpoints are not hand-written or guessed |
| Hardened XML | `defusedxml` | XXE and entity-expansion defence for TCX/GPX |
| Contracts | Pydantic v2 | Typed models; JSON Schema export for contract tests against `spec/contracts` |
| Analytics | NumPy (pandas only if needed) | Deterministic numeric work |
| HTTP API | FastAPI | Typed request models, OpenAPI |
| Persistence | SQLite (WAL) via SQLAlchemy 2 and Alembic | Zero-ops local store; portable to PostgreSQL |
| Secrets | `keyring` with the Windows Credential Manager backend | OS-protected per-user store |
| Architecture rules | import-linter | Dependency direction enforced as a test |
| Quality | ruff, mypy (strict), pytest, hypothesis, pip-audit, gitleaks | Lint, types, tests, property tests, supply chain, secret scanning |
| LLM | Anthropic SDK behind an `LLMProvider` port | Provider abstraction |

Alternatives considered:

- **.NET 8** (installed; Garmin publishes a C# FIT SDK): strong typing, but no official Garmin Personal API client and a thinner analytics ecosystem.
- **TypeScript/Node**: good for a future UI; not chosen for the core. A TypeScript web UI remains compatible because it would consume the HTTP API.

### 4.2 Deployment topology (V1)

Local-first on a single host (the athlete's Windows PC):

- One process: HTTP API bound to loopback, background sync worker, CLI.
- Data directory outside any synced folder (default `%LOCALAPPDATA%\CoachDeportivo\`).
- Outbound HTTPS only: Garmin (pull) and the LLM provider.
- No inbound internet surface. The Personal tier is pull-only, so there are no webhooks.

Cloud hosting (for example Azure Container Apps with Key Vault and Entra ID) remains possible later by swapping infrastructure adapters. Domain and application layers stay cloud-neutral (§68). No cloud service is introduced in V1 without a stated reason.

### 4.3 Layers and dependency rule

```
interfaces      api (FastAPI) | cli | agent (AI coach: typed tools, prompt, LLM adapter)
     |
application     use cases | authorization | unit of work | ports:
     |            Repository, FitnessProvider, WorkoutPublisher, CredentialProvider,
     |            BlobStore, Clock, LLMProvider, MalwareScanner, AuditSink
     |
policy          deterministic policy engine (final gate on every proposal)
domain          entities | value objects | domain services
                metrics engine | planning engine | interference | recovery | patterns
                (pure Python: no I/O, no framework imports)

infrastructure  SQLite repositories | blob store | keyring credentials | logging + redaction
integrations    garmin: adapter, capabilities, token broker, sync
                files: FIT, TCX, GPX, CSV parsers, ingestion pipeline, parser sandbox
   -> implement application ports; never imported by domain, policy or application
```

Package layout (Python src layout, an equivalent adaptation of spec §76):

```
src/coach/
  domain/  metrics/  planning/  policy/  application/
  integrations/garmin/  integrations/files/
  infrastructure/  agent/  api/  cli/
tests/
  unit/  integration/  contract/  security/  agent_evals/
```

### 4.4 Agent boundary

- Each agent tool maps 1:1 to an application use case. Tool schemas never contain `athleteId`; the athlete is bound from the authenticated session (FR-AGT-002). Even a successful prompt injection cannot address another athlete's data.
- Inbound athlete messages pass through deterministic filters **before** persistence and before the LLM: secret detector and redaction (SEC-SEC-005), then red-flag detector (SAFE-MED-002). Red-flag detection is fail-safe: a deterministic hit or an LLM flag activates SAFETY_HOLD.
- External content (activity names, descriptions, file metadata) reaches the LLM only as typed data blocks marked untrusted.
- Plans, workouts and adaptations proposed by the AI go through the policy engine and are stored as PROPOSED. External writes require a separate confirmed EXECUTE step.
- UI decision deferred to P13: in-app chat (own agent loop) or an MCP server consumed by a host such as Claude Desktop. In an MCP-host setup the athlete's message reaches the LLM before this application sees it, so pre-LLM secret redaction (AT-SEC-001) and ingress safety filtering cannot be guaranteed. The in-app loop is therefore recommended as the primary UI.

### 4.5 ADRs

Mandated ADR-001 to ADR-012 are written in P1. This report adds:

| ADR | Decision |
|---|---|
| ADR-013 | Python stack (pending D-2) |
| ADR-014 | Local-first deployment; SQLite; data directory outside synced folders |
| ADR-015 | Garmin Personal tier: own token broker, in-memory tokens, official client as data transport only |
| ADR-016 | Owner-scoped repositories: no unscoped get-by-id exists |
| ADR-017 | Policy engine is the final gate for every AI proposal |
| ADR-018 | LLM provider abstraction and explicit AI-processing consent |
| ADR-019 | Parser sandboxing: subprocess with timeout and resource limits |

---

## 5. Domain model

| Context | Aggregate root | Contains | Invariants / notes | Concurrency |
|---|---|---|---|---|
| Identity | User | AthleteLink | Authenticated principal is distinct from Athlete. V1 is 1:1; model allows N. | version |
| Athlete | AthleteProfile | physiology, HR settings, preferences, devices, sensors, SportExperience x5, resources/equipment, HealthConstraints | UNKNOWN explicit. Per-field provenance (athlete-declared vs provider). | optimistic |
| Athlete | Availability | weekly pattern, time windows, AvailabilityExceptions | Precedence: exception > recurring > default. | optimistic |
| Consent | ConsentLedger | ConsentRecords (append-only) | Current state = latest record per type. Read by the policy engine. | optimistic |
| Goals | SportEvent | | Independent of goals. | optimistic |
| Goals | Goal | baseline snapshot, feasibility assessments | Priority A/B/C. Optional eventId. | optimistic |
| Activity | RawSource | blob ref, sha256, origin metadata | Immutable (write-once). | none |
| Activity | Activity (logical) | source links, resolved fields with provenance, laps/lengths, stream refs, subjective data, quality | One logical activity per real session. Volume counted once. | optimistic |
| Activity | DataOverride | | Append-only. Effective value = override over normalized value. | none |
| Activity | DataQualityIssue | | Raised by ingestion, dedup and metrics. | version |
| Strength | Exercise (catalogue) | aliases, substitutions | Stable canonical id. Reference data. | n/a |
| Strength | StrengthSession | StrengthSetResults | Links to an Activity (if recorded) and a PlannedWorkout (if planned). | optimistic |
| Recovery | DailyRecovery | provider metrics per training day | Consent-gated per category. | none |
| Recovery | DailyCheckIn | | Documented scale. | none |
| Recovery | RecoveryPrescription | | Workload management, not medical treatment. | none |
| Metrics | MetricDefinition | | Defined in code, versioned. | n/a |
| Metrics | DerivedMetricValue | input refs | Append-only. Superseded, never updated. | none |
| Insights | PerformanceInsight | evidence refs | Observation, interpretation, hypothesis, recommendation kept separate. | none |
| Planning | TrainingPlan (version) | TrainingWeeks with PlannedWorkouts (Endurance, Strength, Tennis, Recovery, Evaluation) | Content immutable, with content hash. Status changes via transition records. At most one ACTIVE. | optimistic on transitions |
| Planning | WorkoutExecution | compliance, athlete feedback | Links planned and completed. `CompletedWorkout` is a read model over WorkoutExecution plus Activities. | optimistic |
| Coaching | CoachingState | current state, hold reasons | §66 state machine. | optimistic |
| Coaching | AgentDecision, AdaptationDecision | evidence refs | Append-only. | none |
| Integration | ProviderConnection, IntegrationStatus | capability snapshot, secret **handle** (never the secret), sync cursor, key expiry | | optimistic |
| Cross-cutting | AuditEvent | | Append-only. | none |

Shared value objects: `FieldValue<T>` (value or UNKNOWN, unit, provenance, quality), `Quantity` (canonical units), `Instant` (UTC) with `LocalContext` (local time, IANA timezone, offset), `TrainingDay`, `Provenance`, `Confidence`, `UncertaintyReason`, `EvidenceRef`, `SchemaVersion`.

Boundary rules:

- Every non-reference aggregate carries `athleteId`, the ownership root.
- The domain contains no Garmin types. Garmin specifics live in `integrations/garmin` mapping tables.
- Aggregates reference each other by id only.

---

## 6. Data architecture

### 6.1 Layers

```
PROVIDER / FILE
   |  bytes as received
RAW          raw_source metadata + content-addressed blob (sha256), write-once
   |  normalizer vN: deterministic, versioned, re-runnable from RAW
NORMALIZED   canonical entities, canonical units, field-level provenance
   |  + DataOverride, applied at read time (normalized row untouched)
EFFECTIVE    view = normalized + overrides
   |  metrics engine vN
DERIVED      derived_metric_value: append-only; method, version, inputs, quality
   |
INSIGHT  ->  RECOMMENDATION / DECISION   (evidence refs down the chain)
```

### 6.2 RAW

- Blobs stored at `<data_dir>/raw/<sha256[0:2]>/<sha256>`, read-only after write. Uploads sit in `<data_dir>/quarantine/` until validation passes.
- `raw_source`: id, athleteId, origin (FILE_UPLOAD, GARMIN_PERSONAL_API, MANUAL_ENTRY), provider object id, endpoint category, receivedAt, sha256, byte size, detected type, sanitised original name (kept as data only), ingestion status.
- Garmin JSON responses are stored byte-exact as received.
- The only exception to immutability is privacy deletion (PRIV-DEL-001), which is audited. Privacy outranks immutability under the spec's precedence rules.

### 6.3 NORMALIZED

- Canonical units: metres, seconds, m/s, bpm, watts, rpm/spm, degrees Celsius, kg. Pace is a presentation of speed.
- Per-second streams are stored per activity as a compressed columnar blob. Format decided in the P5 spec.
- Each resolved field stores value or UNKNOWN, unit, source ref (raw_source id plus field path) and the resolution rule id.

### 6.4 Field-level resolution defaults (§20)

| Field group | Preferred source | Fallback |
|---|---|---|
| providerActivityId | Provider API | none |
| Sensor streams, laps, lengths | FIT | Provider JSON summary |
| Summary totals (distance, duration) | FIT session message | Provider summary. One value is chosen; values are never summed. |
| Subjective (RPE, soreness, pain, comments) | Athlete | None. Never inferred. |
| Recovery (sleep, HRV, Body Battery) | Health provider | None |
| Any field with a DataOverride | Override | none |

### 6.5 Deduplication and identity

1. Same sha256: same raw source, idempotent no-op that returns the existing record.
2. Same provider and providerActivityId: same logical activity.
3. Fuzzy candidate: same athlete, compatible sport, start times within tolerance, duration and distance within tolerance. Scored. Above threshold the new source is linked as enrichment. Ambiguous matches raise a DataQualityIssue for athlete confirmation and are never auto-merged.

Aggregations run over logical Activities only, so volume is counted once (AT-ACT-001). Tolerances are configured, versioned and property-tested; initial values are set in the P5 spec.

### 6.6 Time

- Persist UTC. Keep original local time, original IANA timezone and UTC offset.
- FIT: offset from the activity `local_timestamp` when present. GPX and TCX usually carry no timezone, so the timezone is UNKNOWN; the athlete's profile timezone is used and flagged ESTIMATED_VALUE.
- Training day = local date at start in the activity's own timezone (A-07).

### 6.7 Lineage

Each DerivedMetricValue stores input refs (normalized entity ids, field names, raw_source ids). Insights store EvidenceRefs to derived values. AgentDecision and AdaptationDecision store insight refs. `explain_lineage(id)` walks the chain to the provider or file. Derived values are append-only, so the lineage behind a past decision stays stable after later corrections.

### 6.8 Storage and lifecycle

- SQLite (WAL) and the blob directory live under `%LOCALAPPDATA%\CoachDeportivo\` (configurable), outside OneDrive.
- Encryption at rest: OS full-disk encryption (BitLocker) in V1 (A-10).
- Explicit backup/export command (NFR-REL-005).
- Schema evolution with Alembic, backward-compatible first. Destructive migration requires human approval (§81).

---

## 7. Garmin integration strategy

### 7.1 Verified facts (sources at the end; checked 2026-10-24)

Garmin restructured the Connect Developer Program around 2026-10-22:

| | Personal | Starter (business) | Enterprise (business) |
|---|---|---|---|
| Eligibility | Individuals, own data only. "Not intended for commercial applications serving multiple end users." | Organisations; application and approval | Via Developer Portal; tailored commercial terms |
| Activity | JSON & .FIT | JSON & .FIT | JSON & .FIT |
| Health, Women's Health | JSON | JSON | JSON |
| Training (workouts) | **Not included** | Yes | Yes |
| Courses | **Not included** | Yes | Yes |
| Delivery | Pull only | Push only | Push or ping/pull |
| Historical data | Not stated | Not included | 1 month + |
| Users | Own account | Limited | Unlimited |

Personal-tier authentication (Garmin Personal API FAQ):

- The athlete creates an API key in Garmin Connect Web (Settings, Personal API Key). Expiry: 30, 90 or 180 days, or 1 year. Shown once; Garmin says to protect it "like a password".
- The key is exchanged for an access token: `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token` with header `Authorization: ApiKey <key>`.
- The access token is valid 24 hours (or until key expiry if sooner). **No refresh tokens**: request a new token instead.
- Deleting the key revokes it and every token derived from it.

Official client `garmin/connect-personal-api-python` (Apache-2.0, Python >= 3.12) documents read-only GET wrappers for: activity search (offset pagination, optional date range and type), activity details, activity workouts, activity file download (ZIP), daily summaries, sleep, HRV, Body Battery, stress, heart rate, HR zones, SpO2, steps, weight, profile settings, and more. It has no write endpoints.

Its authenticator caches the bearer token in a plaintext file under `~/.config/garmin/`. The cache "can't be disabled", and its 0600 permission only applies on POSIX. The data client accepts a token string directly, so the authenticator (and its cache) can be bypassed.

Third-party report (Runalyze, cited by the5krunner): Garmin strips undocumented fields (for example VO2 max, recovery time, performance condition) from API-delivered FIT files. Garmin has not confirmed whether this applies to the Personal tier.

### 7.2 Capability configuration for V1 (Personal tier)

Each capability carries a basis: CONFIGURED (documented), OBSERVED (verified at runtime) or UNKNOWN. A write needs SUPPORTED; UNKNOWN never enables a write.

| Capability | Value | Basis |
|---|---|---|
| accessTier | PERSONAL | To confirm (A-04) |
| activityRead | SUPPORTED | CONFIGURED: Garmin overview |
| healthRead | SUPPORTED | CONFIGURED: Garmin overview |
| fitDownload | SUPPORTED, verify on first sync | CONFIGURED: overview lists ".FIT"; client returns a ZIP |
| pullSupport | SUPPORTED | CONFIGURED: "pull only" |
| pushSupport | UNSUPPORTED | CONFIGURED: "pull only" |
| historicalData | UNKNOWN until observed | Not stated by Garmin |
| trainingPublish | UNSUPPORTED | CONFIGURED: not in Personal tier |
| coursesPublish | UNSUPPORTED | CONFIGURED: not in Personal tier |

### 7.3 Adapter design

```
GarminPersonalProvider            implements FitnessProvider
 |- CapabilityConfig              tier -> capabilities; snapshot stored in IntegrationStatus
 |- TokenBroker                   key from CredentialProvider -> POST token endpoint ->
 |                                JWT kept in memory, re-exchanged before expiry;
 |                                never written to disk, logs or errors
 |- Transport                     official client for data GETs with our token
 |                                (its authenticator and cache unused); own thin transport
 |                                if response-size caps are needed (ZIP is held in memory)
 |- Mappers                       Garmin JSON/FIT -> canonical; activity-type mapping table
 |                                built from observed values
 '- SyncService                   windowed pull, resumable cursor, idempotent via RAW hash
                                  and providerActivityId

WorkoutPublisher -> LocalWorkoutPublisher (V1): structured workout generated locally;
                    publish() refused by the capability gate (AT-GAR-001)
```

### 7.4 Degradation

Garmin unreachable, key expired or key revoked: IntegrationStatus becomes INTEGRATION_ERROR with a reason. Local history and file ingestion keep working, freshness is shown, and no data is fabricated. Key expiry is tracked: the athlete enters the expiry date when storing the key and is reminded before it lapses.

### 7.5 Unresolved (resolved at P7 from authoritative sources, never guessed)

| ID | Open question | Resolution path |
|---|---|---|
| G-1 | Data API base URL and authorization header for data calls | Official client source and Swagger docs |
| G-2 | Response schemas | Swagger, plus real responses captured by the athlete as redacted fixtures |
| G-3 | Historical depth, rate limits, maximum page size, maximum date range | Not documented; discover conservatively |
| G-4 | Contents of the activity download ZIP (FIT assumed) | Verify on first sync |
| G-5 | Personal API Terms and Conditions (accepted at key creation, not public): is processing Garmin data with a third-party AI permitted? | The athlete reviews. Blocks only P13 use of Garmin-sourced data by the LLM. |
| G-6 | Garmin activity types to canonical sport (including tennis, pool vs open-water swim) | Mapping table from observed values |
| G-7 | Semantics of proprietary health metrics (Body Battery, HRV status, stress) | Treated as PROVIDER_DERIVED; never equated with other providers |

The business tiers (OAuth 2.0 per third-party reports; PKCE unconfirmed) are not targeted in V1. The FitnessProvider abstraction keeps them addable.

---

## 8. Security review: immediate risks

| ID | Risk | Severity | Recommendation | Req |
|---|---|---|---|---|
| R-01 | The repository, and by default any future data and secrets, sit in OneDrive synced to the employer's M365 tenant. Athlete health data (HR, HRV, sleep, health constraints) would leave the athlete's control; tenant retention or eDiscovery can defeat deletion. `.git` and `.venv` also suffer under sync. | HIGH | Keep data, raw files, DB, secrets and token material outside any synced folder. Preferably move the repository too, to a non-synced path with a private remote. | PRIV-DEL-001, SEC-SEC-003 |
| R-02 | No VCS: no history, no pre-commit secret scanning. | MEDIUM | `git init` with a `.gitignore` covering data, `.env`, `*.fit`, `*.zip`; gitleaks before the first code commit. | SEC-SEC-006 |
| R-03 | The Garmin Personal API key is a credential for all of the athlete's health data, valid up to one year and displayed once. It may be pasted into chat or a `.env`. | HIGH | Store via keyring (Windows Credential Manager) using a hidden-input CLI command; shortest practical expiry (90 days) with a reminder; secret detector on chat input. | SEC-SEC-001/004/005 |
| R-04 | The official client's token cache writes a plaintext JWT to disk on Windows. | MEDIUM | Do not use its authenticator; own in-memory token broker. | INT-GAR-006 |
| R-05 | The AI coach sends health data to an external LLM provider. | MEDIUM | Explicit consent purpose; minimisation (derived summaries, not raw streams); no credentials; review provider data-retention settings; Garmin T&C check (G-5). | PRIV-CON-003 |
| R-06 | A localhost API is reachable by any local process and by browsers (CSRF, DNS rebinding). | MEDIUM | Loopback bind, Host allow-list, authentication, no permissive CORS, SameSite cookies. | SEC-AUTH-002 |
| R-07 | Untrusted binary, XML, CSV and ZIP parsing (DoS, XXE, zip bombs, traversal). | HIGH | Ordered pipeline, defusedxml, subprocess parser sandbox with timeout and limits. | SEC-UPL-* |
| R-08 | Prompt injection through activity names, descriptions and file metadata. | MEDIUM | Typed untrusted data blocks, no dangerous tools, policy gate, athleteId from session. | SEC-INJ-* |
| R-09 | Supply chain from new dependencies. | LOW-MEDIUM | `uv.lock` with hashes, pip-audit, minimal dependency set. | none |
| R-10 | Sensitive-category creep: the Personal API also exposes Women's Health and nutrition data. | LOW | Not ingested unless the athlete explicitly opts in. | PRIV-CON-004 |

---

## 9. Threat model summary (STRIDE)

The full model goes to `docs/threat-model.md` (v0 in P1, complete in P4).

| Component | Principal threats | Mitigations | Req |
|---|---|---|---|
| Local API and auth | S: unauthenticated local process or browser; CSRF; DNS rebinding. E: missing ownership checks. | Loopback bind, Host check, authentication, owner-scoped repositories | SEC-AUTH-001/002, SEC-IDOR-001 |
| Garmin Personal auth | I: key or token theft via logs, `.env`, OneDrive, token cache, chat. S: replay of a stolen key. | keyring, in-memory 24 h token, redaction, gitleaks, rotation, revocation guidance | SEC-SEC-*, INT-GAR-006 |
| OAuth/PKCE | Not applicable in V1 (Personal tier uses API key to token). Future business tier: PKCE S256, state parameter, exact redirect URI. | Documented for later | none |
| Push/webhooks | Not applicable in V1. Pull-only means no inbound endpoint, which removes the spoofing and replay surface. Future: signature verification, timestamp window, idempotency. | Documented for later | none |
| File upload | T/D: malicious or oversized files, polyglots, path traversal via filename. | Ordered pipeline, limits, content-hash storage, quarantine | INT-FIL-002/003, SEC-UPL-001/005 |
| FIT parser | D: pathological files exhausting CPU or memory; crashes. | Subprocess sandbox, timeout, record limits | SEC-UPL-004 |
| XML (TCX/GPX) | I/D: XXE, SSRF through external entities, billion laughs. | defusedxml, DTD forbidden | SEC-UPL-002 |
| CSV | T: formula injection on export. D: huge rows. | Escape on export, row and size limits | SEC-INJ-004 |
| ZIP (Garmin download or upload) | D: zip bomb. T: path traversal. | Member count, size and ratio caps; no extraction to paths | SEC-UPL-003 |
| Agent tools | E: prompt injection leading to tool misuse. I: exfiltration through tool arguments. T: injected "publish". | Narrow tools, no outbound or arbitrary tools, athleteId from session, policy gate, PROPOSE/EXECUTE with human confirmation | FR-AGT-001/002, SEC-INJ-001/002 |
| LLM provider | I: sensitive data disclosed to a third party; secrets in prompts. | Consent, minimisation, pre-LLM secret redaction | PRIV-CON-003, SEC-SEC-002/005 |
| Repositories | E/I: IDOR, SQL injection, mass assignment. | Owner-scoped API, parameterised queries, allow-listed command DTOs | SEC-IDOR-001, SEC-INJ-003, SEC-AUTHZ-003 |
| External workout publication | T/R: unconfirmed or replayed writes. | Capability gate (false in V1), confirmation token, idempotency key, audit | INT-GAR-002/003, SEC-AUD-001 |
| Administrator / host | I: another local OS user or malware reads DB and blobs. | Data outside synced folders, BitLocker, per-user ACL, no secrets in DB | A-10 |
| Audit | R: athlete or agent actions are deniable. | Append-only AuditEvent | SEC-AUD-001 |
| Replay | Duplicate commands or imports. | Idempotency keys, hash deduplication | NFR-REL-001 |

---

## 10. SDD file plan

Project root is `Projects/Coach Deportivo/`, or its new location after decision D-1.

```
CLAUDE.md                               P1    project rules (§77)
README.md                               P1
.gitignore, .editorconfig               P0.5
.pre-commit-config.yaml                 P0.5  gitleaks, ruff
pyproject.toml, uv.lock                 P0.5  after D-2
docs/
  discovery/00-discovery-report.md      P0    this file
  architecture.md                       P1
  threat-model.md                       P1 v0, P4 complete
  privacy.md                            P1
  adr/ADR-001 ... ADR-012               P1    mandated by §71
  adr/ADR-013 ... ADR-019               P1    from §4.5 of this report
spec/
  product.md                            P1    vision, loop, scope, out-of-scope
  requirements-index.md                 P1    every ID from §3
  traceability.md                       P1    req -> AT -> test -> code (generated later)
  glossary.md                           P1
  nfr.spec.md                           P1
  functional/
    onboarding, profile, goals, activities, metrics, running, cycling, swimming,
    strength, tennis, recovery, planning, insights          (.spec.md)    P1
    policy, reporting, agent                                (.spec.md)    P1  added
  integration/
    garmin, provider-capabilities, files, canonical-model, deduplication  P1
  security/
    authentication, authorization, secrets, privacy, uploads,
    prompt-injection                                        (.spec.md)    P1
    audit.spec.md                                                         P1  added
  safety/sport-safety.spec.md                                             P1
  contracts/
    tools.spec.md                                                         P1
    activity, workout, training-plan, insight, consent      (.schema.json) P1-P2
    athlete-profile, goal, sport-event, availability, daily-checkin,
    strength-set-result, provider-capabilities, metric-definition,
    data-override, agent-decision                           (.schema.json) P2-P12
  acceptance/
    AT-*.md, Given/When/Then, one file per area                           P1
src/coach/...  tests/...                                                  P0.5 onward
```

---

## 11. Acceptance test plan

Nothing has been executed. All entries are PROPOSED.

### 11.1 Mandatory (spec §74)

| AT | Requirements | Level | First executable |
|---|---|---|---|
| AT-ONB-001 | FR-ONB-001/002 | unit (question planner) + agent-eval | P2 planner, P13 dialogue |
| AT-ACT-001 | FR-ACT-007/008, NFR-REL-001 | integration | P6 (FIT fixtures), P7 (Garmin + FIT) |
| AT-STR-001 | FR-STR-007 | unit | P10 |
| AT-TEN-001 | FR-TEN-001/003 | unit | P9-P10 |
| AT-CYC-001 | FR-CYC-002, FR-PLN-005 | unit | P9 |
| AT-ADP-001 | FR-ADP-003 | unit | P12 |
| AT-SEC-001 | SEC-SEC-005 | security + agent-eval | P4 detector, P13 agent |
| AT-SAFE-001 | SAFE-MED-002/003 | unit (policy) + agent-eval | P12 policy, P13 agent |
| AT-EXP-001 | FR-ADP-004, NFR-EXP-001 | unit + contract | P12 |
| AT-INJ-001 | SEC-INJ-001/002 | security + agent-eval | P6 data path, P13 agent |
| AT-IDOR-001 | SEC-IDOR-001, SEC-AUTHZ-001 | security (repository + API) | P4 |
| AT-GAR-001 | INT-GAR-003/004 | contract + unit | P7 |
| AT-PLN-001 | FR-PLN-002 | unit + integration | P12 |
| AT-RAW-001 | FR-ACT-002/006, PRIV-ACC-002 | integration | P5 |
| AT-MET-001 | FR-MET-001/004 | unit | P3 registry, P8 engine |

### 11.2 Proposed additions

| AT | Requirement | Scenario | First executable |
|---|---|---|---|
| AT-AVL-001 | FR-PRF-005 | Date exception overrides recurring availability | P2 |
| AT-STM-001 | FR-ONB-006 | Valid and invalid state transitions | P2 |
| AT-CONC-001 | NFR-REL-003 | Concurrent profile update is rejected, not overwritten | P2 |
| AT-MET-002 | FR-MET-003 | Application metric is never labelled as a provider metric | P3 |
| AT-CON-001 | PRIV-CON-002, FR-REC-005 | Recovery processing blocked without consent | P4 |
| AT-SEC-002 | SEC-SEC-002/003, NFR-OBS-002 | Secrets absent from logs, errors, prompts and DB | P4 |
| AT-TIM-001 | FR-ACT-003/004 | Activity crossing local midnight; travel timezone; GPX without timezone | P5 |
| AT-XXE-001 | SEC-UPL-002 | TCX/GPX with DTD or external entity is rejected | P6 |
| AT-ZIP-001 | SEC-UPL-003 | Zip bomb and traversal member are rejected | P6 |
| AT-UPL-001 | INT-FIL-003 | Oversized and over-record files are rejected | P6 |
| AT-CSV-001 | SEC-INJ-004 | Formula prefixes neutralised on export | P6 |
| AT-DEG-001 | INT-GAR-008 | Garmin down: local history usable, freshness reported | P7 |
| AT-SWM-001 | FR-SWM-001/004 | Pool and open-water sessions not compared without context | P9 |
| AT-REC-001 | FR-REC-003 | One low reading does not trigger REST | P11 |
| AT-REC-002 | FR-REC-002 | Wearable does not override athlete-reported condition | P11 |
| AT-LIN-001 | NFR-TRC-001 | Recommendation traces to raw source | P11 |
| AT-ONB-002/003/004 | FR-ONB-003/004/005 | Readiness, provisional plan, baseline collection | P12 |
| AT-GOL-001 | FR-GOL-003 | Feasibility pipeline output | P12 |
| AT-UNP-001 | FR-ACT-011 | §47: unplanned 120 min tennis on a rest day affects Thursday | P12 |
| AT-PLN-002 | FR-PLN-003 | Exactly one ACTIVE version after activation | P12 |
| AT-POL-001 | FR-POL-001/002 | AI proposal violating availability or SAFETY_HOLD is rejected | P12 |
| AT-STR-002 | FR-STR-001 | Generated plan contains strength unless contraindicated | P12 |
| AT-IDOR-002 | FR-AGT-002 | Agent cannot target another athlete | P13 |
| AT-DEL-001 | PRIV-DEL-001 | Deletion removes every listed store | P15 |

### 11.3 Tooling

- pytest with `@pytest.mark.req("FR-ACT-007")` markers, from which the traceability matrix is generated (NFR-TRC-002).
- hypothesis for deduplication, time and unit-conversion properties.
- jsonschema contract tests against `spec/contracts`.
- import-linter for layering; ruff (including security rules), mypy strict, pip-audit, gitleaks.
- Agent evals: scenario fixtures with recorded tool results; assertions on tool calls, numeric traceability to tool output, and safety behaviour. Run separately from the unit suite because they need model access and consent.

---

## 12. Implementation phases (adapted to the actual repository)

| Phase | Scope | Depends on | Notes |
|---|---|---|---|
| P0 | Discovery | none | **Done** |
| P0.5 | Workspace: location, git, `.gitignore`, gitleaks, uv project, ruff/mypy/pytest/import-linter skeleton | D-1, D-2 | New phase because the repository was empty |
| P1 | SDD: spec tree, requirement index, ADR-001 to ADR-019, architecture, threat model v0, privacy, CLAUDE.md, acceptance specs | D-1 | Stack-neutral content |
| P2 | Domain core and shared value objects (FieldValue/UNKNOWN, units, time, provenance, quality); Athlete, Availability, Goal/Event, Consent, CoachingState; OwnerScope primitive; log redaction; schemas and contract tests | P0.5, P1 | Ownership and redaction pulled forward from P4: ownership must exist before the first repository |
| P3 | Metric registry; RAW/NORMALIZED/DERIVED storage; lineage refs; SQLite and blob store | P2 | |
| P4 | Local authentication, authorization chain, CredentialProvider (keyring), audit, consent enforcement, threat model v1 | P2-P3 | |
| P5 | Canonical Activity, quality, uncertainty, deduplication, overrides, repository | P3-P4 | |
| P6 | Ingestion pipeline: FIT (garmin-fit-sdk), TCX/GPX (defusedxml), CSV; parser sandbox; ZIP handling | P5 | First end-to-end value: importing the athlete's own Garmin exports |
| P7 | FitnessProvider, capability model, Garmin Personal adapter, token broker, sync, degradation | P5-P6 | Live tests need the athlete's key, stored via keyring |
| P8 | Deterministic metrics engine (load, session RPE, zones, aggregations, HR drift) | P5 | Can run parallel to P6-P7 |
| P9 | Running, cycling and swimming analytics; tennis load | P8 | |
| P10 | Exercise catalogue, strength prescription and execution, interference engine | P8, P2 | Parallel to P9 |
| P11 | Check-in, recovery engine, pattern detection, insights, deterministic report parts | P9-P10 | |
| P12 | Full policy engine, planning engine, plan versioning, execution matching, unplanned handling, adaptive loop | P11 | |
| P13 | AI coach: tools, prompt, LLM adapter, ingress filters, UI decision, agent evals | P12; G-5 before LLM use of Garmin data | |
| P14 | Garmin workout publication | Starter or Enterprise tier | **Deferred.** V1 ships LocalWorkoutPublisher only. |
| P15 | Hardening, complete deletion workflow, observability, end-to-end verification | All | |

Critical path: P0.5, P1, P2, P3, P5, P6, P8, P11, P12, P13. P7 can proceed in parallel with P8-P10 once P6 is done.

---

## 13. Assumptions

| ID | Assumption | Impact if wrong | Status |
|---|---|---|---|
| A-01 | One athlete (the user) in V1. The model supports many athletes with strict ownership; no multi-tenant infrastructure. | Add tenancy and an identity provider | To confirm |
| A-02 | Local-first deployment on the athlete's Windows PC; cloud optional later. | Add hosted auth and secret store through existing ports | To confirm |
| A-03 | Python stack (§4.1). | ADR-013 changes | Decision D-2 |
| A-04 | Garmin access through the Personal tier (own data, pull-only, read-only). | Starter/Enterprise adds push and publishing | To confirm |
| A-05 | Garmin facts as verified on 2026-10-24; re-verified at P7. | Adapter configuration | Rule |
| A-06 | Garmin endpoints, headers and schemas come only from the official client, Swagger or real responses. Nothing is guessed. | none | Rule |
| A-07 | Training day = local date at activity start in the activity's own timezone. When unknown: athlete timezone, flagged ESTIMATED_VALUE. | Day-boundary reporting | Proposed |
| A-08 | Canonical units are metric/SI; display follows athlete preference. | none | Proposed |
| A-09 | LLM: Anthropic Claude behind an LLMProvider port; requires explicit AI-processing consent. | Swap adapter | To confirm |
| A-10 | Encryption at rest through the OS (BitLocker) in V1; secrets in Windows Credential Manager via keyring. | Add DB encryption (e.g. SQLCipher) | To confirm |
| A-11 | Malware scanning through an optional scanner hook (e.g. the Microsoft Defender command-line scanner if present). Without a scanner, imports are flagged. | none | Verify at P6 |
| A-12 | Session RPE on the CR-10 scale (Foster session-RPE as a STANDARD_CALCULATION). DailyCheckIn on a documented 1-5 scale with anchors. | Spec change | Proposed |
| A-13 | Leaving SAFETY_HOLD requires explicit athlete confirmation (e.g. declared professional clearance) and is audited. The system never clears a hold on its own. | Safety spec | Proposed |
| A-14 | Spec and code in English. Athlete-facing coaching language is configurable, default Spanish. | none | To confirm |
| A-15 | Women's Health and nutrition data are not ingested unless explicitly enabled. | none | Proposed |
| A-16 | Dedup tolerances are defined and versioned in the P5 spec; ambiguous matches require athlete confirmation. | none | Proposed |

---

## 14. Blockers

> **Resolution (2026-10-24).** The athlete decided: D-1 keep the repository in its current
> (synced) folder; D-2 Python; D-3 GitHub remote, pushed later. Recorded in ADR-013, ADR-014
> and ADR-020. Runtime athlete data and secrets still default to locations outside synced
> folders (ADR-014). The rest of this section is the original pre-decision text.

**No hard blockers for P1** (SDD materialisation).

Decisions needed before P0.5 (code scaffolding):

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| D-1 | Workspace location (privacy and security, R-01) | (a) Move the repository to a non-synced path such as `C:\dev\coach-deportivo`, data under `%LOCALAPPDATA%`. (b) Keep code in OneDrive, data and secrets outside. (c) Everything in OneDrive. | (a). (b) is acceptable. (c) is not recommended. |
| D-2 | Stack | Python, .NET 8, TypeScript | Python (§4.1) |
| D-3 | Git remote | Private GitHub (enables CI), other private remote, local only | Private remote |

Needed later; not blocking now:

- **P7:** the athlete creates a Garmin Personal API key in Garmin Connect and stores it through the application's secure command. It is never pasted into chat.
- **P13:** the athlete reviews the Garmin Personal API Terms and Conditions on third-party AI processing (G-5) before Garmin-sourced data is sent to an LLM.

---

## Appendix A: Discovery actions executed

| Action | Result |
|---|---|
| Listed the project directory recursively | Empty |
| Listed the parent workspace (names only) | Unrelated projects; not opened |
| Checked toolchain versions (node, npm, python, uv, pip, dotnet, git, docker; also java, go, az, pnpm, yarn) | See §1.2 |
| Checked the persistent memory directory | Empty |
| Web research on the Garmin Connect Developer Program, Personal API, official client and FIT SDK | See §7 and sources |

## Sources

- Garmin, Connect Developer Program overview: https://developer.garmin.com/gc-developer-program/overview/
- Garmin, Personal API: https://developer.garmin.com/gc-developer-program/personal/personal-api/
- Garmin, Personal API FAQ: https://developer.garmin.com/gc-developer-program/personal/personal-faq/
- Garmin, official Personal API Python client: https://github.com/garmin/connect-personal-api-python
- Garmin, official FIT Python SDK: https://github.com/garmin/fit-python-sdk
- the5krunner, "Garmin Personal API Key 2026": https://the5krunner.com/2026/10/22/garmin-personal-api/
- DC Rainmaker, "Garmin Revamps and Opens Up Connect Developer API Program": https://www.dcrainmaker.com/2026/10/garmin-connect-api-revamps-connect-developer.html
