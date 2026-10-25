import dataclasses
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest

from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.common.sport import Sport
from coach.domain.goals.goal import (
    Goal,
    GoalBaseline,
    GoalPriority,
    GoalStatus,
    SportEvent,
    ensure_single_active_primary,
)

NOW = datetime(2026, 10, 24, 18, 30, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
EVENT = UUID("0193b2a0-0000-7000-8000-000000000611")


def goal(goal_number: int = 1, **overrides: Any) -> Goal:
    values: dict[str, Any] = {
        "goal_id": UUID(f"0193b2a0-0000-7000-8000-00000000060{goal_number}"),
        "athlete_id": ATHLETE,
        "sport": Sport.RUNNING,
        "distance_meters": 10_000.0,
        "target_time_seconds": 2700.0,
        "target_date": date(2026, 12, 13),
        "priority": GoalPriority.A,
        "created_at": NOW,
    }
    values.update(overrides)
    return Goal.new(**values)


@pytest.mark.req("FR-GOL-001")
def test_goal_is_separate_from_the_event_it_may_reference() -> None:
    event = SportEvent(
        event_id=EVENT,
        athlete_id=ATHLETE,
        name="Synthetic City 10K",
        sport=Sport.RUNNING,
        event_date=date(2026, 12, 13),
        distance_meters=10_000.0,
        priority=GoalPriority.A,
    )
    assert goal(event_id=event.event_id).event_id == EVENT
    assert goal().event_id is None


@pytest.mark.req("FR-GOL-001")
def test_event_name_is_untrusted_text() -> None:
    with pytest.raises(DomainValidationError):
        SportEvent(
            event_id=EVENT,
            athlete_id=ATHLETE,
            name=" ",
            sport=Sport.RUNNING,
            event_date=date(2026, 12, 13),
            priority=GoalPriority.B,
        )


@pytest.mark.req("FR-GOL-002")
def test_priorities_are_a_b_c() -> None:
    assert {priority.value for priority in GoalPriority} == {"A", "B", "C"}


@pytest.mark.req("FR-GOL-002")
def test_only_one_active_primary_goal() -> None:
    active_primary = goal(1).transition(GoalStatus.ACTIVE)
    with pytest.raises(DomainValidationError):
        ensure_single_active_primary([active_primary], goal(2).transition(GoalStatus.ACTIVE))
    ensure_single_active_primary(
        [active_primary], goal(2, priority=GoalPriority.B).transition(GoalStatus.ACTIVE)
    )
    ensure_single_active_primary([active_primary], active_primary)


@pytest.mark.req("FR-GOL-002")
def test_goal_status_transitions() -> None:
    draft = goal()
    assert draft.status is GoalStatus.DRAFT
    achieved = draft.transition(GoalStatus.ACTIVE).transition(GoalStatus.ACHIEVED)
    assert achieved.status is GoalStatus.ACHIEVED
    with pytest.raises(InvalidTransitionError):
        achieved.transition(GoalStatus.ACTIVE)
    with pytest.raises(InvalidTransitionError):
        draft.transition(GoalStatus.ACHIEVED)


@pytest.mark.req("FR-GOL-001")
def test_goal_values_are_validated() -> None:
    with pytest.raises(DomainValidationError):
        goal(target_time_seconds=0.0)
    with pytest.raises(DomainValidationError):
        goal(distance_meters=-1.0)
    with pytest.raises(DomainValidationError):
        goal(created_at=datetime(2026, 10, 24))  # noqa: DTZ001


@pytest.mark.req("FR-GOL-001")
def test_baseline_is_an_immutable_snapshot() -> None:
    baseline = GoalBaseline(captured_at=NOW, evidence=(), summary="Declared 10K in 49:00")
    with_baseline = goal(baseline=baseline)
    assert with_baseline.baseline == baseline
    with pytest.raises(dataclasses.FrozenInstanceError):
        with_baseline.baseline = None  # type: ignore[misc]
