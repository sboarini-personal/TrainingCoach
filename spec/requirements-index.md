# Requirements index

Authoritative list of requirement IDs. Format and statuses are defined in [README.md](README.md).
The "Owner" column is the spec file (relative to `spec/`) that defines the requirement in detail.
`uv run pytest` checks that every ID is unique, every owner file exists and mentions the ID,
and every acceptance test referenced here is defined in `spec/acceptance/`.

## Functional

| ID | Title | Owner | Acceptance | Status |
|---|---|---|---|---|
| FR-ONB-001 | Progressive onboarding in small groups of related questions | functional/onboarding.spec.md | AT-ONB-001 | ACCEPTED |
| FR-ONB-002 | Known and valid information is never requested again | functional/onboarding.spec.md | AT-ONB-001 | ACCEPTED |
| FR-ONB-003 | Definition-of-Ready gate before full plan creation | functional/onboarding.spec.md | AT-ONB-002 | ACCEPTED |
| FR-ONB-004 | Missing preferred inputs yield only a provisional plan with explicit assumptions | functional/onboarding.spec.md | AT-ONB-003 | ACCEPTED |
| FR-ONB-005 | Insufficient history enters BASELINE_COLLECTION with conservative assessments | functional/onboarding.spec.md | AT-ONB-004 | ACCEPTED |
| FR-ONB-006 | Coaching state machine with testable transitions | functional/onboarding.spec.md | AT-STM-001 | ACCEPTED |
| FR-PRF-001 | AthleteProfile with identity, physiology, heart rate, preferences, devices, sensors | functional/profile.spec.md | - | ACCEPTED |
| FR-PRF-002 | Unknown athlete values stay UNKNOWN and are never invented | functional/profile.spec.md | - | ACCEPTED |
| FR-PRF-003 | Experience stored independently per sport | functional/profile.spec.md | - | ACCEPTED |
| FR-PRF-004 | Recurring availability and resources | functional/profile.spec.md | - | ACCEPTED |
| FR-PRF-005 | AvailabilityException with precedence exception over recurring over default | functional/profile.spec.md | AT-AVL-001 | ACCEPTED |
| FR-PRF-006 | Declared health constraints captured and enforced by the policy engine | functional/profile.spec.md | AT-POL-001 | ACCEPTED |
| FR-PRF-007 | Preferred language for athlete-facing text | functional/profile.spec.md | - | PROPOSED |
| FR-GOL-001 | SportEvent separate from Goal | functional/goals.spec.md | - | ACCEPTED |
| FR-GOL-002 | Goal priority A, B, C | functional/goals.spec.md | - | ACCEPTED |
| FR-GOL-003 | Deterministic feasibility pipeline from current state to feasibility | functional/goals.spec.md | AT-GOL-001 | ACCEPTED |
| FR-GOL-004 | Desired, realistic and minimum-satisfactory goals when appropriate | functional/goals.spec.md | AT-GOL-001 | ACCEPTED |
| FR-GOL-005 | Outcomes are never guaranteed | functional/goals.spec.md | AT-GOL-001 | ACCEPTED |
| FR-ACT-001 | Canonical provider-independent Activity with schemaVersion | integration/canonical-model.spec.md | - | ACCEPTED |
| FR-ACT-002 | RAW immutable, NORMALIZED and DERIVED data layers | functional/activities.spec.md | AT-RAW-001 | ACCEPTED |
| FR-ACT-003 | UTC persistence with original local time, timezone and offset | functional/activities.spec.md | AT-TIM-001 | ACCEPTED |
| FR-ACT-004 | Training day is the local date at activity start | functional/activities.spec.md | AT-TIM-001 | ACCEPTED |
| FR-ACT-005 | Field-level conflict resolution with per-field provenance | functional/activities.spec.md | AT-ACT-001 | ACCEPTED |
| FR-ACT-006 | Traceable DataOverride corrections that never destroy originals | functional/activities.spec.md | AT-RAW-001 | ACCEPTED |
| FR-ACT-007 | Deduplication rules and idempotent imports | integration/deduplication.spec.md | AT-ACT-001 | ACCEPTED |
| FR-ACT-008 | Enrichment never duplicates distance, duration, load or volume | integration/deduplication.spec.md | AT-ACT-001 | ACCEPTED |
| FR-ACT-009 | DataQuality with completeness, reliability, anomalies and confidence | functional/activities.spec.md | - | ACCEPTED |
| FR-ACT-010 | Enumerated uncertainty reasons | functional/activities.spec.md | - | ACCEPTED |
| FR-ACT-011 | Unplanned activities count toward load, interference, recovery and adaptation | functional/activities.spec.md | AT-UNP-001 | ACCEPTED |
| FR-MET-001 | Deterministic sport calculations run in application code | functional/metrics.spec.md | AT-MET-001 | ACCEPTED |
| FR-MET-002 | MetricDefinition registry | functional/metrics.spec.md | AT-MET-001 | ACCEPTED |
| FR-MET-003 | Metric origin classification never mislabelled | functional/metrics.spec.md | AT-MET-002 | ACCEPTED |
| FR-MET-004 | Derived values keep method, version, inputs and minimum quality | functional/metrics.spec.md | AT-MET-001 | ACCEPTED |
| FR-MET-005 | External and internal load modelled separately | functional/metrics.spec.md | - | ACCEPTED |
| FR-MET-006 | Cross-sport load preserves sport context | functional/metrics.spec.md | AT-TEN-001 | ACCEPTED |
| FR-MET-007 | Proprietary metrics of different providers are not assumed equivalent | functional/metrics.spec.md | AT-MET-002 | ACCEPTED |
| FR-RUN-001 | Running analytics over available fields only | functional/running.spec.md | - | ACCEPTED |
| FR-RUN-002 | Detect pace and heart-rate changes and HR drift | functional/running.spec.md | - | ACCEPTED |
| FR-RUN-003 | Detect pacing instability and interval degradation | functional/running.spec.md | - | ACCEPTED |
| FR-RUN-004 | Detect cadence, efficiency and fatigue changes | functional/running.spec.md | - | ACCEPTED |
| FR-CYC-001 | Cycling analytics over available fields | functional/cycling.spec.md | - | ACCEPTED |
| FR-CYC-002 | Measured and estimated power always distinguished | functional/cycling.spec.md | AT-CYC-001 | ACCEPTED |
| FR-CYC-003 | FTP used only when available, with provenance | functional/cycling.spec.md | AT-CYC-001 | ACCEPTED |
| FR-CYC-004 | Power distribution and fatigue analysis only with measured power | functional/cycling.spec.md | AT-CYC-001 | ACCEPTED |
| FR-SWM-001 | Pool and open-water sessions always distinguished | functional/swimming.spec.md | AT-SWM-001 | ACCEPTED |
| FR-SWM-002 | Pool metrics including SWOLF only where available and valid | functional/swimming.spec.md | - | ACCEPTED |
| FR-SWM-003 | Swim consistency, efficiency, pace degradation and technical fatigue | functional/swimming.spec.md | - | ACCEPTED |
| FR-SWM-004 | Pool and open-water comparison requires explicit context | functional/swimming.spec.md | AT-SWM-001 | ACCEPTED |
| FR-STR-001 | Strength is first-class and mandatory in planning unless contraindicated | functional/strength.spec.md | AT-STR-002 | ACCEPTED |
| FR-STR-002 | Controlled vocabularies for movement patterns and muscle groups | functional/strength.spec.md | - | ACCEPTED |
| FR-STR-003 | Exercise catalogue with canonical identity and aliases | functional/strength.spec.md | - | ACCEPTED |
| FR-STR-004 | Strength prescriptions explain what, why, dose, targets, progression, interference | functional/strength.spec.md | - | ACCEPTED |
| FR-STR-005 | StrengthSetResult captures actual execution | functional/strength.spec.md | - | ACCEPTED |
| FR-STR-006 | Strength progression uses execution history | functional/strength.spec.md | - | ACCEPTED |
| FR-STR-007 | Interference engine with rules plus athlete context | functional/strength.spec.md | AT-STR-001 | ACCEPTED |
| FR-STR-008 | Strength volume reported by muscle group | functional/strength.spec.md | - | ACCEPTED |
| FR-TEN-001 | Tennis is recreational and counts as training load | functional/tennis.spec.md | AT-TEN-001 | ACCEPTED |
| FR-TEN-002 | Tennis session capture | functional/tennis.spec.md | - | ACCEPTED |
| FR-TEN-003 | Tennis regional loading feeds interference and recovery | functional/tennis.spec.md | AT-TEN-001 | ACCEPTED |
| FR-REC-001 | DailyCheckIn with an explicitly documented scale | functional/recovery.spec.md | - | ACCEPTED |
| FR-REC-002 | Wearable metrics never automatically override athlete-reported condition | functional/recovery.spec.md | AT-REC-002 | ACCEPTED |
| FR-REC-003 | Recovery decisions use trends and multiple signals, never one reading | functional/recovery.spec.md | AT-REC-001 | ACCEPTED |
| FR-REC-004 | RecoveryPrescription types with reason, duration and re-evaluation | functional/recovery.spec.md | - | ACCEPTED |
| FR-REC-005 | Recovery inputs processed only with the matching consent | functional/recovery.spec.md | AT-CON-001 | ACCEPTED |
| FR-INS-001 | Insight separates observation, interpretation, hypothesis and recommendation | functional/insights.spec.md | AT-EXP-001 | ACCEPTED |
| FR-INS-002 | Pattern detection catalogue | functional/insights.spec.md | - | ACCEPTED |
| FR-INS-003 | Correlation is never presented as causation | functional/insights.spec.md | - | ACCEPTED |
| FR-PLN-001 | Training phases | functional/planning.spec.md | - | ACCEPTED |
| FR-PLN-002 | Plans are immutable versions; changes create new versions | functional/planning.spec.md | AT-PLN-001 | ACCEPTED |
| FR-PLN-003 | At most one ACTIVE version; activation supersedes atomically | functional/planning.spec.md | AT-PLN-002 | ACCEPTED |
| FR-PLN-004 | TrainingWeek structure | functional/planning.spec.md | - | ACCEPTED |
| FR-PLN-005 | Endurance intensity only in dimensions supported by reliable data | functional/planning.spec.md | AT-CYC-001 | ACCEPTED |
| FR-PLN-006 | No universal percentage progression rule | functional/planning.spec.md | - | ACCEPTED |
| FR-PLN-007 | WorkoutExecution planned-vs-completed status and compliance | functional/planning.spec.md | AT-UNP-001 | ACCEPTED |
| FR-PLN-008 | Plans combine endurance, intervals, technique, strength, tennis, recovery, rest, evaluation | functional/planning.spec.md | AT-STR-002 | ACCEPTED |
| FR-ADP-001 | Adaptive loop from planned and actual to decision | functional/planning.spec.md | AT-UNP-001 | ACCEPTED |
| FR-ADP-002 | Adaptation decisions MAINTAIN, PROGRESS, REDUCE, RESCHEDULE, REPLACE, REST | functional/planning.spec.md | AT-ADP-001 | ACCEPTED |
| FR-ADP-003 | One isolated poor workout never triggers plan reconstruction | functional/planning.spec.md | AT-ADP-001 | ACCEPTED |
| FR-ADP-004 | Load reductions carry evidence, reason, uncertainty and confidence | functional/planning.spec.md | AT-EXP-001 | ACCEPTED |
| FR-POL-001 | Deterministic policy engine | functional/policy.spec.md | AT-POL-001 | ACCEPTED |
| FR-POL-002 | AI recommendations cannot bypass policy decisions | functional/policy.spec.md | AT-POL-001 | ACCEPTED |
| FR-RPT-001 | Post-activity report | functional/reporting.spec.md | - | ACCEPTED |
| FR-RPT-002 | Weekly report | functional/reporting.spec.md | - | ACCEPTED |
| FR-RPT-003 | Monthly report with cautious forecast | functional/reporting.spec.md | - | ACCEPTED |
| FR-AGT-001 | Agent uses only narrow typed tools | functional/agent.spec.md | AT-INJ-001 | ACCEPTED |
| FR-AGT-002 | Acting athlete bound from the session, never from tool arguments | functional/agent.spec.md | AT-IDOR-002 | ACCEPTED |
| FR-AGT-003 | Significant decisions recorded as AgentDecision without chain-of-thought | functional/agent.spec.md | - | ACCEPTED |
| FR-AGT-004 | No fabricated metrics or tool results in agent output | functional/agent.spec.md | AT-EXP-001 | ACCEPTED |

