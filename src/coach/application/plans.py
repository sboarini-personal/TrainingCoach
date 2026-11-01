"""Plan versions: propose, activate, reject (planning spec section 2; FR-PLN-002, FR-PLN-003,
FR-ONB-003, FR-ONB-004, ADR-010).

A version is created PROPOSED with its content hash and never changes content again; any
change is a new version with its parent. A proposal needs the athlete's own open goal and the
Definition of Ready: none while NOT_READY, only a provisional one while PROVISIONAL. The
policy engine decides before anything is written: a denied proposal is never persisted
(FR-POL-002, AT-POL-001).

Activation is the athlete's explicit action, made with the plan revision they reviewed: in one
transaction it updates the plan header (optimistic concurrency, so two activations from the
same reading cannot both succeed, AT-PLN-002), makes the version ACTIVE and supersedes the
athlete's previous ACTIVE version, of this plan or another (at most one ACTIVE version per
athlete), and brings the lifecycle forward. The policy decides before the transaction (an
audited denial, SAFE-MED-003 among others) and again inside it, so a safety hold arriving
meanwhile cannot be bypassed. The activation reason is screened for disclosed secrets before
it is stored (SEC-SEC-005). Only a PROPOSED version can be rejected or replaced. Proposals,
activations and rejections are audited and emitted.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import date
from uuid import UUID

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.coaching import CoachingLifecycle
from coach.application.contracts.plans import content_hash
from coach.application.errors import ApplicationError, ConcurrencyConflictError, NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.policy_gate import PolicyGate
from coach.application.ports import AuditLog, Batch, Clock, EventSink, IdFactory, OwnedRepository
from coach.application.screening import SecretScreen
from coach.domain.coaching.state import Trigger
from coach.domain.common.errors import DomainValidationError
from coach.domain.goals.goal import Goal, GoalStatus
from coach.domain.onboarding.readiness import Readiness, ReadinessInput, ReadinessStatus
from coach.domain.plans.plan import (
    Generator,
    GeneratorKind,
    PlanContent,
    PlanStatus,
    PlanVersion,
    SupersededReason,
    TrainingPlan,
    version_id,
)
from coach.planning.edits import EDIT_RULES_VERSION
from coach.policy.engine import Action, ActionKind

PROPOSE_ACTION = "plan.propose"
ACTIVATE_ACTION = "plan.activate"
OPEN_GOALS = frozenset({GoalStatus.DRAFT, GoalStatus.ACTIVE})


class PlanNotReadyError(ApplicationError):
    """No plan before the Definition of Ready (FR-ONB-003, AT-ONB-002)."""

    def __init__(self, missing: tuple[ReadinessInput, ...]) -> None:
        super().__init__("not ready for a plan: " + ", ".join(item.value for item in missing))
        self.missing = missing


class PlanService:
    def __init__(
        self,
        headers: OwnedRepository[TrainingPlan],
        versions: OwnedRepository[PlanVersion],
        *,
        goals: OwnedRepository[Goal],
        readiness: Callable[[OwnerScope], Readiness],
        lifecycle: CoachingLifecycle,
        gate: PolicyGate,
        screen: SecretScreen,
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
        batch: Batch = nullcontext,
    ) -> None:
        self._headers = headers
        self._versions = versions
        self._goals = goals
        self._readiness = readiness
        self._lifecycle = lifecycle
        self._gate = gate
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
        """The athlete's ACTIVE version, if it covers the day (when one is given)."""
        for header in self.plans(scope):
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
        actor: ActorKind = ActorKind.SYSTEM,
    ) -> PlanVersion:
        """A new PROPOSED version: of a new plan, or of ``plan_id`` after ``parent``.

        The goal must be the athlete's and open, the Definition of Ready met (a provisional
        plan while PROVISIONAL) and the policy engine must allow the content; otherwise
        nothing is written but the audited denial. ``replaces`` names a PROPOSED version this
        one replaces: it becomes SUPERSEDED (REPLACED_BY_NEWER_PROPOSAL) in the same
        transaction. Other proposals stay PROPOSED until activated or rejected."""
        if plan_id is None and (parent is not None or replaces is not None):
            raise DomainValidationError("the first version of a plan has no parent")
        goal = self._goals.get(scope, goal_id)  # the athlete's own goal, or NotFoundError
        if goal.status not in OPEN_GOALS:
            raise DomainValidationError("a closed goal gets no new plan")
        ready = self._readiness(scope)
        if ready.status is ReadinessStatus.NOT_READY:
            raise PlanNotReadyError(ready.missing_required)
        if ready.status is ReadinessStatus.PROVISIONAL and not content.provisional:
            raise DomainValidationError(
                "with preferred inputs missing only a provisional plan may be proposed (FR-ONB-004)"
            )
        self._gate.enforce(
            scope,
            Action(
                kind=ActionKind.PROPOSE,
                name=PROPOSE_ACTION,
                resource_owner=scope.athlete_id,
                plan=content,
            ),
            actor=actor,
        )
        now = self._clock()
        with self._batch():
            replaced = None
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
                superseding = None
                if replaces is not None:  # checked before anything is written
                    earlier = self._versions.get(scope, version_id(plan_id, replaces))
                    superseding = earlier.moved(
                        PlanStatus.SUPERSEDED, reason=SupersededReason.REPLACED_BY_NEWER_PROPOSAL
                    )
                number = stored.latest_number + 1
                header = self._headers.update(
                    scope, replace(stored, latest_number=number), expected_version=stored.version
                )
                if superseding is not None:
                    replaced = self._versions.update(
                        scope, superseding, expected_version=superseding.version
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
        self._record(scope, "plan.proposed", proposed, actor)
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
        expected_revision: int,
        reason: str | None = None,
    ) -> PlanVersion:
        """The athlete activates a PROPOSED version, with the plan revision they reviewed."""
        found = self._versions.get(scope, version_id(plan_id, number))
        found.moved(PlanStatus.ACTIVE)  # the transition is valid before policy is asked
        action = Action(
            kind=ActionKind.WRITE_INTERNAL,
            name=ACTIVATE_ACTION,
            resource_owner=found.athlete_id,
            plan=found.content,
            activates_plan=True,
        )
        self._gate.enforce(scope, action)
        if reason is not None:
            reason = self._screen.screen(reason, source="plan").text
        superseded: list[PlanVersion] = []
        with self._batch():
            header = self._headers.get(scope, plan_id)
            if header.version != expected_revision:
                raise ConcurrencyConflictError("the plan changed since it was read")
            if not self._gate.allows(scope, action):  # the state changed since the decision
                raise ConcurrencyConflictError("the coaching state changed; try again")
            target = self._versions.get(scope, version_id(plan_id, number))
            active = target.moved(PlanStatus.ACTIVE, activation_reason=reason)
            for other in self.plans(scope):  # at most one ACTIVE version per athlete
                if other.active_number is None:
                    continue
                previous = self._versions.get(scope, version_id(other.plan_id, other.active_number))
                superseded.append(
                    self._versions.update(
                        scope,
                        previous.moved(PlanStatus.SUPERSEDED),
                        expected_version=previous.version,
                    )
                )
                if other.plan_id != plan_id:
                    self._headers.update(
                        scope, replace(other, active_number=None), expected_version=other.version
                    )
            self._headers.update(
                scope, replace(header, active_number=number), expected_version=header.version
            )
            stored = self._versions.update(scope, active, expected_version=target.version)
            if not self._lifecycle.advance(scope, Trigger.PLAN_ACTIVATED):
                self._lifecycle.advance(scope, Trigger.ADAPTED_VERSION_ACTIVATED)
        self._record(scope, "plan.activated", stored, ActorKind.USER)
        self._events.emit(EventName.PLAN_ACTIVATED, {"version": str(number)})
        for item in superseded:
            self._events.emit(EventName.PLAN_SUPERSEDED, {"version": str(item.number)})
        return stored

    def edit(
        self,
        scope: OwnerScope,
        plan_id: UUID,
        content: PlanContent,
        *,
        expected_revision: int,
        summary: str,
    ) -> PlanVersion:
        """The athlete's own change of the ACTIVE version, active at once (edit rules,
        athlete's review of 2026-10-31): a new version whose parent is the ACTIVE one,
        checked by every plan policy and by activation's (a safety hold denies it), then
        activated. Refused while a proposed version waits, which it would leave behind."""
        header = self._headers.get(scope, plan_id)
        if header.version != expected_revision:
            raise ConcurrencyConflictError("the plan changed since it was read")
        if header.active_number is None:
            raise DomainValidationError("this plan has no ACTIVE version to change")
        latest = self.version(scope, plan_id, header.latest_number)
        if latest.status is PlanStatus.PROPOSED:
            raise DomainValidationError(
                f"version {latest.number} is waiting for you: activate or reject it first"
            )
        active = self.version(scope, plan_id, header.active_number)
        self._gate.enforce(  # before anything is written: activation's own policies
            scope,
            Action(
                kind=ActionKind.WRITE_INTERNAL,
                name=ACTIVATE_ACTION,
                resource_owner=scope.athlete_id,
                plan=content,
                activates_plan=True,
            ),
        )
        proposed = self.propose(
            scope,
            goal_id=active.goal_id,
            content=content,
            generator=Generator(kind=GeneratorKind.ATHLETE, engine_version=EDIT_RULES_VERSION),
            plan_id=plan_id,
            parent=active.number,
            actor=ActorKind.USER,
        )
        return self.activate(
            scope,
            plan_id,
            proposed.number,
            expected_revision=self._headers.get(scope, plan_id).version,
            reason=summary,
        )

    def reject(self, scope: OwnerScope, plan_id: UUID, number: int) -> PlanVersion:
        """The athlete rejects a PROPOSED version (an ACTIVE one is superseded only by
        activating another)."""
        target = self._versions.get(scope, version_id(plan_id, number))
        rejected = self._versions.update(
            scope,
            target.moved(PlanStatus.SUPERSEDED, reason=SupersededReason.REJECTED),
            expected_version=target.version,
        )
        self._record(scope, "plan.rejected", rejected, ActorKind.USER)
        self._events.emit(EventName.PLAN_SUPERSEDED, {"version": str(number)})
        if rejected.generator.decision_id is not None:  # an adaptation the athlete refused
            self._lifecycle.advance(scope, Trigger.ADAPTATION_REJECTED)
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
