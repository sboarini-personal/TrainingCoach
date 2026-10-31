"""Plan generation (planning spec 5.1; FR-ONB-003 to FR-ONB-005, FR-PLN-001): the application
side of generator rules 1.0.0.

It reads the Definition of Ready (none while NOT_READY, AT-ONB-002), the goal (the primary
one by default), the planning context the policies read, the coaching conditions, the
athlete's profile and the goal sport's weekly minutes over the last complete weeks; runs the
generator; and proposes the content through ``PlanService``, which checks the goal, readiness
and every plan policy again. A goal keeps one plan: a new generation is a new version of it,
whose parent is the ACTIVE version (else the latest) and which replaces a pending proposal.
The lifecycle state BASELINE_COLLECTION gives a baseline block (FR-ONB-005).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta
from uuid import UUID

from coach.application.analytics import ELAPSED_FIELD, AnalyticsService
from coach.application.athlete_inputs import AthleteInputsService
from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentRequiredError
from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.plans import PlanNotReadyError, PlanService
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.coaching.state import Condition, LifecycleState
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange
from coach.domain.goals.goal import Goal
from coach.domain.onboarding.readiness import ReadinessInput, ReadinessStatus
from coach.domain.plans.plan import Generator, GeneratorKind, PlanStatus, PlanVersion
from coach.planning.generator import (
    GENERATOR_RULES_VERSION,
    HISTORY_WEEKS,
    GenerationInputs,
    generate,
)
from coach.policy.plans import PlanningContext

GENERATOR = Generator(kind=GeneratorKind.ENGINE, engine_version=GENERATOR_RULES_VERSION)
WeeklyMinutes = Callable[[OwnerScope, Sport, date], tuple[float, ...]]


def weekly_minutes_from(analytics: AnalyticsService) -> WeeklyMinutes:
    """The sport's minutes in each of the last complete weeks (Monday to Sunday) before the
    week of ``today``, oldest first; nothing without the activities consent."""

    def weekly(scope: OwnerScope, sport: Sport, today: date) -> tuple[float, ...]:
        monday = today - timedelta(days=today.isoweekday() - 1)
        first = monday - timedelta(weeks=HISTORY_WEEKS)
        try:
            views = analytics.activities(scope, DateRange(first, monday - timedelta(days=1)))
        except ConsentRequiredError:
            return ()
        minutes: dict[int, float] = defaultdict(float)
        for view in views:
            if view.sport is sport and view.counts_toward_totals:
                week = (view.training_day - first).days // 7
                minutes[week] += (view.number(ELAPSED_FIELD) or 0.0) / 60
        return tuple(minutes[week] for week in range(HISTORY_WEEKS))

    return weekly


def _years(profile: AthleteProfile, sport: Sport) -> float | None:
    field = {
        Sport.RUNNING: profile.running_experience,
        Sport.CYCLING: profile.cycling_experience,
        Sport.SWIMMING: profile.swimming_experience,
    }.get(sport)
    if field is None or field.value is None:
        return None
    return field.value.years_experience


class PlanGenerationService:
    def __init__(
        self,
        inputs: AthleteInputsService,
        plans: PlanService,
        *,
        planning: Callable[[OwnerScope], PlanningContext],
        conditions: CoachingConditions,
        weekly_minutes: WeeklyMinutes,
    ) -> None:
        self._inputs = inputs
        self._plans = plans
        self._planning = planning
        self._conditions = conditions
        self._weekly = weekly_minutes

    def generate(self, scope: OwnerScope, goal_id: UUID | None = None) -> PlanVersion:
        """A PROPOSED version for the goal (the primary one by default)."""
        report = self._inputs.readiness(scope)
        ready = report.readiness
        if ready.status is ReadinessStatus.NOT_READY:
            raise PlanNotReadyError(ready.missing_required)
        goal = self._goal(scope, goal_id)
        context = self._planning(scope)
        if context.availability is None:
            raise PlanNotReadyError((ReadinessInput.WEEKLY_AVAILABILITY,))
        if context.health is None:
            raise PlanNotReadyError((ReadinessInput.HEALTH_CONSTRAINTS,))
        profile = self._inputs.profile(scope)
        conditions = self._conditions.current(scope).conditions
        strength = profile.strength_experience
        content = generate(
            GenerationInputs(
                today=context.today,
                goal_id=goal.goal_id,
                sport=goal.sport,
                target_date=goal.target_date,
                availability=context.availability,
                resources=context.resources,
                health=context.health,
                strength=None if strength is None else strength.value,
                sport_years=_years(profile, goal.sport),
                weekly_minutes=self._weekly(scope, goal.sport, context.today),
                baseline=report.lifecycle is LifecycleState.BASELINE_COLLECTION,
                missing_preferred=frozenset(ready.missing_preferred),
                safety_hold=Condition.SAFETY_HOLD in conditions,
                recovery_block=Condition.RECOVERY_BLOCK in conditions,
                recovery_block_until=context.recovery_block_until,
            )
        )
        existing = self._plans.latest_plan(scope, goal.goal_id)
        if existing is None:
            return self._plans.propose(
                scope, goal_id=goal.goal_id, content=content, generator=GENERATOR
            )
        latest = self._plans.version(scope, existing.plan_id, existing.latest_number)
        return self._plans.propose(
            scope,
            goal_id=goal.goal_id,
            content=content,
            generator=GENERATOR,
            plan_id=existing.plan_id,
            parent=existing.active_number or existing.latest_number,
            replaces=latest.number if latest.status is PlanStatus.PROPOSED else None,
        )

    def _goal(self, scope: OwnerScope, goal_id: UUID | None) -> Goal:
        if goal_id is None:
            primary = self._inputs.primary_goal(scope)
            if primary is None:
                raise PlanNotReadyError((ReadinessInput.PRIMARY_GOAL,))
            return primary
        found = next((item for item in self._inputs.goals(scope) if item.goal_id == goal_id), None)
        if found is None:
            raise NotFoundError
        return found
