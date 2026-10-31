"""Workout execution (planning spec 6, execution rules 1.0.0; FR-PLN-007, FR-ACT-011,
AT-UNP-001). Synthetic data only.

The sample week (``plan_samples.week``) from Monday 2026-10-26: rest on Monday, 45 minutes of
strength on Tuesday, an easy 40-minute run on Wednesday, 60 minutes of intervals on Thursday
and an easy 60-minute run on Saturday.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.application.consent import ConsentRequiredError
from coach.application.errors import NotFoundError
from coach.application.executions import ExecutionService
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange
from coach.domain.consent.ledger import ConsentType
from coach.domain.plans.execution import (
    DoneActivity,
    ExecutionLink,
    ExecutionStatus,
    WorkoutExecution,
)
from coach.domain.plans.plan import PlanContent
from coach.domain.plans.workout import PlannedWorkout
from coach.infrastructure.memory import InMemoryRepository
from coach.planning.execution import executions
from plan_samples import MONDAY, content, easy_run, intervals, rest, week
from plan_world import ENGINE, GOAL, SCOPE, PlanWorld
from samples import ATHLETE, sample_activity, sample_observation

TUESDAY, WEDNESDAY, THURSDAY = (MONDAY + timedelta(days=offset) for offset in (1, 2, 3))
SATURDAY, SUNDAY = MONDAY + timedelta(days=5), MONDAY + timedelta(days=6)
LINKED = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def activity(
    day: date,
    sport: Sport,
    minutes: float | None,
    *,
    rpe: float | None = None,
    hour: int = 18,
    number: int = 0,
) -> DoneActivity:
    return DoneActivity(
        activity_id=UUID(int=0x900 + day.toordinal() % 100 * 10 + number),
        training_day=day,
        start=datetime.combine(day, time(hour), tzinfo=UTC),
        sport=sport,
        elapsed_seconds=None if minutes is None else minutes * 60,
        rpe=rpe,
    )


def by_status(found: list[WorkoutExecution]) -> list[tuple[date, str, int]]:
    return [
        (item.training_day, item.status.value, len(item.completed_activity_ids)) for item in found
    ]


def planned(plan: PlanContent) -> list[PlannedWorkout]:
    return list(plan.workouts)


@pytest.mark.req("FR-PLN-007", "FR-ACT-011")
def test_activities_match_their_day_and_sport_and_set_the_status() -> None:
    plan = content()
    done = [
        activity(TUESDAY, Sport.STRENGTH, 47),  # 47 / 45: COMPLETE
        activity(WEDNESDAY, Sport.RUNNING, 25, rpe=6),  # 25 / 40: PARTIAL
        activity(THURSDAY, Sport.CYCLING, 60),  # another sport instead: REPLACED
        activity(MONDAY, Sport.TENNIS, 120, rpe=7),  # a rest day: UNPLANNED
        activity(SUNDAY, Sport.RUNNING, 30),  # nothing planned: UNPLANNED
    ]
    found = executions(planned(plan), done, {}, today=SATURDAY)
    assert by_status(found) == [
        (MONDAY, "UNPLANNED", 1),
        (TUESDAY, "COMPLETE", 1),
        (WEDNESDAY, "PARTIAL", 1),
        (THURSDAY, "REPLACED", 1),
        (SATURDAY, "PLANNED", 0),  # not due yet
        (SUNDAY, "UNPLANNED", 1),
    ]
    tuesday, wednesday = found[1], found[2]
    assert tuesday.compliance.duration_ratio == pytest.approx(47 / 45, abs=0.001)
    assert (wednesday.compliance.duration_ratio, wednesday.session_rpe) == (0.625, 6)
    assert wednesday.compliance.intensity_share is None  # RPE targets: not measured in 1.0.0
    later = executions(planned(plan), done, {}, today=SATURDAY + timedelta(days=2))
    assert by_status(later)[4] == (SATURDAY, "SKIPPED", 0)  # the day after has ended
    graced = executions(planned(plan), done, {}, today=SUNDAY)
    assert by_status(graced)[4] == (SATURDAY, "PLANNED", 0)  # still within the grace day


@pytest.mark.req("FR-PLN-007", "FR-ACT-011", "FR-ADP-001")
@pytest.mark.acceptance("AT-UNP-001")
def test_tennis_on_a_rest_day_is_unplanned_and_the_next_intervals_stay_planned() -> None:
    plan = content(week(MONDAY, {2: [rest(WEDNESDAY)], 3: [intervals(THURSDAY)]}))
    tennis = activity(WEDNESDAY, Sport.TENNIS, 120, rpe=7)
    found = executions(planned(plan), [tennis], {}, today=THURSDAY)
    assert by_status(found) == [(WEDNESDAY, "UNPLANNED", 1), (THURSDAY, "PLANNED", 0)]
    assert found[0].session_rpe == 7


@pytest.mark.req("FR-PLN-007")
def test_the_athletes_links_place_an_activity_or_make_it_unplanned() -> None:
    plan = content()
    run = activity(WEDNESDAY, Sport.RUNNING, 40)
    lift = activity(TUESDAY, Sport.STRENGTH, 45)
    thursday = plan.weeks[0].days[3][0]
    links = {
        run.activity_id: ExecutionLink(
            athlete_id=ATHLETE,
            activity_id=run.activity_id,
            planned_workout_id=thursday.workout_id,
            linked_at=LINKED,
        ),
        lift.activity_id: ExecutionLink(
            athlete_id=ATHLETE,
            activity_id=lift.activity_id,
            planned_workout_id=None,
            linked_at=LINKED,
        ),
    }
    found = executions(planned(plan), [run, lift], links, today=SUNDAY + timedelta(days=2))
    assert by_status(found) == [
        (TUESDAY, "SKIPPED", 0),
        (TUESDAY, "UNPLANNED", 1),  # the athlete said it was not the planned session
        (WEDNESDAY, "SKIPPED", 0),
        (THURSDAY, "PARTIAL", 1),  # 40 of 60 minutes, linked by the athlete
        (SATURDAY, "SKIPPED", 0),
    ]
    assert found[3].manual
    assert found[1].manual
    stale = {
        run.activity_id: ExecutionLink(
            athlete_id=ATHLETE,
            activity_id=run.activity_id,
            planned_workout_id=UUID(int=0x999),  # not in this plan any more
            linked_at=LINKED,
        )
    }
    again = executions(planned(plan), [run], stale, today=SATURDAY)
    assert by_status(again)[1] == (WEDNESDAY, "COMPLETE", 1)  # matched by the rules


@pytest.mark.req("FR-PLN-007")
def test_compliance_measures_what_is_prescribed_and_known() -> None:
    plan = content(week(MONDAY, {2: [easy_run(WEDNESDAY, minutes=40)]}))
    unknown = activity(WEDNESDAY, Sport.RUNNING, None)
    found = executions(planned(plan), [unknown], {}, today=THURSDAY)
    assert found[0].compliance.measured == ()  # an unknown duration is no ratio, not zero
    assert found[0].status is ExecutionStatus.COMPLETE
    twice = [
        activity(WEDNESDAY, Sport.RUNNING, 20, hour=7),
        activity(WEDNESDAY, Sport.RUNNING, 25, number=1),
    ]
    split = executions(planned(plan), twice, {}, today=THURSDAY)
    assert by_status(split) == [(WEDNESDAY, "PARTIAL", 1), (WEDNESDAY, "UNPLANNED", 1)]
    assert split[0].completed_activity_ids == (twice[0].activity_id,)  # the earliest one


# --------------------------------------------------------------------------- service


class Activities:
    def __init__(self) -> None:
        self.views: dict[UUID, Any] = {}

    def add(self, view: Any) -> Any:
        self.views[view.activity_id] = view
        return view

    def get(self, scope: Any, activity_id: UUID) -> Any:
        found = self.views.get(activity_id)
        if found is None or found.athlete_id != scope.athlete_id:
            raise NotFoundError
        return found

    def between(self, scope: Any, period: DateRange) -> list[Any]:
        return [
            view
            for view in self.views.values()
            if view.athlete_id == scope.athlete_id
            and period.start <= view.training_day <= period.end
        ]


@pytest.mark.req("FR-PLN-007", "SEC-AUTHZ-001")
def test_links_name_the_athletes_activity_and_a_workout_of_its_week() -> None:
    world = PlanWorld()
    two_weeks = content(week(MONDAY), week(MONDAY + timedelta(weeks=1), index=2))
    first = world.service.propose(SCOPE, goal_id=GOAL, content=two_weeks, generator=ENGINE)
    world.activate(first.plan_id, 1)
    store = Activities()
    wednesday_run = store.add(
        sample_activity(
            sample_observation(
                updates={"timing.startTimeUtc": datetime(2026, 10, 28, 21, 0, tzinfo=UTC)}
            ),
            activity_id=UUID(int=0x801),
        )
    )
    links = InMemoryRepository[ExecutionLink]()
    service = ExecutionService(
        world.service,
        links,
        activities=store.between,
        activity=store.get,
        today=lambda _scope: SUNDAY,
        audit=world.audit,
        clock=world.clock,
    )
    period = DateRange(MONDAY, SUNDAY)
    statuses = {item.training_day: item.status for item in service.executions(SCOPE, period)}
    assert statuses[WEDNESDAY] is ExecutionStatus.PARTIAL  # the sample run lasts 30 minutes
    content_ = world.service.active(SCOPE).content  # type: ignore[union-attr]
    thursday = content_.weeks[0].days[3][0]
    made = service.link(SCOPE, wednesday_run.activity_id, thursday.workout_id)
    assert made.version == 1
    again = service.link(SCOPE, wednesday_run.activity_id, None)
    assert again.version == 2  # a newer link replaces it
    kinds = [(item.training_day, item.status) for item in service.executions(SCOPE, period)]
    assert (WEDNESDAY, ExecutionStatus.UNPLANNED) in kinds
    with pytest.raises(NotFoundError):  # not a workout of the ACTIVE plan
        service.link(SCOPE, wednesday_run.activity_id, UUID(int=0x999))
    with pytest.raises(NotFoundError):  # not the athlete's activity
        service.link(SCOPE, UUID(int=0x802), thursday.workout_id)
    with pytest.raises(DomainValidationError):  # a rest day
        service.link(SCOPE, wednesday_run.activity_id, content_.weeks[0].days[0][0].workout_id)
    following = content_.weeks[1].days[3][0]
    with pytest.raises(DomainValidationError):  # a workout of another week
        service.link(SCOPE, wednesday_run.activity_id, following.workout_id)
    actions = [record.document["action"] for record in world.audit.all_records()]
    assert actions.count("plan.execution_linked") == 2


@pytest.mark.req("FR-PLN-007")
def test_a_link_counts_even_when_the_period_cuts_the_week() -> None:
    world = PlanWorld()
    first = world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.activate(first.plan_id, 1)
    store = Activities()
    thursday_run = store.add(
        sample_activity(
            sample_observation(
                updates={"timing.startTimeUtc": datetime(2026, 10, 29, 21, 0, tzinfo=UTC)}
            ),
            activity_id=UUID(int=0x803),
        )
    )
    service = ExecutionService(
        world.service,
        InMemoryRepository[ExecutionLink](),
        activities=store.between,
        activity=store.get,
        today=lambda _scope: SUNDAY,
        audit=world.audit,
        clock=world.clock,
    )
    tuesday = world.service.active(SCOPE).content.on(TUESDAY)[0]  # type: ignore[union-attr]
    service.link(SCOPE, thursday_run.activity_id, tuesday.workout_id)  # done on Thursday
    later = service.executions(SCOPE, DateRange(WEDNESDAY, SUNDAY))  # Tuesday outside it
    thursday = next(item for item in later if item.training_day == THURSDAY)
    assert thursday.completed_activity_ids == ()  # the linked run is not taken by Thursday
    assert not [item for item in later if item.status is ExecutionStatus.UNPLANNED]
    whole = service.executions(SCOPE, DateRange(MONDAY, SUNDAY))
    linked = next(item for item in whole if item.planned_workout_id == tuesday.workout_id)
    assert (linked.completed_activity_ids, linked.manual) == ((thursday_run.activity_id,), True)
    # the plan policies read the workouts done on a day as history (review of P12.8)
    assert service.done_workouts(SCOPE, TUESDAY) == {tuesday.workout_id}
    assert service.done_workouts(SCOPE, WEDNESDAY) == frozenset()  # planned, not done


@pytest.mark.req("FR-PLN-007", "PRIV-CON-002")
def test_without_the_activities_consent_nothing_counts_as_done() -> None:
    world = PlanWorld()
    first = world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.activate(first.plan_id, 1)

    def refused(_scope: Any, _period: DateRange) -> list[Any]:
        raise ConsentRequiredError((ConsentType.ACTIVITIES,))

    service = ExecutionService(
        world.service,
        InMemoryRepository[ExecutionLink](),
        activities=refused,
        activity=Activities().get,
        today=lambda _scope: SUNDAY,
        audit=world.audit,
        clock=world.clock,
    )
    assert service.done_workouts(SCOPE, TUESDAY) == frozenset()  # the policies stay strict
