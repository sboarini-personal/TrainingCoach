"""Plan versions: propose, activate, reject (planning spec section 2; FR-PLN-002, FR-PLN-003,
ADR-010).

A version is created PROPOSED with its content hash and never changes content again; any
change is a new version with its parent. Activation is the athlete's explicit action: in one
transaction it updates the plan header (optimistic concurrency, so two concurrent activations
cannot both succeed, AT-PLN-002), makes the version ACTIVE and the previous ACTIVE one
SUPERSEDED. Activation is refused while SAFETY_HOLD is active (SAFE-MED-003); its reason is
screened for disclosed secrets before it is stored (SEC-SEC-005). Proposals and
activations are audited and emitted, and bring the coaching lifecycle forward where the state
machine allows (GOAL_DEFINED to PLAN_READY, PLAN_READY to TRAINING).
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from uuid import UUID

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.coaching import CoachingConditions, CoachingLifecycle
from coach.application.contracts.plans import content_hash
from coach.application.errors import ConcurrencyConflictError, NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import AuditLog, Batch, Clock, EventSink, IdFactory, OwnedRepository
from coach.application.screening import SecretScreen
from coach.domain.coaching.state import Condition, Trigger
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.plans.plan import (
    Generator,
    PlanContent,
    PlanStatus,
    PlanVersion,
    SupersededReason,
    TrainingPlan,
    version_id,
)


class PlanService:
    def __init__(
        self,
        headers: OwnedRepository[TrainingPlan],
        versions: OwnedRepository[PlanVersion],
        *,
        lifecycle: CoachingLifecycle,
        conditions: CoachingConditions,
        screen: SecretScreen,
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
        batch: Batch = nullcontext,
    ) -> None:
        self._headers = headers
        self._versions = versions
        self._lifecycle = lifecycle
        self._conditions = conditions
        self._screen = screen
        self._audit = audit
        self._events = events
        self._clock = clock
        self._ids = ids
        self._batch = batch

    # ------------------------------------------------------------------ reads

    def plans(self, scope: OwnerScope) -> list[TrainingPlan]:
        return sorted(self._headers.list_all(scope), key=lambda item: item.created_at)

    def plan(self, scope: OwnerScope, plan_id: UUID) -> TrainingPlan:
        return self._headers.get(scope, plan_id)

    def versions(self, scope: OwnerScope, plan_id: UUID) -> list[PlanVersion]:
        found = [item for item in self._versions.list_all(scope) if item.plan_id == plan_id]
        return sorted(found, key=lambda item: item.number)

    def version(self, scope: OwnerScope, plan_id: UUID, number: int) -> PlanVersion:
        return self._versions.get(scope, version_id(plan_id, number))

    def latest_plan(self, scope: OwnerScope, goal_id: UUID | None = None) -> TrainingPlan | None:
        """The most recently created plan (of a goal, when given)."""
        found = [item for item in self.plans(scope) if goal_id is None or item.goal_id == goal_id]
        return found[-1] if found else None

    def active(self, scope: OwnerScope, *, on: date | None = None) -> PlanVersion | None:
        """The ACTIVE version covering a day (any ACTIVE version without a day)."""
        for header in reversed(self.plans(scope)):
            if header.active_number is None:
                continue
            version = self.version(scope, header.plan_id, header.active_number)
            if on is None or version.content.start <= on <= version.content.end:
                return version
        return None

    # ------------------------------------------------------------------ changes

    def propose(
        self,
        scope: OwnerScope,
        *,
        goal_id: UUID,
        content: PlanContent,
        generator: Generator,
        plan_id: UUID | None = None,
        parent: int | None = None,
        replaces: int | None = None,
    ) -> PlanVersion:
        """A new PROPOSED version: of a new plan, or of ``plan_id`` after ``parent``.

        The policy engine has already allowed the content (planning spec 2: DRAFT to PROPOSED
        once it passed the policy engine). ``replaces`` names a PROPOSED version this one
        replaces: it becomes SUPERSEDED (REPLACED_BY_NEWER_PROPOSAL) in the same
        transaction. Other proposals stay PROPOSED until activated or rejected."""
        if plan_id is None and (parent is not None or replaces is not None):
            raise DomainValidationError("the first version of a plan has no parent")
        now = self._clock()
        with self._batch():
            if plan_id is None:
                header = self._headers.add(
                    scope,
                    TrainingPlan(
                        plan_id=self._ids(),
                        athlete_id=scope.athlete_id,
                        goal_id=goal_id,
                        created_at=now,
                    ),
                )
                number = 1
            else:
                stored = self._headers.get(scope, plan_id)
                if stored.goal_id != goal_id:
                    raise DomainValidationError("a plan keeps its goal")
                if any(
                    found is not None and not 1 <= found <= stored.latest_number
                    for found in (parent, replaces)
                ):
                    raise NotFoundError
                if replaces is not None:  # checked before anything is written
                    earlier = self._versions.get(scope, version_id(plan_id, replaces))
                    superseding = earlier.moved(
                        PlanStatus.SUPERSEDED, reason=SupersededReason.REPLACED_BY_NEWER_PROPOSAL
                    )
                number = stored.latest_number + 1
                header = self._headers.update(
                    scope,
                    TrainingPlan(
                        plan_id=stored.plan_id,
                        athlete_id=stored.athlete_id,
                        goal_id=stored.goal_id,
                        created_at=stored.created_at,
                        latest_number=number,
                        active_number=stored.active_number,
                        version=stored.version,
                    ),
                    expected_version=stored.version,
                )
            draft = PlanVersion(
                plan_id=header.plan_id,
                number=number,
                parent_number=parent,
                athlete_id=scope.athlete_id,
                goal_id=goal_id,
                generated_at=now,
                generator=generator,
                content=content,
                content_hash=content_hash(content),
            )
            proposed = self._versions.add(scope, draft.moved(PlanStatus.PROPOSED))
            replaced = None
            if replaces is not None:
                replaced = self._versions.update(
                    scope, superseding, expected_version=earlier.version
                )
        self._record(scope, "plan.proposed", proposed, ActorKind.SYSTEM)
        self._events.emit(EventName.PLAN_GENERATED, {"generator": generator.kind.value})
        self._events.emit(EventName.PLAN_PROPOSED, {"version": str(number)})
        if replaced is not None:
            self._record(scope, "plan.replaced", replaced, ActorKind.SYSTEM)
            self._events.emit(EventName.PLAN_SUPERSEDED, {"version": str(replaced.number)})
        self._lifecycle.advance(scope, Trigger.PLAN_PROPOSED)
        return proposed

    def activate(
        self,
        scope: OwnerScope,
        plan_id: UUID,
        number: int,
        *,
        expected_revision: int | None = None,
        reason: str | None = None,
    ) -> PlanVersion:
        """The athlete activates a PROPOSED version; the previous ACTIVE one is superseded in
        the same transaction. ``expected_revision`` is the header revision the athlete saw."""
        if Condition.SAFETY_HOLD in self._conditions.current(scope).conditions:
            raise InvalidTransitionError("plan activation is blocked during SAFETY_HOLD")
        if reason is not None:
            reason = self._screen.screen(reason, source="plan").text
        with self._batch():
            header = self._headers.get(scope, plan_id)
            if expected_revision is not None and header.version != expected_revision:
                raise ConcurrencyConflictError("the plan changed since it was read")
            target = self._versions.get(scope, version_id(plan_id, number))
            active = target.moved(PlanStatus.ACTIVE, activation_reason=reason)
            previous_number = header.active_number
            self._headers.update(
                scope,
                TrainingPlan(
                    plan_id=header.plan_id,
                    athlete_id=header.athlete_id,
                    goal_id=header.goal_id,
                    created_at=header.created_at,
                    latest_number=header.latest_number,
                    active_number=number,
                    version=header.version,
                ),
                expected_version=header.version,
            )
            stored = self._versions.update(scope, active, expected_version=target.version)
            superseded = None
            if previous_number is not None:
                previous = self._versions.get(scope, version_id(plan_id, previous_number))
                superseded = self._versions.update(
                    scope,
                    previous.moved(PlanStatus.SUPERSEDED),
                    expected_version=previous.version,
                )
        self._record(scope, "plan.activated", stored, ActorKind.USER)
        self._events.emit(EventName.PLAN_ACTIVATED, {"version": str(number)})
        if superseded is not None:
            self._events.emit(EventName.PLAN_SUPERSEDED, {"version": str(superseded.number)})
        self._lifecycle.advance(scope, Trigger.PLAN_ACTIVATED)
        return stored

    def reject(self, scope: OwnerScope, plan_id: UUID, number: int) -> PlanVersion:
        target = self._versions.get(scope, version_id(plan_id, number))
        rejected = self._versions.update(
            scope,
            target.moved(PlanStatus.SUPERSEDED, reason=SupersededReason.REJECTED),
            expected_version=target.version,
        )
        self._record(scope, "plan.rejected", rejected, ActorKind.USER)
        self._events.emit(EventName.PLAN_SUPERSEDED, {"version": str(number)})
        return rejected

    def _record(
        self, scope: OwnerScope, action: str, version: PlanVersion, actor: ActorKind
    ) -> None:
        self._audit.append(
            AuditEvent(
                occurred_at=self._clock(),
                actor=actor,
                action=action,
                outcome=AuditOutcome.SUCCESS,
                athlete_id=scope.athlete_id,
                target_type="PLAN_VERSION",
                target_id=str(version.entity_id),
                reason_codes=(version.status.value,),
            )
        )
