"""Tennis load (tennis spec 4; AT-TEN-001). Synthetic data only."""

from __future__ import annotations

from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService
from coach.domain.activity.observation import SourceKind
from coach.domain.common.quality import UncertaintyReason
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, MetricSubject
from coach.metrics.tennis import REGIONAL_LOAD_HOURS, TENNIS_REGIONS, LoadLevel, load_level
from samples import sample_observation, tennis_values

WEEK = "2026-W43"


@pytest.mark.req("FR-TEN-003")
@pytest.mark.parametrize(
    ("minutes", "rpe", "match", "expected"),
    [
        (30, 5, False, LoadLevel.LOW),
        (60, 5, False, LoadLevel.MODERATE),
        (120, 5, False, LoadLevel.HIGH),
        (60, 8, False, LoadLevel.HIGH),  # hard: one up
        (60, 2, False, LoadLevel.LOW),  # easy: one down
        (30, 5, True, LoadLevel.MODERATE),  # a match: one up
        (30, 2, False, LoadLevel.LOW),  # never below LOW
        (120, 9, True, LoadLevel.HIGH),  # never above HIGH
    ],
)
def test_the_level_rises_with_duration_effort_and_matches(
    minutes: float, rpe: float, match: bool, expected: LoadLevel
) -> None:
    assert load_level(minutes, rpe, match=match) is expected


def tennis(world: World, **changes: object) -> UUID:
    """A tennis session the athlete entered (subjective fields come from athlete entries)."""
    observation = sample_observation(SourceKind.ATHLETE_ENTRY, values=tennis_values(**changes))
    return world.ingest(observation).activity.activity_id


def load_of(world: World, activity_id: UUID) -> DerivedMetricValue:
    value = world.metrics.current(
        SCOPE, "ten.neuromuscular_load", MetricSubject.activity(activity_id)
    )
    assert value is not None
    return value


@pytest.mark.req("FR-TEN-001", "FR-TEN-003", "FR-MET-006")
@pytest.mark.acceptance("AT-TEN-001")
def test_two_hours_of_demanding_tennis_count_as_load() -> None:
    world = World()
    activity_id = tennis(
        world,
        **{
            "timing.elapsedSeconds": 7200.0,
            "subjective.rpe": 8.0,
            "classification.subtype": "MATCH",
        },
    )
    load = load_of(world, activity_id)
    assert load.value == float(LoadLevel.HIGH)
    assert set(TENNIS_REGIONS) == {
        "calves",
        "soleus",
        "achilles_region",
        "knees",
        "adductors",
        "shoulders",
        "elbows",
    }
    assert REGIONAL_LOAD_HOURS == 48
    analytics = AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
    )
    report = analytics.week(SCOPE, WEEK)
    (sport,) = report.sports
    assert sport.totals["any.srpe_load_total"].value.value == pytest.approx(960.0)
    assert report.cross_sport["any.srpe_load_total"].value.value == pytest.approx(960.0)


@pytest.mark.req("FR-TEN-002", "FR-PRF-002")
def test_without_rpe_the_level_is_unknown_and_an_unknown_type_is_flagged() -> None:
    world = World()
    no_rpe = tennis(world, **{"subjective.rpe": None})
    assert load_of(world, no_rpe).status is DerivedStatus.MISSING_INPUT
    other = World()
    unknown = tennis(other, **{"classification.subtype": None, "subjective.rpe": 6.0})
    value = load_of(other, unknown)
    assert value.status is DerivedStatus.COMPUTED
    assert UncertaintyReason.MISSING_DATA in value.uncertainty_reasons
