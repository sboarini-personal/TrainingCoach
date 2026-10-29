# Acceptance: metrics and lineage

Implementation status (P8): AT-MET-001 is executed by the metrics engine over activities
(`tests/unit/test_activity_metrics.py`, `tests/unit/test_metrics_engine.py`,
`tests/integration/test_cli_metrics.py`): speed, pace and time in zones are calculated in
code from the EFFECTIVE view, store their input snapshot and recompute idempotently, and
an activity below the minimum quality gets an UNKNOWN value with its reason. AT-LIN-001
is executed for a weekly load total down to session values, activities and RAW sources
(`tests/unit/test_analytics.py`); insights and recommendations arrive in P11.

Implementation status (P3): AT-MET-001 is executed with session RPE load
(`tests/unit/test_derived_metrics.py`, `tests/unit/test_derived_metric_service.py`); the full
metrics engine over activities comes in P8. AT-MET-002 is executed by
`tests/unit/test_metric_registry.py`. AT-LIN-001 is partly executed
(`tests/unit/test_lineage.py`): lineage resolves derived values and RAW sources, and activity
references resolve once the canonical Activity exists (P5).

### AT-MET-001 — Deterministic metrics in code with provenance
- Requirements: FR-MET-001, FR-MET-002, FR-MET-004
- Level: unit. First executable: P3 (registry), P8 (engine).

**Given** an activity with known duration and RPE
**When** session RPE load is required
**Then** it is calculated by application code
**And** the stored value records metric ID, calculation version, input references and input confidence
**And** recomputation with the same inputs and version gives the same value
**And** when input quality is below the metric's minimum the value is UNKNOWN with a reason.

### AT-MET-002 — Metric origin is never mislabelled
- Requirements: FR-MET-003, FR-MET-007
- Level: unit and contract. First executable: P3.

**Given** an application-calculated load and a provider training-effect value
**Then** the first has origin STANDARD_CALCULATION or APPLICATION_DERIVED and the second PROVIDER_DERIVED with the provider name
**And** a provider metric is never merged with a similarly named metric from another provider.

### AT-LIN-001 — Recommendation traces to the source
- Requirements: NFR-TRC-001
- Level: integration. First executable: P11.

**Given** a recommendation produced from an insight
**When** its lineage is requested
**Then** the chain insight, derived metric values, normalized fields, raw sources and provider record or file is returned
**And** the chain still resolves after a later correction of an input field.
