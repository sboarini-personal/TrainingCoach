"""Workout executions (planning spec 6; FR-PLN-007, FR-ACT-011): what was done against the
ACTIVE plan version, by execution rules 1.0.0, and the athlete's manual links.

Executions are computed on read from the ACTIVE version, the ACTIVE activities (EFFECTIVE
view, so corrections count) and the links; only the links are stored. A link names an
activity of the athlete and a planned workout of the ACTIVE version in the same training week
(Monday to Sunday), or none to make the activity UNPLANNED; a newer link replaces it. Both
activity readers require the ACTIVITIES consent (privacy spec: every activity read).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from uuid import UUID

from coach.application.analytics import ELAPSED_FIELD, RPE_FIELD
from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.consent import ConsentRequiredError
from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.plans import PlanService
from coach.application.ports import AuditLog, Clock, OwnedRepository
from coach.domain.activity.activity import Activity
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.time import DateRange
from coach.domain.plans.execution import DoneActivity, ExecutionLink, WorkoutExecution
from coach.domain.plans.workout import WorkoutType
from coach.planning.execution import executions
from coach.planning.patterns import PlannedSession

DISTANCE_FIELD = "distance.meters"


def done(view: Activity) -> DoneActivity:
    return DoneActivity(
        activity_id=view.activity_id,
        training_day=view.training_day,
        start=view.start_time_utc,
        sport=view.sport,
        elapsed_seconds=view.number(ELAPSED_FIELD),
        distance_meters=view.number(DISTANCE_FIELD),
        rpe=view.number(RPE_FIELD),
    )


def _monday(day: date) -> date:
    return day - timedelta(days=day.isoweekday() - 1)


class ExecutionService:
    def __init__(
        self,
        plans: PlanService,
        links: OwnedRepository[ExecutionLink],
        *,
        activities: Callable[[OwnerScope, DateRange], Sequence[Activity]],
        activity: Callable[[OwnerScope, UUID], Activity],
        today: Callable[[OwnerScope], date],
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._plans = plans
        self._links = links
        self._activities = activities
        self._activity = activity
        self._today = today
        self._audit = audit
        self._clock = clock

    def executions(self, scope: OwnerScope, period: DateRange) -> list[WorkoutExecution]:
        """The executions of the period: its planned workouts and its activities."""
        return self.recent(scope, period)[0]

    def done_workouts(self, scope: OwnerScope, day: date) -> frozenset[UUID]:
        """The ACTIVE version's workouts of the day with activities: history, which the plan
        policies and the adaptive loop leave as it is. None without the activities consent
        (nothing is read, so nothing counts as done)."""
        try:
            found = self.executions(scope, DateRange(day, day))
        except ConsentRequiredError:
            return frozenset()
        return frozenset(
            item.planned_workout_id
            for item in found
            if item.planned_workout_id is not None and item.completed_activity_ids
        )

    def recent(
        self, scope: OwnerScope, period: DateRange
    ) -> tuple[list[WorkoutExecution], dict[UUID, DoneActivity]]:
        """The executions of the period and the activities they read. Matching runs over
        whole training weeks, so an activity linked to a workout of its week counts there
        even when the period cuts the week (links stay within a week)."""
        weeks = DateRange(_monday(period.start), _monday(period.end) + timedelta(days=6))
        active = self._plans.active(scope)
        planned = (
            []
            if active is None
            else [
                item
                for item in active.content.workouts
                if weeks.start <= item.training_day <= weeks.end
            ]
        )
        views = [done(view) for view in self._activities(scope, weeks)]
        links = {item.activity_id: item for item in self._links.list_all(scope)}
        found = [
            item
            for item in executions(planned, views, links, today=self._today(scope))
            if period.start <= item.training_day <= period.end
        ]
        return found, {
            item.activity_id: item
            for item in views
            if any(item.activity_id in entry.completed_activity_ids for entry in found)
        }

    def planned_sessions(
        self, scope: OwnerScope, period: DateRange
    ) -> tuple[list[PlannedSession], EvidenceRef] | None:
        """The executions of the ACTIVE plan's workouts in the period as the pattern rules
        read them, and the plan version as their evidence; ``None`` without a plan."""
        active = self._plans.active(scope)
        if active is None:
            return None
        found = []
        for item in self.executions(scope, period):
            if item.planned_workout_id is None:
                continue
            workout = active.content.workout(item.planned_workout_id)
            found.append(
                PlannedSession(
                    workout_id=item.planned_workout_id,
                    day=item.training_day,
                    status=item.status,
                    duration_ratio=item.compliance.duration_ratio,
                    session_rpe=item.session_rpe,
                    target_rpe=None if workout is None else workout.max_rpe,
                    activity_ids=item.completed_activity_ids,
                )
            )
        return found, EvidenceRef(EvidenceKind.PLAN_VERSION, active.entity_id)

    def link(self, scope: OwnerScope, activity_id: UUID, workout_id: UUID | None) -> ExecutionLink:
        """The athlete links an activity to a planned workout, or to none (UNPLANNED)."""
        view = self._activity(scope, activity_id)  # the athlete's own, or NotFoundError
        if workout_id is not None:
            active = self._plans.active(scope)
            workout = None if active is None else active.content.workout(workout_id)
            if workout is None:
                raise NotFoundError
            if workout.type is WorkoutType.REST:
                raise DomainValidationError("a rest day has no workout to link")
            if _monday(workout.training_day) != _monday(view.training_day):
                raise DomainValidationError("an activity links to a workout of its own week")
        made = ExecutionLink(
            athlete_id=scope.athlete_id,
            activity_id=activity_id,
            planned_workout_id=workout_id,
            linked_at=self._clock(),
        )
        try:
            stored = self._links.get(scope, activity_id)
        except NotFoundError:
            saved = self._links.add(scope, made)
        else:
            saved = self._links.update(
                scope, made.with_version(stored.version), expected_version=stored.version
            )
        self._audit.append(
            AuditEvent(
                occurred_at=self._clock(),
                actor=ActorKind.USER,
                action="plan.execution_linked",
                outcome=AuditOutcome.SUCCESS,
                athlete_id=scope.athlete_id,
                target_type="ACTIVITY",
                target_id=str(activity_id),
                reason_codes=("UNPLANNED" if workout_id is None else "LINKED",),
            )
        )
        return saved
