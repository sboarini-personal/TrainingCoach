"""Contract tests for spec/contracts: schema validity, examples, and rules the spec forbids.

Each negative test mutates a valid example and asserts the schema rejects the result, so the
contract itself enforces the cited requirement.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from schemas import EXAMPLES_DIR, REGISTRY, SCHEMAS, load_json
from schemas import validation_errors as _errors

EXAMPLE_FILES = sorted(EXAMPLES_DIR.glob("*.json"))
_load = load_json


def _example(filename: str) -> dict[str, Any]:
    data: dict[str, Any] = _load(EXAMPLES_DIR / filename)
    return copy.deepcopy(data)


def _refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


# --------------------------------------------------------------------------- structure


def test_expected_contracts_exist() -> None:
    expected = {
        "common", "activity", "workout", "training-plan", "insight", "consent",
        "athlete-profile", "availability", "goal", "sport-event",
        "raw-source", "derived-metric-value", "consent-ledger", "coaching-state",
        "data-override", "data-quality-issue", "source-observation",
        "ingestion-record", "parser-output", "time-series", "integration-status",
        "laps", "lengths", "exercise", "sets", "strength-session",
    }  # fmt: skip
    assert expected <= SCHEMAS.keys(), sorted(expected - SCHEMAS.keys())


@pytest.mark.req("NFR-REL-004")
@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    schema = SCHEMAS[name]
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == f"urn:coach:schema:{name}:1"
    assert schema.get("title")


@pytest.mark.req("NFR-REL-004")
@pytest.mark.parametrize("name", sorted(set(SCHEMAS) - {"common"}))
def test_persisted_contracts_require_schema_version(name: str) -> None:
    assert "schemaVersion" in SCHEMAS[name]["required"]


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_all_references_resolve(name: str) -> None:
    resolver = REGISTRY.resolver(base_uri=SCHEMAS[name]["$id"])
    for ref in set(_refs(SCHEMAS[name])):
        resolver.lookup(ref)  # raises if unresolvable


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_is_valid(path: Path) -> None:
    schema_name = path.name.split(".")[0]
    assert schema_name in SCHEMAS, f"no schema for example {path.name}"
    assert _errors(schema_name, _load(path)) == []


# --------------------------------------------------------------------------- activity


@pytest.mark.req("FR-ACT-001", "SEC-AUTHZ-003")
def test_activity_rejects_unknown_fields() -> None:
    activity = _example("activity.example.json")
    activity["isAdmin"] = True
    assert _errors("activity", activity)


@pytest.mark.req("FR-ACT-003")
@pytest.mark.parametrize("value", ["2026-10-20T21:00:00", "2026-10-20T18:00:00-03:00"])
def test_activity_start_must_be_utc(value: str) -> None:
    activity = _example("activity.example.json")
    activity["timing"]["startTimeUtc"] = value
    assert _errors("activity", activity)


@pytest.mark.req("FR-ACT-004")
def test_activity_requires_training_day_and_basis() -> None:
    activity = _example("activity.example.json")
    del activity["timing"]["trainingDayBasis"]
    assert _errors("activity", activity)


@pytest.mark.req("FR-ACT-009", "FR-PRF-002")
def test_unknown_values_are_null_not_invented() -> None:
    activity = _example("activity.example.json")
    activity["distance"]["meters"] = None
    activity["cardiovascular"]["averageHr"] = None
    assert _errors("activity", activity) == []


@pytest.mark.req("FR-ACT-005")
def test_field_provenance_requires_source_and_rule() -> None:
    activity = _example("activity.example.json")
    del activity["provenance"]["fields"]["distance.meters"]["rule"]
    assert _errors("activity", activity)


@pytest.mark.req("FR-SWM-001")
def test_swimming_subtype_is_pool_open_water_or_unknown() -> None:
    activity = _example("activity.example.json")
    activity["classification"] = {"sport": "SWIMMING", "subtype": "TRACK"}
    assert _errors("activity", activity)
    for subtype in ("POOL", "OPEN_WATER", None):
        activity["classification"]["subtype"] = subtype
        assert _errors("activity", activity) == []


@pytest.mark.req("FR-CYC-002")
def test_power_must_declare_its_source() -> None:
    activity = _example("activity.example.json")
    activity["performance"]["power"] = {"averageWatts": 210, "maxWatts": 480}
    assert _errors("activity", activity)
    activity["performance"]["power"]["source"] = "UNKNOWN"
    assert _errors("activity", activity) == []


@pytest.mark.req("FR-MET-003", "FR-MET-007")
@pytest.mark.parametrize("origin", ["STANDARD_CALCULATION", "APPLICATION_DERIVED"])
def test_provider_metric_cannot_claim_application_origin(origin: str) -> None:
    activity = _example("activity.example.json")
    activity["training"]["aerobicEffect"]["origin"] = origin
    assert _errors("activity", activity)


@pytest.mark.req("FR-TEN-002")
def test_tennis_subtype_is_match_or_practice() -> None:
    activity = _example("activity.example.json")
    activity["classification"] = {"sport": "TENNIS", "subtype": "DOUBLES_FINAL"}
    assert _errors("activity", activity)
    activity["classification"]["subtype"] = "MATCH"
    assert _errors("activity", activity) == []


# --------------------------------------------------------------------------- workouts


@pytest.mark.req("FR-PLN-004")
def test_workout_detail_block_must_match_type() -> None:
    workout = _example("workout.strength.example.json")
    workout["endurance"] = _example("workout.endurance.example.json")["endurance"]
    assert _errors("workout", workout)


@pytest.mark.req("FR-PLN-005")
def test_every_endurance_step_carries_an_rpe_target() -> None:
    workout = _example("workout.endurance.example.json")
    del workout["endurance"]["warmup"][0]["target"]["rpe"]
    assert _errors("workout", workout)


@pytest.mark.req("FR-STR-002")
def test_strength_uses_controlled_muscle_vocabulary() -> None:
    workout = _example("workout.strength.example.json")
    workout["strength"]["exercises"][0]["primaryMuscles"] = ["BICEPS_FEMORIS"]
    assert _errors("workout", workout)


@pytest.mark.req("FR-STR-003")
def test_exercise_identity_is_a_canonical_id_not_free_text() -> None:
    workout = _example("workout.strength.example.json")
    workout["strength"]["exercises"][0]["exerciseId"] = "Back Squat"
    assert _errors("workout", workout)


@pytest.mark.req("FR-STR-004")
@pytest.mark.parametrize("field", ["sets", "reps", "load", "restSeconds", "primaryMuscles"])
def test_strength_prescription_requires_dose_and_targets(field: str) -> None:
    workout = _example("workout.strength.example.json")
    del workout["strength"]["exercises"][0][field]
    assert _errors("workout", workout)


@pytest.mark.req("FR-STR-004", "FR-STR-007")
def test_strength_prescription_states_expected_interference() -> None:
    workout = _example("workout.strength.example.json")
    del workout["strength"]["expectedInterference"]
    assert _errors("workout", workout)


@pytest.mark.req("FR-TEN-001")
@pytest.mark.parametrize("field", ["recreational", "countsAsTrainingLoad"])
def test_planned_tennis_is_recreational_and_counts_as_load(field: str) -> None:
    plan = _example("training-plan.example.json")
    tennis = plan["weeks"][0]["days"]["wednesday"][0]
    tennis["tennis"][field] = False
    assert _errors("workout", tennis)


@pytest.mark.req("FR-ONB-005")
def test_maximal_evaluation_requires_opt_in() -> None:
    evaluation: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "id": "0193b2a0-0000-7000-8000-000000000499",
        "type": "EVALUATION",
        "trainingDay": "2026-10-30",
        "order": 0,
        "isKeyWorkout": False,
        "objective": "Field test",
        "explanation": {"what": "Field test", "why": "Establish an intensity anchor"},
        "evaluation": {
            "sport": "RUNNING",
            "protocolId": "eval.time_trial_20min",
            "description": "20-minute time trial",
            "submaximal": False,
            "requiresOptIn": False,
            "safetyNotes": ["Stop on red-flag symptoms."],
        },
    }
    assert _errors("workout", evaluation)
    evaluation["evaluation"]["requiresOptIn"] = True
    assert _errors("workout", evaluation) == []


# --------------------------------------------------------------------------- training plans


@pytest.mark.req("FR-ONB-004")
def test_provisional_plan_must_state_assumptions() -> None:
    plan = _example("training-plan.example.json")
    plan["assumptions"] = []
    assert _errors("training-plan", plan)


@pytest.mark.req("FR-PLN-002")
def test_plan_version_requires_content_hash_and_lineage() -> None:
    for field in ("contentHash", "parentVersion", "version"):
        plan = _example("training-plan.example.json")
        del plan[field]
        assert _errors("training-plan", plan), field


@pytest.mark.req("FR-PLN-002")
def test_plan_status_is_restricted() -> None:
    plan = _example("training-plan.example.json")
    plan["status"] = "DELETED"
    assert _errors("training-plan", plan)


@pytest.mark.req("FR-PLN-001")
def test_plan_phase_is_restricted() -> None:
    plan = _example("training-plan.example.json")
    plan["phases"][0]["phase"] = "PEAK"
    assert _errors("training-plan", plan)


@pytest.mark.req("FR-MET-006")
def test_weekly_external_load_is_keyed_by_sport() -> None:
    plan = _example("training-plan.example.json")
    plan["weeks"][0]["estimatedLoad"]["bySport"]["ALL_SPORTS"] = {
        "durationSeconds": 15300,
        "distanceMeters": 42000,
    }
    assert _errors("training-plan", plan)


# --------------------------------------------------------------------------- insights


@pytest.mark.req("FR-INS-003")
def test_insight_cannot_assert_causation() -> None:
    insight = _example("insight.example.json")
    insight["relationship"] = "CAUSAL"
    assert _errors("insight", insight)


@pytest.mark.req("FR-INS-003")
def test_association_requires_sample_size_and_window() -> None:
    insight = _example("insight.example.json")
    insight["relationship"] = "ASSOCIATION"
    assert _errors("insight", insight)
    insight["association"] = {
        "variables": ["rec.sleep_quality", "run.hr_drift"],
        "sampleSize": 18,
        "windowDays": 42,
    }
    assert _errors("insight", insight) == []


@pytest.mark.req("FR-INS-001")
def test_insight_requires_evidence_and_observation() -> None:
    insight = _example("insight.example.json")
    insight["evidence"] = []
    assert _errors("insight", insight)
    insight = _example("insight.example.json")
    del insight["observation"]
    assert _errors("insight", insight)


# --------------------------------------------------------------------------- consent


@pytest.mark.req("PRIV-CON-001")
def test_granted_consent_cannot_be_revoked_in_same_record() -> None:
    consent = _example("consent.grant.example.json")
    consent["revokedAt"] = "2026-10-25T10:00:00Z"
    assert _errors("consent", consent)


@pytest.mark.req("PRIV-CON-001")
def test_revocation_requires_revocation_time() -> None:
    consent = _example("consent.revocation.example.json")
    consent["revokedAt"] = None
    assert _errors("consent", consent)


@pytest.mark.req("PRIV-CON-001", "PRIV-CON-003")
def test_consent_types_include_ai_processing_and_are_closed() -> None:
    types = SCHEMAS["consent"]["properties"]["consentType"]["enum"]
    assert "AI_PROCESSING" in types
    consent = _example("consent.grant.example.json")
    consent["consentType"] = "EVERYTHING"
    assert _errors("consent", consent)


# --------------------------------------------------------------------------- raw sources


@pytest.mark.req("FR-ACT-002", "SEC-UPL-006")
@pytest.mark.parametrize(
    ("origin", "provider", "scan"),
    [
        ("GARMIN_PERSONAL_API", None, "NOT_APPLICABLE"),
        ("GARMIN_PERSONAL_API", "garmin", "NOT_SCANNED"),
        ("FILE_UPLOAD", "garmin", "NOT_SCANNED"),
        ("FILE_UPLOAD", None, "NOT_APPLICABLE"),
        ("MANUAL_ENTRY", None, "CLEAN"),
    ],
)
def test_raw_source_origin_rules(origin: str, provider: str | None, scan: str) -> None:
    raw = _example("raw-source.example.json")
    raw.update(origin=origin, provider=provider, malwareScan=scan)
    assert _errors("raw-source", raw)


@pytest.mark.req("SEC-UPL-005")
def test_raw_source_filename_is_capped() -> None:
    raw = _example("raw-source.example.json")
    raw["originalFilename"] = "x" * 256
    assert _errors("raw-source", raw)


# --------------------------------------------------------------------------- derived values


@pytest.mark.req("FR-MET-004")
def test_computed_value_needs_a_number_and_no_missing_inputs() -> None:
    value = _example("derived-metric-value.example.json")
    value["value"] = None
    assert _errors("derived-metric-value", value)
    value = _example("derived-metric-value.example.json")
    value["missingInputs"] = ["rpe"]
    assert _errors("derived-metric-value", value)


@pytest.mark.req("FR-MET-004", "FR-ACT-010")
def test_missing_input_is_unknown_and_explained() -> None:
    value = _example("derived-metric-value.example.json")
    value.update(
        status="MISSING_INPUT", value=None, missingInputs=["rpe"], inputs=value["inputs"][1:]
    )
    assert _errors("derived-metric-value", value)  # MISSING_DATA reason absent
    value["uncertaintyReasons"] = ["MISSING_DATA"]
    assert _errors("derived-metric-value", value) == []
    value["value"] = 310
    assert _errors("derived-metric-value", value)


@pytest.mark.req("FR-MET-004")
@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("ACTIVITY", "not-a-uuid"),
        ("DAY", "2026-13-45"),
        ("WEEK", "2026-W54"),
        ("EXERCISE", "Squat"),
    ],
)
def test_subject_keys_are_typed(kind: str, key: str) -> None:
    value = _example("derived-metric-value.example.json")
    value["subject"] = {"kind": kind, "key": key}
    assert _errors("derived-metric-value", value)


# --------------------------------------------------------------------------- coaching state


@pytest.mark.req("FR-ONB-006")
def test_coaching_state_conditions_are_closed_and_unique() -> None:
    state = _example("coaching-state.example.json")
    state["conditions"] = ["INTEGRATION_ERROR", "INTEGRATION_ERROR"]
    assert _errors("coaching-state", state)
    state["conditions"] = ["BEAST_MODE"]
    assert _errors("coaching-state", state)


# --------------------------------------------------------------------------- activity status (P5)


@pytest.mark.req("FR-ACT-007")
def test_merged_activity_names_its_target_and_only_merged_does() -> None:
    activity = _example("activity.example.json")
    activity["status"] = "MERGED"
    assert _errors("activity", activity)
    activity["mergedIntoId"] = "0193b2a0-0000-7000-8000-000000000102"
    assert _errors("activity", activity) == []
    activity["status"] = "PENDING_REVIEW"
    assert _errors("activity", activity)


@pytest.mark.req("FR-ACT-005", "FR-ACT-009")
@pytest.mark.parametrize("key", ["resolutionRulesVersion", "qualityRulesVersion"])
def test_activity_records_its_rule_versions(key: str) -> None:
    activity = _example("activity.example.json")
    del activity["provenance"][key]
    assert _errors("activity", activity)
    activity["provenance"][key] = "latest"
    assert _errors("activity", activity)


@pytest.mark.req("FR-ACT-007")
def test_source_link_names_the_part_of_its_raw_source() -> None:
    activity = _example("activity.example.json")
    del activity["provenance"]["sources"][0]["part"]
    assert _errors("activity", activity)
    activity["provenance"]["sources"][0]["part"] = -1
    assert _errors("activity", activity)


@pytest.mark.req("FR-ACT-009")
def test_heart_rate_sensor_is_a_closed_vocabulary() -> None:
    activity = _example("activity.example.json")
    activity["cardiovascular"]["hrSensor"] = "MAGIC_RING"
    assert _errors("activity", activity)
    activity["cardiovascular"]["hrSensor"] = None
    assert _errors("activity", activity) == []


# --------------------------------------------------------------------------- data overrides


@pytest.mark.req("FR-ACT-006")
@pytest.mark.parametrize(
    "field", ["timing.startTimeUtc", "provenance.sources", "training.aerobicEffect", "version"]
)
def test_only_correctable_fields_can_be_overridden(field: str) -> None:
    override = _example("data-override.example.json")
    override["field"] = field
    assert _errors("data-override", override)


@pytest.mark.req("FR-ACT-006")
@pytest.mark.parametrize("reason", [None, ""])
def test_override_requires_a_reason(reason: str | None) -> None:
    override = _example("data-override.example.json")
    override["reason"] = reason
    assert _errors("data-override", override)


@pytest.mark.req("FR-ACT-006")
@pytest.mark.parametrize(
    "key", ["originalValue", "correctedValue", "correctedAt", "correctedBy", "supersedesOverrideId"]
)
def test_override_records_original_correction_time_author_and_chain(key: str) -> None:
    override = _example("data-override.example.json")
    del override[key]
    assert _errors("data-override", override)


@pytest.mark.req("FR-ACT-006")
def test_override_values_are_scalars_or_unknown() -> None:
    override = _example("data-override.example.json")
    override["correctedValue"] = None
    assert _errors("data-override", override) == []
    override["correctedValue"] = {"meters": 10000}
    assert _errors("data-override", override)


# --------------------------------------------------------------------------- data quality issues


@pytest.mark.req("FR-ACT-007")
@pytest.mark.parametrize("key", ["relatedActivityId", "matchScore", "dedupRulesVersion"])
def test_ambiguous_match_issue_names_candidate_score_and_rules(key: str) -> None:
    issue = _example("data-quality-issue.ambiguous.example.json")
    del issue[key]
    assert _errors("data-quality-issue", issue)


@pytest.mark.req("FR-ACT-009")
def test_anomaly_issue_names_a_field_and_no_candidate() -> None:
    issue = _example("data-quality-issue.example.json")
    issue["field"] = None
    assert _errors("data-quality-issue", issue)
    issue["field"] = "cardiovascular.maxHr"
    issue["relatedActivityId"] = "0193b2a0-0000-7000-8000-000000000102"
    assert _errors("data-quality-issue", issue)


@pytest.mark.req("FR-ACT-009")
def test_issue_status_and_resolution_agree() -> None:
    issue = _example("data-quality-issue.example.json")
    issue["resolution"] = "ACKNOWLEDGED"
    assert _errors("data-quality-issue", issue)
    issue["status"] = "CLOSED"
    assert _errors("data-quality-issue", issue)
    issue["closedAt"] = "2026-10-21T09:00:00Z"
    assert _errors("data-quality-issue", issue) == []
    issue["resolution"] = "MERGED"
    assert _errors("data-quality-issue", issue)


# --------------------------------------------------------------------------- source observations


@pytest.mark.req("FR-ACT-005")
@pytest.mark.parametrize("kind", ["FIT_FILE", "OTHER_FILE"])
def test_only_athlete_entries_carry_subjective_values(kind: str) -> None:
    observation = _example("source-observation.example.json")
    observation["sourceKind"] = kind
    observation["values"]["subjective.rpe"] = 6
    assert _errors("source-observation", observation)


@pytest.mark.req("FR-ACT-005")
def test_athlete_entry_may_carry_subjective_values() -> None:
    observation = _example("source-observation.example.json")
    observation.update(origin="MANUAL_ENTRY", sourceKind="ATHLETE_ENTRY", streams=[])
    observation["values"]["subjective.rpe"] = 6
    assert _errors("source-observation", observation) == []


@pytest.mark.req("FR-ACT-007")
@pytest.mark.parametrize("kind", ["FIT_FILE", "OTHER_FILE", "ATHLETE_ENTRY"])
def test_only_provider_records_carry_provider_identifiers(kind: str) -> None:
    observation = _example("source-observation.example.json")
    origin = "MANUAL_ENTRY" if kind == "ATHLETE_ENTRY" else "FILE_UPLOAD"
    observation.update(origin=origin, sourceKind=kind, streams=[])
    observation["providerActivityId"] = "synthetic-1001"
    assert _errors("source-observation", observation)


@pytest.mark.req("FR-ACT-001", "FR-ACT-003")
@pytest.mark.parametrize("path", ["classification.sport", "timing.startTimeUtc"])
def test_observation_requires_sport_and_start(path: str) -> None:
    observation = _example("source-observation.example.json")
    del observation["values"][path]
    assert _errors("source-observation", observation)


@pytest.mark.req("FR-ACT-001")
@pytest.mark.parametrize(
    "path", ["timing.trainingDay", "performance.averagePaceSecondsPerKm", "isAdmin"]
)
def test_observation_cannot_provide_computed_or_unknown_fields(path: str) -> None:
    observation = _example("source-observation.example.json")
    observation["values"][path] = "2026-10-20"
    assert _errors("source-observation", observation)


@pytest.mark.req("FR-ACT-007")
def test_provider_record_observation_names_provider_and_record() -> None:
    observation = _example("source-observation.example.json")
    observation.update(origin="GARMIN_PERSONAL_API", sourceKind="PROVIDER_RECORD")
    assert _errors("source-observation", observation)
    observation.update(provider="garmin", providerActivityId="synthetic-1001")
    assert _errors("source-observation", observation) == []
    observation["origin"] = "FILE_UPLOAD"
    assert _errors("source-observation", observation)


# --------------------------------------------------------------------------- P6: file ingestion


@pytest.mark.req("INT-FIL-004")
def test_failed_import_issue_names_its_ingestion_not_an_activity() -> None:
    issue = _example("data-quality-issue.import-failed.example.json")
    del issue["ingestionId"]
    assert _errors("data-quality-issue", issue)
    issue = _example("data-quality-issue.import-failed.example.json")
    issue["activityId"] = "0193b2a0-0000-7000-8000-000000000101"
    assert _errors("data-quality-issue", issue)
    issue = _example("data-quality-issue.import-failed.example.json")
    issue.update(status="CLOSED", closedAt="2026-10-21T09:00:00Z", resolution="MERGED")
    assert _errors("data-quality-issue", issue)
    issue["resolution"] = "ACKNOWLEDGED"
    assert _errors("data-quality-issue", issue) == []


@pytest.mark.req("FR-ACT-009")
def test_activity_issues_still_name_their_activity() -> None:
    issue = _example("data-quality-issue.example.json")
    del issue["activityId"]
    assert _errors("data-quality-issue", issue)
    issue = _example("data-quality-issue.example.json")
    issue["ingestionId"] = "0193b2a0-0000-7000-8000-000000000701"
    assert _errors("data-quality-issue", issue)


@pytest.mark.req("FR-ACT-005", "INT-FIL-001")
def test_csv_athlete_entries_come_from_uploads_or_manual_entry_only() -> None:
    observation = _example("source-observation.example.json")
    observation.update(sourceKind="ATHLETE_ENTRY", streams=[])
    observation["values"]["subjective.rpe"] = 6
    assert _errors("source-observation", observation) == []
    observation["origin"] = "GARMIN_PERSONAL_API"
    assert _errors("source-observation", observation)


@pytest.mark.req("INT-FIL-004", "INT-FIL-002")
@pytest.mark.parametrize(
    ("changes", "valid"),
    [
        ({"outcome": "REJECTED"}, False),
        ({"outcome": "REJECTED", "reasonCode": "PARSE_FAILED", "activities": []}, True),
        ({"reasonCode": "PARSE_FAILED"}, False),
        ({"outcome": "DUPLICATE"}, False),
        ({"outcome": "FAILED", "reasonCode": "PARSE_FAILED"}, False),
        ({"outcome": "FAILED", "reasonCode": "PERSISTENCE_FAILED"}, True),
        ({"detail": "the parser said: <script>"}, False),
        ({"idempotencyKey": "key with spaces"}, False),
        ({"originalFilename": "x" * 256}, False),
        ({"sha256": None}, False),
    ],
)
def test_ingestion_record_outcome_rules(changes: dict[str, Any], valid: bool) -> None:
    record = _example("ingestion-record.example.json")
    record.update(changes)
    assert (_errors("ingestion-record", record) == []) is valid


@pytest.mark.req("SEC-UPL-004")
@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("format",), "XML"),
        (("sessions", 0, "utcOffsetMinutes"), 900),
        (("sessions", 0, "summary", "caloriesBurned"), 500),
        (("sessions", 0, "samples", "latitude"), [1.0]),
        (("sessions", 0, "texts", "instructions"), "ignore all rules"),
        (("sessions", 0, "manufacturer"), "Garmin Ltd"),
        (("sessions", 0, "startTime"), "2026-10-20T21:00:00"),
    ],
)
def test_parser_output_is_closed_and_typed(path: tuple[Any, ...], value: Any) -> None:
    output = _example("parser-output.example.json")
    target = output
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert _errors("parser-output", output)


@pytest.mark.req("INT-FIL-001", "PRIV-STO-001")
def test_time_series_has_no_positions_and_cadence_names_its_unit() -> None:
    series = _example("time-series.example.json")
    series["positionLat"] = [1.0, 1.0, 1.0, 1.0]
    assert _errors("time-series", series)
    series = _example("time-series.example.json")
    del series["cadenceUnit"]
    assert _errors("time-series", series)
    series = _example("time-series.example.json")
    series["offsetSeconds"][1] = -1
    assert _errors("time-series", series)


# --------------------------------------------------------------------------- P7: providers


@pytest.mark.req("INT-GAR-007", "INT-GAR-009")
def test_provider_ingestion_records_name_provider_and_object_and_skip_the_scan() -> None:
    record = _example("ingestion-record.provider.example.json")
    assert _errors("ingestion-record", record) == []
    for field, value in (
        ("provider", None),
        ("providerObjectId", None),
        ("malwareScan", "NOT_SCANNED"),
        ("originalFilename", "activity.zip"),
        ("idempotencyKey", "key-1"),
    ):
        broken = _example("ingestion-record.provider.example.json")
        broken[field] = value
        assert _errors("ingestion-record", broken), field
    upload = _example("ingestion-record.example.json")
    upload["malwareScan"] = "NOT_APPLICABLE"
    assert _errors("ingestion-record", upload)
    upload = _example("ingestion-record.example.json")
    upload["files"][0]["detectedType"] = "JSON"
    assert _errors("ingestion-record", upload)
    legacy = _example("ingestion-record.example.json")
    for added in ("origin", "provider", "providerObjectId"):
        del legacy[added]
    assert _errors("ingestion-record", legacy) == []


@pytest.mark.req("INT-GAR-009")
def test_only_provider_payloads_have_a_request_window() -> None:
    raw = _example("raw-source.example.json")
    raw["requestWindow"] = {"start": "2026-10-01", "end": "2026-10-07"}
    assert _errors("raw-source", raw)
    raw.update(
        origin="GARMIN_PERSONAL_API",
        provider="garmin",
        malwareScan="NOT_APPLICABLE",
        detectedType="JSON",
        originalFilename=None,
    )
    assert _errors("raw-source", raw) == []


@pytest.mark.req("INT-GAR-007", "FR-ACT-002")
def test_a_downloaded_file_is_a_file_observation_without_a_record_id() -> None:
    observation = _example("source-observation.example.json")
    observation.update(sourceKind="FIT_FILE", origin="GARMIN_PERSONAL_API")
    assert _errors("source-observation", observation) == []
    observation.update(provider="garmin", providerActivityId="21004587311")
    assert _errors("source-observation", observation)


# --------------------------------------------------------------------------- P8: metrics engine


@pytest.mark.req("FR-MET-004", "NFR-REL-004")
def test_a_subject_dimension_is_a_sport_or_a_zone() -> None:
    value = _example("derived-metric-value.example.json")
    for dimension in ("RUNNING", "Z1", "Z10"):
        value["subject"]["dimension"] = dimension
        assert _errors("derived-metric-value", value) == [], dimension
    for dimension in ("Z0", "Z11", "running", "SKIING", ""):
        value["subject"]["dimension"] = dimension
        assert _errors("derived-metric-value", value), dimension


@pytest.mark.req("NFR-TRC-001")
def test_evidence_may_name_a_zone_model() -> None:
    value = _example("derived-metric-value.example.json")
    value["inputs"][0]["evidence"]["kind"] = "HR_ZONE_MODEL"
    assert _errors("derived-metric-value", value) == []
    value["inputs"][0]["evidence"]["kind"] = "PROFILE"
    assert _errors("derived-metric-value", value)


@pytest.mark.req("FR-MET-004")
def test_retired_values_and_missing_contributions() -> None:
    retired = _example("derived-metric-value.example.json")
    retired.update(
        status="NOT_APPLICABLE", value=None, inputs=[], inputConfidence=None, uncertaintyReasons=[]
    )
    assert _errors("derived-metric-value", retired) == []
    retired["inputConfidence"] = "HIGH"
    assert _errors("derived-metric-value", retired)
    partial = _example("derived-metric-value.example.json")
    evidence = {"kind": "ACTIVITY", "id": "0193b2a0-0000-7000-8000-000000000102"}
    partial["missingContributions"] = [evidence]
    assert _errors("derived-metric-value", partial)  # only an unknown total names them
    partial.update(
        status="MISSING_INPUT",
        value=None,
        missingInputs=["rpe"],
        uncertaintyReasons=["MISSING_DATA"],
    )
    assert _errors("derived-metric-value", partial) == []
