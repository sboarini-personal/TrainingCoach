"""Plan versions wired with in-memory stores and the real policy gate (P12). The planning
context the gate reads is ``World.planning``: an open one by default, which tests narrow."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from activity_world import MutableClock
from coach.application.coaching import CoachingConditions, CoachingLifecycle
from coach.application.consent import ConsentGate
from coach.application.ownership import OwnerScope
from coach.application.plans import PlanService
from coach.application.policy_gate import PolicyGate
from coach.application.screening import SecretScreen
from coach.domain.athlete.health import HealthDeclaration
from coach.domain.athlete.resources import Equipment, PoolAccess, Resources
from coach.domain.availability.availability import Availability, DayDefaults
from coach.domain.coaching.state import CoachingState, Trigger
from coach.domain.consent.ledger import ConsentLedger
from coach.domain.plans.plan import Generator, GeneratorKind, PlanVersion, TrainingPlan
from coach.infrastructure.memory import InMemoryAuditLog, InMemoryRepository, RecordingEventSink
from coach.policy.plans import PlanningContext
from plan_samples import MONDAY
from samples import ATHLETE

SCOPE = OwnerScope(ATHLETE)
GOAL = UUID(int=0x601)
ENGINE = Generator(kind=GeneratorKind.ENGINE, engine_version="1.0.0")
DECLARED = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def open_planning(today: date = MONDAY, **changes: Any) -> PlanningContext:
    """Every day available for four hours, no health constraint, the usual equipment."""
    context = PlanningContext(
        today=today,
        availability=Availability.new(ATHLETE, defaults=DayDefaults(240)),
        health=HealthDeclaration(declared_at=DECLARED),
        resources=Resources(
            pool=PoolAccess(available=True, length_m=25),
            bicycle=True,
            gym=True,
            strength_equipment=frozenset(
                {Equipment.DUMBBELLS, Equipment.KETTLEBELLS, Equipment.BENCH}
            ),
        ),
    )
    return replace(context, **changes)


class PlanWorld:
    def __init__(
        self,
        headers: Any = None,
        versions: Any = None,
        **kwargs: Any,
    ) -> None:
        self.clock = MutableClock()
        counter = itertools.count(1)
        self.audit = InMemoryAuditLog()
        self.events = RecordingEventSink()
        self.planning = open_planning()
        states = InMemoryRepository[CoachingState]()
        self.conditions = CoachingConditions(
            states, audit=self.audit, events=self.events, clock=self.clock
        )
        self.lifecycle = CoachingLifecycle(
            states, audit=self.audit, events=self.events, clock=self.clock
        )
        self.gate = PolicyGate(
            ConsentGate(InMemoryRepository[ConsentLedger](), policy_version="1.0"),
            states,
            audit=self.audit,
            events=self.events,
            clock=self.clock,
            planning=lambda _scope: self.planning,
        )
        self.headers = headers if headers is not None else InMemoryRepository[TrainingPlan]()
        self.versions = versions if versions is not None else InMemoryRepository[PlanVersion]()
        self.service = PlanService(
            self.headers,
            self.versions,
            lifecycle=self.lifecycle,
            gate=self.gate,
            screen=SecretScreen(self.events),
            audit=self.audit,
            events=self.events,
            clock=self.clock,
            ids=lambda: UUID(int=0x500 + next(counter)),
            **kwargs,
        )

    def to_goal_defined(self) -> None:
        for trigger in (Trigger.CONSENT_DECISIONS_RECORDED, Trigger.REQUIRED_INPUTS_WITH_HISTORY):
            assert self.lifecycle.advance(SCOPE, trigger)
