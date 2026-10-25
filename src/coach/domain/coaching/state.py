"""Coaching state machine (FR-ONB-006, spec/functional/onboarding.spec.md section 4).

Exactly one lifecycle state, plus zero or more conditions. Every change returns an event for
the audit log. SAFETY_HOLD blocks plan activation and is released only by the athlete
(SAFE-MED-005); anyone, including the agent, may activate it (fail-safe).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.errors import InvalidTransitionError
from coach.domain.common.time import ensure_utc


class LifecycleState(StrEnum):
    NEW_USER = "NEW_USER"
    ONBOARDING = "ONBOARDING"
    BASELINE_COLLECTION = "BASELINE_COLLECTION"
    GOAL_DEFINED = "GOAL_DEFINED"
    PLAN_READY = "PLAN_READY"
    TRAINING = "TRAINING"
    ANALYSIS = "ANALYSIS"
    ADAPTATION = "ADAPTATION"


class Condition(StrEnum):
    SAFETY_HOLD = "SAFETY_HOLD"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"
    GOAL_REASSESSMENT = "GOAL_REASSESSMENT"
    RECOVERY_BLOCK = "RECOVERY_BLOCK"


class Trigger(StrEnum):
    CONSENT_DECISIONS_RECORDED = "CONSENT_DECISIONS_RECORDED"
    REQUIRED_INPUTS_WITHOUT_HISTORY = "REQUIRED_INPUTS_WITHOUT_HISTORY"
    REQUIRED_INPUTS_WITH_HISTORY = "REQUIRED_INPUTS_WITH_HISTORY"
    BASELINE_COMPLETED = "BASELINE_COMPLETED"
    PLAN_PROPOSED = "PLAN_PROPOSED"
    PLAN_ACTIVATED = "PLAN_ACTIVATED"
    ANALYSIS_STARTED = "ANALYSIS_STARTED"
    DECISION_MAINTAIN = "DECISION_MAINTAIN"
    DECISION_ADAPT = "DECISION_ADAPT"
    ADAPTED_VERSION_ACTIVATED = "ADAPTED_VERSION_ACTIVATED"
    ADAPTATION_REJECTED = "ADAPTATION_REJECTED"
    GOAL_CLOSED = "GOAL_CLOSED"


class Actor(StrEnum):
    ATHLETE = "ATHLETE"
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"


_L = LifecycleState
TRANSITIONS: Final[dict[tuple[LifecycleState, Trigger], LifecycleState]] = {
    (_L.NEW_USER, Trigger.CONSENT_DECISIONS_RECORDED): _L.ONBOARDING,
    (_L.ONBOARDING, Trigger.REQUIRED_INPUTS_WITHOUT_HISTORY): _L.BASELINE_COLLECTION,
    (_L.ONBOARDING, Trigger.REQUIRED_INPUTS_WITH_HISTORY): _L.GOAL_DEFINED,
    (_L.BASELINE_COLLECTION, Trigger.BASELINE_COMPLETED): _L.GOAL_DEFINED,
    (_L.GOAL_DEFINED, Trigger.PLAN_PROPOSED): _L.PLAN_READY,
    (_L.PLAN_READY, Trigger.PLAN_ACTIVATED): _L.TRAINING,
    (_L.TRAINING, Trigger.ANALYSIS_STARTED): _L.ANALYSIS,
    (_L.ANALYSIS, Trigger.DECISION_MAINTAIN): _L.TRAINING,
    (_L.ANALYSIS, Trigger.DECISION_ADAPT): _L.ADAPTATION,
    (_L.ADAPTATION, Trigger.ADAPTED_VERSION_ACTIVATED): _L.TRAINING,
    (_L.ADAPTATION, Trigger.ADAPTATION_REJECTED): _L.TRAINING,
    (_L.TRAINING, Trigger.GOAL_CLOSED): _L.GOAL_DEFINED,
}
_ACTIVATIONS: Final = frozenset({Trigger.PLAN_ACTIVATED, Trigger.ADAPTED_VERSION_ACTIVATED})


@dataclass(frozen=True, slots=True, kw_only=True)
class CoachingStateChanged:
    """Audit event for a lifecycle transition or a condition change."""

    athlete_id: UUID
    at: datetime
    actor: Actor
    from_state: LifecycleState
    to_state: LifecycleState
    trigger: Trigger | None = None
    condition: Condition | None = None
    activated: bool | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CoachingState:
    athlete_id: UUID
    lifecycle: LifecycleState = LifecycleState.NEW_USER
    conditions: frozenset[Condition] = field(default_factory=frozenset)
    version: int = 1

    @classmethod
    def new(cls, athlete_id: UUID) -> CoachingState:
        return cls(athlete_id=athlete_id)

    @property
    def entity_id(self) -> UUID:
        return self.athlete_id

    def with_version(self, version: int) -> CoachingState:
        return replace(self, version=version)

    def apply(
        self, trigger: Trigger, *, actor: Actor, at: datetime
    ) -> tuple[CoachingState, CoachingStateChanged]:
        target = TRANSITIONS.get((self.lifecycle, trigger))
        if target is None:
            raise InvalidTransitionError(f"{trigger} is not allowed in state {self.lifecycle}")
        if trigger in _ACTIVATIONS and Condition.SAFETY_HOLD in self.conditions:
            raise InvalidTransitionError("plan activation is blocked during SAFETY_HOLD")
        event = CoachingStateChanged(
            athlete_id=self.athlete_id,
            at=ensure_utc(at),
            actor=actor,
            from_state=self.lifecycle,
            to_state=target,
            trigger=trigger,
        )
        return replace(self, lifecycle=target), event

    def activate(
        self, condition: Condition, *, actor: Actor, at: datetime, reason: str | None = None
    ) -> tuple[CoachingState, CoachingStateChanged | None]:
        if condition in self.conditions:
            return self, None
        return self._change(condition, active=True, actor=actor, at=at, reason=reason)

    def clear(
        self, condition: Condition, *, actor: Actor, at: datetime, reason: str | None = None
    ) -> tuple[CoachingState, CoachingStateChanged | None]:
        if condition not in self.conditions:
            return self, None
        if condition is Condition.SAFETY_HOLD and actor is not Actor.ATHLETE:
            raise InvalidTransitionError("only the athlete can release a SAFETY_HOLD")
        return self._change(condition, active=False, actor=actor, at=at, reason=reason)

    def _change(
        self, condition: Condition, *, active: bool, actor: Actor, at: datetime, reason: str | None
    ) -> tuple[CoachingState, CoachingStateChanged]:
        conditions = self.conditions | {condition} if active else self.conditions - {condition}
        event = CoachingStateChanged(
            athlete_id=self.athlete_id,
            at=ensure_utc(at),
            actor=actor,
            from_state=self.lifecycle,
            to_state=self.lifecycle,
            condition=condition,
            activated=active,
            reason=reason,
        )
        return replace(self, conditions=conditions), event