## Integration

| ID | Title | Owner | Acceptance | Status |
|---|---|---|---|---|
| INT-GAR-001 | FitnessProvider interface | integration/provider-capabilities.spec.md | AT-DEG-001 | ACCEPTED |
| INT-GAR-002 | WorkoutPublisher interface separating propose from publish | integration/provider-capabilities.spec.md | AT-GAR-001 | ACCEPTED |
| INT-GAR-003 | Capability model gates every provider feature | integration/provider-capabilities.spec.md | AT-GAR-001 | ACCEPTED |
| INT-GAR-004 | Capabilities configured or observed, never assumed | integration/provider-capabilities.spec.md | AT-GAR-001 | ACCEPTED |
| INT-GAR-005 | Garmin Personal-tier adapter is pull-only and read-only | integration/garmin.spec.md | AT-GAR-001 | ACCEPTED |
| INT-GAR-006 | Credentials confined to integration and secret layers; tokens in memory | integration/garmin.spec.md | AT-SEC-002 | ACCEPTED |
| INT-GAR-007 | Incremental, resumable, idempotent synchronisation | integration/garmin.spec.md | AT-ACT-001 | ACCEPTED |
| INT-GAR-008 | Graceful degradation when the provider is unavailable | integration/garmin.spec.md | AT-DEG-001 | ACCEPTED |
| INT-GAR-009 | Provider payloads persisted unmodified in RAW | integration/garmin.spec.md | AT-RAW-001 | ACCEPTED |
| INT-GAR-010 | Client-side throttling and backoff | integration/garmin.spec.md | AT-DEG-001 | ACCEPTED |
| INT-FIL-001 | Ingest FIT, TCX, GPX and CSV | integration/files.spec.md | AT-ACT-001 | ACCEPTED |
| INT-FIL-002 | Ordered ingestion pipeline | integration/files.spec.md | AT-UPL-001 | ACCEPTED |
| INT-FIL-003 | Configurable ingestion resource limits | integration/files.spec.md | AT-UPL-001 | ACCEPTED |
| INT-FIL-004 | Parse failures recorded without partial persistence | integration/files.spec.md | AT-UPL-001 | ACCEPTED |

