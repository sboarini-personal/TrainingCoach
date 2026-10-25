"""Weekly volume iterates logical ACTIVE activities only (FR-ACT-008, FR-ACT-011)."""

import itertools
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from coach.domain.activity.activity import ActivityStatus
from coach.domain.activity.observation import SourceKind
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.metrics.catalog import ACTIVITY_BINDINGS, SRPE_LOAD, ActivityBinding
from coach.metrics.volume import WeeklyVolume, weekly_volume
from samples import START, sample_activity, sample_observation, tennis_values

SECOND = UUID("0193b2a0-0000-7000-8000-000000000102")
THIRD = UUID("0193b2a0-0000-7000-8000-000000000103")


@pytest.mark.req("FR-ACT-008")
@pytest.mark.acceptance("AT-ACT-001")
def test_linked_sources_count_once_through_their_activity() -> None:
    enriched = sample_activity(sample_observation(SourceKind.PROVIDER_RECORD), sample_observation())
    volume = weekly_volume([enriched])
    assert volume == {
        ("2026-W43", Sport.RUNNING): WeeklyVolume(
            "2026-W43", Sport.RUNNING, 1, 3720.0, 10050.5, 0, 0
        )
    }


@pytest.mark.req("FR-ACT-008", "FR-ACT-011")
def test_pending_and_merged_activities_are_excluded_and_unknowns_counted() -> None:
    run = sample_activity()
    pending = replace(sample_activity(activity_id=SECOND), status=ActivityStatus.PENDING_REVIEW)
    merged = replace(
        sample_activity(activity_id=THIRD),
        status=ActivityStatus.MERGED,
        merged_into=run.activity_id,
    )
    tennis = sample_activity(
        sample_observation(
            values=tennis_values(**{"timing.startTimeUtc": START + timedelta(days=7)})
        ),
        activity_id=SECOND,
    )
    volume = weekly_volume([run, pending, merged, tennis])
    assert volume[("2026-W43", Sport.RUNNING)].activities == 1
    unplanned = volume[("2026-W44", Sport.TENNIS)]
    assert (unplanned.activities, unplanned.duration_seconds) == (1, 5400.0)
    assert (unplanned.distance_meters, unplanned.unknown_distance) == (0.0, 1)


@pytest.mark.req("FR-MET-001", "FR-ACT-006")
def test_activity_bindings_map_every_metric_input_to_a_field() -> None:
    (binding,) = ACTIVITY_BINDINGS
    assert binding.definition is SRPE_LOAD
    assert dict(binding.inputs) == {
        "rpe": "subjective.rpe",
        "session_duration_seconds": "timing.elapsedSeconds",
    }
    with pytest.raises(DomainValidationError):
        ActivityBinding(SRPE_LOAD, (("rpe", "subjective.rpe"),))
    with pytest.raises(DomainValidationError):
        ActivityBinding(
            SRPE_LOAD,
            (("rpe", "subjective.rpe"), ("session_duration_seconds", "timing.duration")),
        )


@pytest.mark.req("FR-ACT-008")
def test_totals_do_not_depend_on_the_order_of_activities() -> None:
    # Plain left-to-right float addition of these values depends on the order.
    durations = (599.7, 600.4, 602.5)
    activities = [
        sample_activity(
            sample_observation(
                raw_source_id=UUID(int=index + 1),
                updates={
                    "timing.startTimeUtc": START + timedelta(hours=index),
                    "timing.elapsedSeconds": duration,
                    "timing.movingSeconds": None,
                },
            ),
            activity_id=UUID(int=100 + index),
        )
        for index, duration in enumerate(durations)
    ]
    totals = {
        weekly_volume(order)[("2026-W43", Sport.RUNNING)].duration_seconds
        for order in itertools.permutations(activities)
    }
    assert totals == {1802.6}
