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