## Security

| ID | Title | Owner | Acceptance | Status |
|---|---|---|---|---|
| SEC-AUTH-001 | Authentication required for all private athlete data | security/authentication.spec.md | AT-IDOR-001 | ACCEPTED |
| SEC-AUTH-002 | Loopback binding, Host validation, cross-origin rejection | security/authentication.spec.md | - | ACCEPTED |
| SEC-AUTH-003 | Rate limiting of authentication and API | security/authentication.spec.md | - | ACCEPTED |
| SEC-AUTHZ-001 | User to athlete to ownership to resource chain on every operation | security/authorization.spec.md | AT-IDOR-001 | ACCEPTED |
| SEC-AUTHZ-002 | Least-privilege agent tool permissions | security/authorization.spec.md | AT-IDOR-002 | ACCEPTED |
| SEC-AUTHZ-003 | Allow-listed command fields prevent mass assignment | security/authorization.spec.md | - | ACCEPTED |
| SEC-IDOR-001 | No unscoped get-by-id; foreign resources return not-found | security/authorization.spec.md | AT-IDOR-001 | ACCEPTED |
| SEC-IDOR-002 | Non-enumerable identifiers as defence in depth | security/authorization.spec.md | - | ACCEPTED |
| SEC-SEC-001 | Secrets only via CredentialProvider and a secret store | security/secrets.spec.md | AT-SEC-002 | ACCEPTED |
| SEC-SEC-002 | The LLM never receives credential values | security/secrets.spec.md | AT-SEC-002 | ACCEPTED |
| SEC-SEC-003 | Secrets never in logs, URLs, plaintext storage, errors, telemetry or prompts | security/secrets.spec.md | AT-SEC-002 | ACCEPTED |
| SEC-SEC-004 | Credential rotation and expiry tracking | security/secrets.spec.md | - | ACCEPTED |
| SEC-SEC-005 | Accidental secret disclosure detected, not repeated, not persisted | security/secrets.spec.md | AT-SEC-001 | ACCEPTED |
| SEC-SEC-006 | Automated secret scanning on commit and in CI | security/secrets.spec.md | - | ACCEPTED |
| SEC-INJ-001 | External content reaches the LLM only as delimited untrusted data | security/prompt-injection.spec.md | AT-INJ-001 | ACCEPTED |
| SEC-INJ-002 | Data cannot change tool permissions, policies or instructions | security/prompt-injection.spec.md | AT-INJ-001 | ACCEPTED |
| SEC-INJ-003 | Parameterised queries only | security/prompt-injection.spec.md | - | ACCEPTED |
| SEC-INJ-004 | CSV export neutralises formula-capable prefixes | security/prompt-injection.spec.md | AT-CSV-001 | ACCEPTED |
| SEC-UPL-001 | Extension and content-signature validation | security/uploads.spec.md | AT-UPL-001 | ACCEPTED |
| SEC-UPL-002 | Hardened XML parsing without DTD or external entities | security/uploads.spec.md | AT-XXE-001 | ACCEPTED |
| SEC-UPL-003 | Archive handling resists zip bombs and traversal | security/uploads.spec.md | AT-ZIP-001 | ACCEPTED |
| SEC-UPL-004 | Parsers run with timeout and resource limits and fail safely | security/uploads.spec.md | AT-UPL-001 | ACCEPTED |
| SEC-UPL-005 | Filenames never determine storage paths | security/uploads.spec.md | AT-UPL-001 | ACCEPTED |
| SEC-UPL-006 | Malware-scan hook; unscanned imports flagged | security/uploads.spec.md | - | ACCEPTED |
| SEC-AUD-001 | Append-only audit events for security-relevant actions | security/audit.spec.md | AT-DEL-001 | ACCEPTED |

