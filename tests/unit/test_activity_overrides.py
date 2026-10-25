"""DataOverride: append-only corrections applied at read time (FR-ACT-006, AT-RAW-001)."""

from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.domain.activity.activity import Activity
from coach.domain.activity.overrides import (
    DataOverride,
    EntityType,
    StaleOverrideError,
    apply_overrides,
    check_new_override,
    current_overrides,
)
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import TrainingDayBasis
from samples import ACTIVITY, FIT_SOURCE, NOW, OTHER_ATHLETE, USER, sample_activity

FIRST = UUID("0193b2a0-0000-7000-8000-000000000501")
SECOND = UUID("0193b2a0-0000-7000-8000-000000000502")
THIRD = UUID("0193b2a0-0000-7000-8000-000000000503")


def _override(
    field: str = "distance.meters",
    corrected: object = 10000.0,
    *,
    override_id: UUID = FIRST,
    supersedes: UUID | None = None,
    original: object = 10050.5,
    **changes: Any,
) -> DataOverride:
    values: dict[str, Any] = {
        "override_id": override_id,
        "athlete_id": sample_activity().athlete_id,
        "entity_type": EntityType.ACTIVITY,
        "entity_id": ACTIVITY,
        "field": field,
        "original_value": original,
        "corrected_value": corrected,
        "reason": "Measured course",
        "corrected_at": NOW,
        "corrected_by": USER,
        "supersedes_override_id": supersedes,
    }
    values.update(changes)
    return DataOverride(**values)


@pytest.mark.req("FR-ACT-006")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timing.startTimeUtc", NOW),
        ("training.load.internal.sessionRpeLoad", 300.0),
        ("distance.meters", -5.0),
        ("classification.sport", None),
        ("timing.trainingDay", None),
        ("cardiovascular.averageHr", 148.7),
        ("subjective.rpe", 11.0),
    ],
)
def test_only_valid_corrections_of_correctable_fields_are_accepted(
    field: str, value: object
) -> None:
    with pytest.raises(DomainValidationError):
        _override(field, value, original=None)


@pytest.mark.req("FR-ACT-006", "SEC-INJ-001")
def test_override_requires_a_reason_and_cleans_it() -> None:
    with pytest.raises(DomainValidationError):
        _override(reason="  ")
    assert _override(reason="GPS\x00 lost").reason == "GPS lost"
    with pytest.raises(DomainValidationError):
        _override(supersedes=FIRST)


@pytest.mark.req("FR-ACT-006")
def test_corrections_form_one_chain_per_field() -> None:
    first = _override()
    check_new_override([], first)
    second = _override(corrected=10020.0, override_id=SECOND, supersedes=FIRST)
    check_new_override([first], second)
    with pytest.raises(StaleOverrideError):
        check_new_override([first], _override(override_id=SECOND))
    with pytest.raises(StaleOverrideError):
        check_new_override([first, second], _override(override_id=THIRD, supersedes=FIRST))
    with pytest.raises(StaleOverrideError):
        check_new_override([first], _override(override_id=FIRST, supersedes=SECOND))
    other_field = _override("elevation.gainMeters", 90.0, override_id=THIRD, original=85.0)
    check_new_override([first, second], other_field)
    assert current_overrides([first, second, other_field]) == {
        "distance.meters": second,
        "elevation.gainMeters": other_field,
    }


@pytest.mark.req("FR-ACT-006", "SEC-IDOR-001")
def test_overrides_of_other_entities_are_never_mixed() -> None:
    first = _override()
    with pytest.raises(DomainValidationError):
        check_new_override([first], _override(override_id=SECOND, entity_id=FIRST))
    stranger = _override(athlete_id=OTHER_ATHLETE)
    with pytest.raises(DomainValidationError):
        apply_overrides(sample_activity(), [stranger])


@pytest.mark.req("FR-ACT-002", "FR-ACT-006")
@pytest.mark.acceptance("AT-RAW-001")
def test_effective_value_is_corrected_while_normalized_is_untouched() -> None:
    activity = sample_activity()
    before = dict(activity.values)
    effective = apply_overrides(activity, [_override()])
    assert effective.values["distance.meters"] == 10000.0
    origin = effective.provenance["distance.meters"]
    assert (origin.source_id, origin.rule, origin.override_id) == (FIT_SOURCE, "OVERRIDE", FIRST)
    assert dict(activity.values) == before
    assert activity.provenance["distance.meters"].rule == "TOTALS_PREFER_FIT"


@pytest.mark.req("FR-ACT-006")
def test_last_correction_of_the_chain_applies_and_revert_restores_original() -> None:
    first = _override()
    revert = _override(corrected=10050.5, override_id=SECOND, supersedes=FIRST)
    effective = apply_overrides(sample_activity(), [revert, first])
    assert effective.values["distance.meters"] == 10050.5
    assert effective.provenance["distance.meters"].override_id == SECOND


@pytest.mark.req("FR-ACT-004", "FR-ACT-006")
def test_corrected_training_day_is_configured() -> None:
    day = _override("timing.trainingDay", date(2026, 10, 21), original=date(2026, 10, 20))
    effective = apply_overrides(sample_activity(), [day])
    assert effective.values["timing.trainingDay"] == date(2026, 10, 21)
    assert effective.values["timing.trainingDayBasis"] is TrainingDayBasis.CONFIGURED


@pytest.mark.req("FR-ACT-006")
def test_sport_correction_drops_a_subtype_that_no_longer_fits() -> None:
    sport = _override("classification.sport", Sport.TENNIS, original=Sport.RUNNING)
    effective = apply_overrides(sample_activity(), [sport])
    assert effective.values["classification.sport"] is Sport.TENNIS
    assert effective.values["classification.subtype"] is None
    assert "performance.averagePaceSecondsPerKm" not in effective.values
    subtype = _override("classification.subtype", "MATCH", override_id=SECOND, original="ROAD")
    both = apply_overrides(sample_activity(), [sport, subtype])
    assert both.values["classification.subtype"] == "MATCH"


@pytest.mark.req("FR-ACT-006")
def test_pace_follows_a_corrected_speed_and_unknown_is_allowed() -> None:
    speed = _override("performance.averageSpeedMps", 2.5, original=2.753)
    effective = apply_overrides(sample_activity(), [speed])
    assert effective.values["performance.averagePaceSecondsPerKm"] == pytest.approx(400.0)
    assert effective.provenance["performance.averagePaceSecondsPerKm"].override_id == FIRST
    unknown = apply_overrides(
        sample_activity(), [_override("cardiovascular.maxHr", None, original=171)]
    )
    assert unknown.values["cardiovascular.maxHr"] is None


@pytest.mark.req("FR-ACT-006")
def test_effective_values_form_a_valid_activity() -> None:
    activity: Activity = sample_activity()
    effective = apply_overrides(
        activity, [_override("timing.elapsedSeconds", 3600.0, original=3720.0)]
    )
    rebuilt = replace(activity, values=effective.values, provenance=effective.provenance)
    assert rebuilt.number("timing.elapsedSeconds") == 3600.0
    assert rebuilt.imported_at + timedelta(0) == activity.imported_at
