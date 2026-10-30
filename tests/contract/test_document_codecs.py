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
    INGESTION_RECORD_CODEC,
    INTEGRATION_STATUS_CODEC,
    LAPS_CODEC,
    LENGTHS_CODEC,
    OBSERVATION_CODEC,
    PROFILE_CODEC,
    RAW_SOURCE_CODEC,
    SETS_CODEC,
    SPORT_EVENT_CODEC,
    STRENGTH_SESSION_CODEC,
    TIME_SERIES_CODEC,
    DocumentCodec,
    UnsupportedSchemaVersionError,
)
from coach.domain.activity.activity import ActivityStatus
from coach.domain.activity.fields import CadenceUnit
from coach.domain.activity.issues import DataQualityIssue, IssueResolution
from coach.domain.activity.observation import SourceKind
from coach.domain.activity.segments import (
    Lap,
    LapIntensity,
    Laps,
    LapTrigger,
    Lengths,
    SwimLength,
)
from coach.domain.activity.streams import TimeSeries
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.coaching.state import CoachingState
from coach.domain.consent.ledger import ConsentLedger
from coach.domain.strength.exercise import ExerciseAlias
from coach.domain.strength.session import SetEntry, StrengthSession
from coach.domain.strength.sets import SetType, WatchSet, WatchSets
from samples import (
    ACTIVITY,
    ATHLETE,
    NOW,
    age_predicted_zones,
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
    sample_integration_status,
    sample_issue,
    sample_observation,
    sample_override,
    sample_raw_source,
    sample_total,
    tennis_values,
)
from schemas import EXAMPLES_DIR, load_json, validation_errors

CODECS: list[DocumentCodec[Any]] = list(ALL_CODECS)
ROUND_TRIPS: list[tuple[DocumentCodec[Any], Any]] = [
    (PROFILE_CODEC, full_profile()),
    (PROFILE_CODEC, AthleteProfile.new(ATHLETE)),
    (
        PROFILE_CODEC,
        full_profile().apply(ProfileUpdate(zone_models=(age_predicted_zones(),))),
    ),
    (
        PROFILE_CODEC,
        full_profile().apply(
            ProfileUpdate(
                exercise_aliases=(
                    ExerciseAlias("Búlgaras", "ex.bulgarian_split_squat"),
                    ExerciseAlias("Gemelos en máquina", "ex.seated_calf_raise"),
                )
            )
        ),
    ),
    (AVAILABILITY_CODEC, sample_availability()),
    (GOAL_CODEC, sample_goal()),
    (SPORT_EVENT_CODEC, sample_event()),
    (CONSENT_LEDGER_CODEC, sample_consent_ledger()),
    (CONSENT_LEDGER_CODEC, ConsentLedger.new(ATHLETE)),
    (COACHING_STATE_CODEC, sample_coaching_state()),
    (COACHING_STATE_CODEC, CoachingState.new(ATHLETE)),
    (RAW_SOURCE_CODEC, sample_raw_source()),
    (DERIVED_VALUE_CODEC, sample_derived_value()),
    (DERIVED_VALUE_CODEC, sample_total()),
    (DERIVED_VALUE_CODEC, sample_total(missing=True)),
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
        INGESTION_RECORD_CODEC,
        INGESTION_RECORD_CODEC.load(load_json(EXAMPLES_DIR / "ingestion-record.example.json")),
    ),
    (
        INGESTION_RECORD_CODEC,
        INGESTION_RECORD_CODEC.load(
            load_json(EXAMPLES_DIR / "ingestion-record.rejected.example.json")
        ),
    ),
    (
        INGESTION_RECORD_CODEC,
        INGESTION_RECORD_CODEC.load(
            load_json(EXAMPLES_DIR / "ingestion-record.provider.example.json")
        ),
    ),
    (
        TIME_SERIES_CODEC,
        TIME_SERIES_CODEC.load(load_json(EXAMPLES_DIR / "time-series.example.json")),
    ),
    (
        INTEGRATION_STATUS_CODEC,
        INTEGRATION_STATUS_CODEC.load(load_json(EXAMPLES_DIR / "integration-status.example.json")),
    ),
    (INTEGRATION_STATUS_CODEC, sample_integration_status()),
    (
        TIME_SERIES_CODEC,
        TimeSeries(start_time_utc=NOW, offset_seconds=(0.0, 1.5), power_watts=(None, 250.0)),
    ),
    (LAPS_CODEC, Laps(start_time_utc=NOW, laps=(Lap(start_offset_seconds=0.0),))),
    (
        LAPS_CODEC,
        Laps(
            start_time_utc=NOW,
            cadence_unit=CadenceUnit.RPM,
            laps=(
                Lap(
                    start_offset_seconds=0.0,
                    timer_seconds=300.0,
                    average_cadence=88.5,
                    average_power_watts=231.0,
                    intensity=LapIntensity.INTERVAL,
                    trigger=LapTrigger.POSITION,
                ),
            ),
        ),
    ),
    (LENGTHS_CODEC, Lengths(start_time_utc=NOW, lengths=(SwimLength(start_offset_seconds=0.0),))),
    (
        SETS_CODEC,
        WatchSets(
            start_time_utc=NOW,
            sets=(
                WatchSet(start_offset_seconds=0.0),
                WatchSet(
                    start_offset_seconds=30.0,
                    duration_seconds=42.5,
                    type=SetType.ACTIVE,
                    repetitions=0,
                    weight_kg=0.0,
                    exercise_id="ex.plank",
                    provider_exercise="fit:plank/plank",
                ),
            ),
        ),
    ),
    (STRENGTH_SESSION_CODEC, StrengthSession.new(ATHLETE, ACTIVITY).with_version(1)),
    (
        STRENGTH_SESSION_CODEC,
        StrengthSession.new(ATHLETE, ACTIVITY)
        .record(
            SetEntry(
                exercise_id="ex.back_squat",
                set_number=2,
                recorded_at=NOW,
                completed_reps=7,
                actual_load_kg=62.5,
                actual_rpe=8.5,
                actual_rir=1,
                target_reps=8,
                target_load_kg=60.0,
                target_rpe=8.0,
            )
        )
        .relabel("fit:squat", "ex.goblet_squat")
        .watch_sets(False)
        .with_version(4),
    ),
    (
        DATA_OVERRIDE_CODEC,
        sample_override(
            field="performance.powerSensor", original_value=None, corrected_value="POWER_METER"
        ),
    ),
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
