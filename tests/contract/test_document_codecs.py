"""Domain <-> contract documents: lossless round trips and canonical examples (NFR-REL-004)."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from coach.application.contracts import (
    ACTIVITY_CODEC,
    ALL_CODECS,
    AVAILABILITY_CODEC,
    COACHING_STATE_CODEC,
    CONSENT_LEDGER_CODEC,
    CREDENTIAL_RECORD_CODEC,
    DATA_OVERRIDE_CODEC,
    DATA_QUALITY_ISSUE_CODEC,
    DERIVED_VALUE_CODEC,
    GOAL_CODEC,
    OBSERVATION_CODEC,
    PROFILE_CODEC,
    RAW_SOURCE_CODEC,
    SPORT_EVENT_CODEC,
    DocumentCodec,
    UnsupportedSchemaVersionError,
)
from coach.domain.activity.activity import ActivityStatus
from coach.domain.activity.issues import DataQualityIssue, IssueResolution
from coach.domain.activity.observation import SourceKind
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.coaching.state import CoachingState
from coach.domain.consent.ledger import ConsentLedger
from samples import (
    ACTIVITY,
    ATHLETE,
    NOW,
    full_profile,
    rich_activity,
    rich_observation,
    sample_activity,
    sample_availability,
    sample_coaching_state,
    sample_consent_ledger,
    sample_credential_record,
    sample_derived_value,
    sample_event,
    sample_goal,
    sample_issue,
    sample_observation,
    sample_override,
    sample_raw_source,
    tennis_values,
)
from schemas import EXAMPLES_DIR, load_json, validation_errors

CODECS: list[DocumentCodec[Any]] = list(ALL_CODECS)
ROUND_TRIPS: list[tuple[DocumentCodec[Any], Any]] = [
    (PROFILE_CODEC, full_profile()),
    (PROFILE_CODEC, AthleteProfile.new(ATHLETE)),
    (AVAILABILITY_CODEC, sample_availability()),
    (GOAL_CODEC, sample_goal()),
    (SPORT_EVENT_CODEC, sample_event()),
    (CONSENT_LEDGER_CODEC, sample_consent_ledger()),
    (CONSENT_LEDGER_CODEC, ConsentLedger.new(ATHLETE)),
    (COACHING_STATE_CODEC, sample_coaching_state()),
    (COACHING_STATE_CODEC, CoachingState.new(ATHLETE)),
    (RAW_SOURCE_CODEC, sample_raw_source()),
    (DERIVED_VALUE_CODEC, sample_derived_value()),
    (CREDENTIAL_RECORD_CODEC, sample_credential_record()),
    (ACTIVITY_CODEC, sample_activity()),
    (ACTIVITY_CODEC, rich_activity()),
    (
        ACTIVITY_CODEC,
        replace(
            sample_activity(),
            status=ActivityStatus.MERGED,
            merged_into=UUID("0193b2a0-0000-7000-8000-000000000102"),
        ),
    ),
    (OBSERVATION_CODEC, sample_observation()),
    (OBSERVATION_CODEC, rich_observation()),
    (
        OBSERVATION_CODEC,
        sample_observation(
            SourceKind.ATHLETE_ENTRY, values=tennis_values(**{"subjective.rpe": 7.0})
        ),
    ),
    (DATA_OVERRIDE_CODEC, sample_override()),
    (
        DATA_OVERRIDE_CODEC,
        sample_override(
            field="timing.trainingDay",
            original_value=date(2026, 10, 20),
            corrected_value=date(2026, 10, 21),
            supersedes_override_id=ACTIVITY,
        ),
    ),
    (
        DATA_OVERRIDE_CODEC,
        sample_override(
            field="classification.sport", original_value="RUNNING", corrected_value="TENNIS"
        ),
    ),
    (DATA_OVERRIDE_CODEC, sample_override(original_value=None, corrected_value=None)),
    (DATA_QUALITY_ISSUE_CODEC, sample_issue()),
    (
        DATA_QUALITY_ISSUE_CODEC,
        DataQualityIssue.for_ambiguous_match(
            issue_id=ACTIVITY,
            athlete_id=ATHLETE,
            activity_id=UUID("0193b2a0-0000-7000-8000-000000000102"),
            related_activity_id=ACTIVITY,
            match_score=0.72,
            dedup_rules_version="1.0.0",
            created_at=NOW,
        ).close(IssueResolution.KEPT_SEPARATE, at=NOW),
    ),
]


def _case_id(case: Any) -> str:
    return getattr(case, "kind", type(case).__name__)


@pytest.mark.req("NFR-REL-004", "FR-PRF-002")
@pytest.mark.parametrize(("codec", "entity"), ROUND_TRIPS, ids=_case_id)
def test_round_trip_is_lossless_and_valid(codec: DocumentCodec[Any], entity: Any) -> None:
    document = codec.dump(entity)
    assert validation_errors(codec.schema, document) == []
    stored = json.loads(json.dumps(document, allow_nan=False))
    assert codec.load(stored) == entity


@pytest.mark.req("NFR-REL-004")
@pytest.mark.parametrize("codec", CODECS, ids=_case_id)
def test_published_example_is_canonical(codec: DocumentCodec[Any]) -> None:
    example: dict[str, Any] = load_json(EXAMPLES_DIR / f"{codec.schema}.example.json")
    assert codec.dump(codec.load(example)) == example


@pytest.mark.req("NFR-REL-004")
@pytest.mark.parametrize("codec", CODECS, ids=_case_id)
def test_other_major_versions_are_refused_minor_versions_accepted(
    codec: DocumentCodec[Any],
) -> None:
    example: dict[str, Any] = load_json(EXAMPLES_DIR / f"{codec.schema}.example.json")
    with pytest.raises(UnsupportedSchemaVersionError):
        codec.load({**example, "schemaVersion": "2.0.0"})
    assert codec.load({**example, "schemaVersion": "1.4.2"}) == codec.load(example)


def test_every_codec_has_a_contract_and_an_example() -> None:
    kinds = [codec.kind for codec in CODECS]
    assert len(kinds) == len(set(kinds))
    for codec in CODECS:
        assert Path(EXAMPLES_DIR / f"{codec.schema}.example.json").is_file()
