"""Contract tests for spec/contracts: schema validity, examples, and rules the spec forbids.

Each negative test mutates a valid example and asserts the schema rejects the result, so the
contract itself enforces the cited requirement.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from specindex import SPEC_DIR

CONTRACTS = SPEC_DIR / "contracts"
EXAMPLES = CONTRACTS / "examples"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMAS: dict[str, dict[str, Any]] = {
    path.name.removesuffix(".schema.json"): _load(path)
    for path in sorted(CONTRACTS.glob("*.schema.json"))
}
REGISTRY: Registry[Any] = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)
EXAMPLE_FILES = sorted(EXAMPLES.glob("*.json"))


def _errors(schema_name: str, instance: Any) -> list[str]:
    validator = Draft202012Validator(
        SCHEMAS[schema_name],
        registry=REGISTRY,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    return [error.message for error in validator.iter_errors(instance)]


def _example(filename: str) -> dict[str, Any]:
    data: dict[str, Any] = _load(EXAMPLES / filename)
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
    expected = {"common", "activity", "workout", "training-plan", "insight", "consent"}
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