## Privacy

| ID | Title | Owner | Acceptance | Status |
|---|---|---|---|---|
| PRIV-CON-001 | ConsentRecord model | security/privacy.spec.md | AT-CON-001 | ACCEPTED |
| PRIV-CON-002 | Missing or revoked consent blocks dependent processing | security/privacy.spec.md | AT-CON-001 | ACCEPTED |
| PRIV-CON-003 | Consent for processing by an external AI provider | security/privacy.spec.md | AT-CON-001 | PROPOSED |
| PRIV-CON-004 | Unnecessary sensitive categories not ingested by default | security/privacy.spec.md | - | PROPOSED |
| PRIV-ACC-001 | Athlete can inspect and export their data | security/privacy.spec.md | - | ACCEPTED |
| PRIV-ACC-002 | Athlete can correct their data | security/privacy.spec.md | AT-RAW-001 | ACCEPTED |
| PRIV-DEL-001 | Deletion workflow covers every data store | security/privacy.spec.md | AT-DEL-001 | ACCEPTED |
| PRIV-DEL-002 | Provider access revoked where supported, otherwise instructions given | security/privacy.spec.md | AT-DEL-001 | ACCEPTED |
| PRIV-DEL-003 | Retained audit information minimised | security/privacy.spec.md | AT-DEL-001 | ACCEPTED |

