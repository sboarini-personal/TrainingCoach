"""The adaptive loop as a service (planning spec 7; FR-ADP-001 to FR-ADP-004, FR-PLN-002,
FR-ONB-006; AT-ADP-001, AT-EXP-001). Synthetic data only."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from test_execution import Activities

from coach.application.adaptation import AdaptationService
from coach.application.events import EventName
from coach.application.executions import ExecutionService
from coach.domain.activity.observation import SourceKind
from coach.domain.availability.availability import Availability, DayDefaults
from coach.domain.coaching.state import LifecycleState
from coach.domain.common.quality import Confidence
from coach.domain.onboarding.readiness import Readiness, ReadinessInput, ReadinessStatus
from coach.domain.plans.adaptation import (
    AdaptationDecision,
    AdaptationReason,
    AdaptationTrigger,
    Decision,
)
from coach.domain.plans.execution import ExecutionLink
from coach.domain.plans.plan import PlanStatus, PlanVersion
from coach.domain.recovery.assessment import Reason, RecoveryAssessment, RecoveryStatus
from coach.domain.recovery.checkin import CheckInPain, DailyCheckIn
from coach.infrastructure.memory import InMemoryRepository
from plan_samples import MONDAY, content, week
from plan_world import ENGINE, GOAL, PATTERN, SCOPE, PlanWorld
from samples import ATHLETE, sample_activity, sample_observation

THURSDAY = MONDAY + timedelta(days=3)
AT = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)


def normal(day: date) -> RecoveryAssessment:
    return RecoveryAssessment(
        assessment_id=UUID(int=0xA01),
        athlete_id=ATHLETE,
        day=day,
        status=RecoveryStatus.NORMAL,
        reasons=(Reason.NO_DEVIATION,),
        signals=(),
        pain=None,
        confidence=Confidence.MEDIUM,
        uncertainty_reasons=frozenset(),
        assessed_at=AT,
    )


class Generation:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID | None, UUID | None]] = []

    def generate(
        self, scope: Any, goal_id: UUID | None = None, *, decision_id: UUID | None = None
    ) -> Any:
        self.calls.append((goal_id, decision_id))
        return None


class Loop:
    def __init__(self) -> None:
        self.world = PlanWorld()
        self.world.to_goal_defined()
        two_weeks = content(week(MONDAY), week(MONDAY + timedelta(weeks=1), index=2))
        plan = self.world.service.propose(SCOPE, goal_id=GOAL, content=two_weeks, generator=ENGINE)
        self.plan_id = plan.plan_id
        self.world.activate(plan.plan_id, 1)
        self.today = THURSDAY
        self.checkins: list[DailyCheckIn] = []
        self.store = Activities()
        self.generation = Generation()
        counter = itertools.count(1)
        executions = ExecutionService(
            self.world.service,
            InMemoryRepository[ExecutionLink](),
            activities=self.store.between,
            activity=self.store.get,
            today=lambda _scope: self.today,
            audit=self.world.audit,
            clock=self.world.clock,
        )
        self.decisions = InMemoryRepository[AdaptationDecision]()
        self.service = AdaptationService(
            self.world.service,
            executions,
            self.generation,  # type: ignore[arg-type]
            self.decisions,
            assessments=lambda _scope, until, _days: [normal(until)],
            prescriptions=lambda _scope, _day: (),
            checkins=lambda _scope, _until, _days: tuple(self.checkins),
            availability=lambda _scope: Availability.new(
                ATHLETE, recurring=PATTERN, defaults=DayDefaults(240)
            ),
            readiness=lambda _scope: self.world.ready,
            goal_date=lambda _scope, _goal: date(2027, 3, 14),
            conditions=self.world.conditions,
            lifecycle=self.world.lifecycle,
            today=lambda _scope: self.today,
            audit=self.world.audit,
            events=self.world.events,
            clock=self.world.clock,
            ids=lambda: UUID(int=0xE00 + next(counter)),
        )

    def run_on(self, day: date, minutes: float) -> None:
        start = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=21)
        observation = sample_observation(
            SourceKind.ATHLETE_ENTRY,
            updates={
                "timing.startTimeUtc": start,
                "timing.elapsedSeconds": minutes * 60,
                "subjective.rpe": 4.0,
            },
            provider_activity_id=f"run-{day}",
        )
        self.store.add(sample_activity(observation, activity_id=UUID(int=0x880 + day.day)))

    def lifecycle(self) -> LifecycleState:
        return self.world.lifecycle.current(SCOPE).lifecycle

    def versions(self) -> list[PlanVersion]:
        return self.world.service.versions(SCOPE, self.plan_id)


@pytest.mark.req("FR-ADP-001", "FR-ADP-003", "FR-ONB-006")
@pytest.mark.acceptance("AT-ADP-001")
def test_a_poor_workout_is_recorded_as_maintain_and_creates_no_version() -> None:
    loop = Loop()
    loop.run_on(MONDAY + timedelta(days=2), 20)  # 20 of the planned 40 minutes on Wednesday
    result = loop.service.run(SCOPE, AdaptationTrigger.COMPLETED_WORKOUT)
    assert (result.decision.decision, result.proposed) == (Decision.MAINTAIN, None)
    assert "50% of the planned duration" in result.decision.interpretation
    assert [item.number for item in loop.versions()] == [1]  # no new version
    assert loop.lifecycle() is LifecycleState.TRAINING  # ANALYSIS, then back
    assert loop.service.decisions(SCOPE) == [result.decision]
    assert (result.decision.plan_id, result.decision.from_version) == (loop.plan_id, 1)
    actions = [record.document["action"] for record in loop.world.audit.all_records()]
    assert "plan.adaptation_decided" in actions
    assert (
        EventName.ADAPTATION_DECIDED,
        {"decision": "MAINTAIN", "trigger": "COMPLETED_WORKOUT"},
    ) in loop.world.events.events


@pytest.mark.req("FR-ADP-002", "FR-ADP-004", "FR-PLN-002", "NFR-EXP-001")
@pytest.mark.acceptance("AT-EXP-001")
def test_a_reduction_proposes_a_version_that_names_its_decision() -> None:
    loop = Loop()
    loop.today = MONDAY + timedelta(days=1)  # Tuesday: goblet squats at RPE 7 today
    loop.checkins = [
        DailyCheckIn(
            checkin_id=UUID(int=0xD02),
            athlete_id=ATHLETE,
            recorded_at=AT,
            training_day=loop.today,
            pain=CheckInPain(intensity=5, location="rodilla"),
            pain_flag=True,
        )
    ]
    result = loop.service.run(SCOPE)
    decision = result.decision
    assert decision.decision is Decision.REDUCE
    assert result.proposed is not None
    assert (result.proposed.number, result.proposed.parent_number, result.proposed.status) == (
        2,
        1,
        PlanStatus.PROPOSED,
    )
    assert result.proposed.generator.decision_id == decision.decision_id
    assert decision.proposed_version == 2
    assert decision.evidence  # AT-EXP-001: the reduction states its evidence
    assert decision.affected_workouts
    assert loop.lifecycle() is LifecycleState.ADAPTATION
    loop.world.activate(loop.plan_id, 2)
    assert loop.lifecycle() is LifecycleState.TRAINING  # the adapted version is active
    same_day = loop.service.run(SCOPE)  # the strength is already light: nothing to change
    assert (same_day.decision.decision, same_day.proposed) == (Decision.MAINTAIN, None)
    loop.today = MONDAY + timedelta(days=8)  # a new pain report on Tuesday of week 2
    loop.checkins = [replace(loop.checkins[0], checkin_id=UUID(int=0xD03), training_day=loop.today)]
    again = loop.service.run(SCOPE)
    assert again.proposed is not None
    assert loop.lifecycle() is LifecycleState.ADAPTATION
    loop.world.service.reject(SCOPE, loop.plan_id, again.proposed.number)
    assert loop.lifecycle() is LifecycleState.TRAINING  # the athlete refused it


@pytest.mark.req("FR-ADP-002", "FR-PLN-006")
def test_progress_regenerates_from_the_updated_history() -> None:
    loop = Loop()
    loop.today = MONDAY + timedelta(weeks=1)
    durations = {1: 45, 2: 40, 3: 60, 5: 60}  # every planned workout done as planned
    for offset, minutes in durations.items():
        loop.run_on(MONDAY + timedelta(days=offset), minutes)
    strength_day = MONDAY + timedelta(days=1)
    loop.store.views[UUID(int=0x880 + strength_day.day)] = sample_activity(
        sample_observation(
            SourceKind.ATHLETE_ENTRY,
            updates={
                "classification.sport": "STRENGTH",
                "classification.subtype": None,
                "timing.startTimeUtc": datetime(2026, 10, 27, 21, 0, tzinfo=UTC),
                "timing.elapsedSeconds": 2700.0,
                "subjective.rpe": 6.0,
            },
            provider_activity_id="gym-1",
        ),
        activity_id=UUID(int=0x880 + strength_day.day),
    )
    result = loop.service.run(SCOPE, AdaptationTrigger.WEEKLY_REVIEW)
    assert result.decision.decision is Decision.PROGRESS, result.decision.interpretation
    assert loop.generation.calls == [(GOAL, result.decision.decision_id)]


@pytest.mark.req("FR-ADP-004", "FR-ONB-006")
def test_a_refused_proposal_is_still_recorded_and_the_lifecycle_returns() -> None:
    loop = Loop()
    loop.today = MONDAY + timedelta(days=1)
    loop.checkins = [
        DailyCheckIn(
            checkin_id=UUID(int=0xD04),
            athlete_id=ATHLETE,
            recorded_at=AT,
            training_day=loop.today,
            pain=CheckInPain(intensity=5, location="rodilla"),
            pain_flag=True,
        )
    ]
    loop.world.ready = Readiness(
        ReadinessStatus.NOT_READY, (ReadinessInput.HEALTH_CONSTRAINTS,), ()
    )  # the health declaration was not re-confirmed
    result = loop.service.run(SCOPE)
    assert (result.decision.decision, result.proposed) == (Decision.REDUCE, None)
    assert result.decision.reasons[-1] is AdaptationReason.PROPOSAL_REFUSED
    assert "could not be proposed (NOT_READY)" in result.decision.interpretation
    assert loop.service.decisions(SCOPE) == [result.decision]
    assert loop.lifecycle() is LifecycleState.TRAINING
    assert [item.number for item in loop.versions()] == [1]
