"""Deduplication scoring (FR-ACT-007; spec/integration/deduplication.spec.md section 3)."""

from datetime import timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coach.domain.activity.matching import (
    MatchDecision,
    MatchFacts,
    best_match,
    decide,
    relative_difference,
    score,
    sports_compatible,
)
from coach.domain.common.sport import Sport
from samples import START, sample_observation, tennis_values

RUN = MatchFacts(Sport.RUNNING, START, 3600.0, 3500.0, 10000.0)


def _facts(**changes: object) -> MatchFacts:
    values = {
        "sport": RUN.sport,
        "start_time_utc": RUN.start_time_utc,
        "elapsed_seconds": RUN.elapsed_seconds,
        "moving_seconds": RUN.moving_seconds,
        "distance_meters": RUN.distance_meters,
    }
    values.update(changes)
    return MatchFacts(**values)  # type: ignore[arg-type]


def _score(**changes: object) -> float | None:
    result = score(_facts(**changes), RUN)
    return None if result is None else result[0]


@pytest.mark.req("FR-ACT-007")
def test_sport_compatibility() -> None:
    assert sports_compatible(Sport.RUNNING, Sport.RUNNING)
    assert sports_compatible(Sport.OTHER, Sport.TENNIS)
    assert sports_compatible(Sport.CYCLING, Sport.OTHER)
    assert not sports_compatible(Sport.RUNNING, Sport.CYCLING)
    assert _score(sport=Sport.CYCLING) is None
    assert _score(sport=Sport.OTHER) == 1.0


@pytest.mark.req("FR-ACT-007")
def test_start_component_is_full_to_60_seconds_and_excludes_at_600() -> None:
    assert _score(start_time_utc=START + timedelta(seconds=60)) == 1.0
    assert _score(start_time_utc=START - timedelta(seconds=599)) is not None
    assert _score(start_time_utc=START + timedelta(seconds=600)) is None
    halfway = _score(start_time_utc=START + timedelta(seconds=330))
    assert halfway == pytest.approx(0.4 * 0.5 + 0.3 + 0.3)


@pytest.mark.req("FR-ACT-007")
def test_ratios_are_full_within_two_percent_and_zero_from_fifteen() -> None:
    assert _score(distance_meters=10200.0) == pytest.approx(1.0)  # 1.96 %
    assert _score(distance_meters=11800.0) == pytest.approx(0.7)  # 15.25 %
    assert _score(elapsed_seconds=3600.0 / 0.915) == pytest.approx(0.4 + 0.3 * 0.5 + 0.3)  # 8.5 %
    assert relative_difference(0.0, 0.0) == 0.0
    assert relative_difference(0.0, 5.0) == 1.0


@pytest.mark.req("FR-ACT-007")
def test_missing_components_are_excluded_and_weights_renormalised() -> None:
    no_distance = _score(distance_meters=None, start_time_utc=START + timedelta(seconds=330))
    assert no_distance == pytest.approx((0.4 * 0.5 + 0.3) / 0.7)
    moving_only = score(_facts(elapsed_seconds=None), RUN)
    assert moving_only is not None
    assert moving_only[0] == 1.0
    start_only = _score(
        elapsed_seconds=None,
        moving_seconds=None,
        distance_meters=None,
        start_time_utc=START + timedelta(seconds=330),
    )
    assert start_only == pytest.approx(0.5)


@pytest.mark.req("FR-ACT-007")
@pytest.mark.parametrize(
    ("value", "decision"),
    [
        (1.0, MatchDecision.LINK),
        (0.85, MatchDecision.LINK),
        (0.8499, MatchDecision.AMBIGUOUS),
        (0.60, MatchDecision.AMBIGUOUS),
        (0.5999, MatchDecision.DISTINCT),
        (0.0, MatchDecision.DISTINCT),
    ],
)
def test_thresholds(value: float, decision: MatchDecision) -> None:
    assert decide(value) is decision


@pytest.mark.req("FR-ACT-007")
def test_manual_tennis_entry_against_a_recorded_session_is_ambiguous() -> None:
    manual = MatchFacts.of(tennis_values(**{"timing.startTimeUtc": START + timedelta(minutes=5)}))
    recorded = MatchFacts.of(tennis_values(**{"timing.elapsedSeconds": 5100.0}))
    result = score(manual, recorded)
    assert result is not None
    assert decide(result[0]) is MatchDecision.AMBIGUOUS


@pytest.mark.req("FR-ACT-007")
def test_best_match_prefers_score_then_start_then_lowest_id() -> None:
    low, high = UUID(int=1), UUID(int=2)
    near = _facts(start_time_utc=START + timedelta(seconds=10))
    far = _facts(start_time_utc=START + timedelta(seconds=50))
    worse = _facts(distance_meters=11000.0)
    match = best_match(RUN, [(high, worse), (low, far), (high, near)])
    assert match is not None
    assert match.activity_id == high
    tie = best_match(RUN, [(high, near), (low, near)])
    assert tie is not None
    assert tie.activity_id == low
    assert best_match(RUN, [(low, _facts(sport=Sport.SWIMMING))]) is None
    assert best_match(RUN, []) is None


@pytest.mark.req("FR-ACT-007")
def test_facts_come_from_observation_values() -> None:
    facts = MatchFacts.of(sample_observation().values)
    assert facts == MatchFacts(Sport.RUNNING, START, 3720.0, 3650.0, 10050.5)


_FACTS = st.builds(
    MatchFacts,
    sport=st.sampled_from(list(Sport)),
    start_time_utc=st.integers(0, 900).map(lambda seconds: START + timedelta(seconds=seconds)),
    elapsed_seconds=st.one_of(st.none(), st.floats(0, 20_000)),
    moving_seconds=st.one_of(st.none(), st.floats(0, 20_000)),
    distance_meters=st.one_of(st.none(), st.floats(0, 100_000)),
)


@pytest.mark.req("FR-ACT-007")
@given(first=_FACTS, second=_FACTS)
def test_score_is_symmetric_and_bounded(first: MatchFacts, second: MatchFacts) -> None:
    forward, backward = score(first, second), score(second, first)
    assert forward == backward
    if forward is not None:
        assert 0.0 <= forward[0] <= 1.0
        assert forward[1] < 600