## Safety

| ID | Title | Owner | Acceptance | Status |
|---|---|---|---|---|
| SAFE-MED-001 | No diagnosis, medication or clinical rehabilitation | safety/sport-safety.spec.md | AT-SAFE-001 | ACCEPTED |
| SAFE-MED-002 | Declared red flags activate SAFETY_HOLD | safety/sport-safety.spec.md | AT-SAFE-001 | ACCEPTED |
| SAFE-MED-003 | SAFETY_HOLD enforced by the policy engine | safety/sport-safety.spec.md | AT-SAFE-001 | ACCEPTED |
| SAFE-MED-004 | Safety reason communicated and healthcare evaluation recommended | safety/sport-safety.spec.md | AT-SAFE-001 | ACCEPTED |
| SAFE-MED-005 | Leaving SAFETY_HOLD requires explicit athlete action and is audited | safety/sport-safety.spec.md | AT-SAFE-001 | PROPOSED |

## Non-functional

| ID | Title | Owner | Acceptance | Status |
|---|---|---|---|---|
| NFR-REL-001 | Idempotent ingestion and commands | nfr.spec.md | AT-ACT-001 | ACCEPTED |
| NFR-REL-002 | Explicit retry strategy and no silent data loss | nfr.spec.md | AT-DEG-001 | ACCEPTED |
| NFR-REL-003 | Optimistic concurrency on mutable aggregates | nfr.spec.md | AT-CONC-001 | ACCEPTED |
| NFR-REL-004 | Schema versioning and explicit, non-destructive migrations | nfr.spec.md | - | ACCEPTED |
| NFR-REL-005 | Backup and restore of the local data store | nfr.spec.md | - | PROPOSED |
| NFR-OBS-001 | Structured observability events | nfr.spec.md | - | ACCEPTED |
| NFR-OBS-002 | Log and telemetry redaction | nfr.spec.md | AT-SEC-002 | ACCEPTED |
| NFR-EXP-001 | Significant plan modifications are explained | nfr.spec.md | AT-EXP-001 | ACCEPTED |
| NFR-TRC-001 | Queryable lineage from recommendation to source | nfr.spec.md | AT-LIN-001 | ACCEPTED |
| NFR-TRC-002 | Tests declare requirement IDs; traceability matrix generated | nfr.spec.md | - | ACCEPTED |
