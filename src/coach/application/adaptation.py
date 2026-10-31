"""The adaptive loop (planning spec 7; FR-ADP-001 to FR-ADP-004, NFR-EXP-001).

A run reads the ACTIVE version, the executions and activities of the last 14 days, the
recovery assessment and prescriptions in force, the check-ins of the last 7 days, the
availability and the coaching conditions; decides by adaptation rules 1.0.0; and records the
decision with its evidence, interpretation, uncertainty and confidence, whatever it is. A
decision other than MAINTAIN proposes a new version of the plan (FR-PLN-002) through
``PlanService``, so the goal, readiness and every plan policy are checked: the adapted
content (parent: the ACTIVE version; it replaces a pending proposal), or for PROGRESS a version
regenerated from the updated history. The proposed version names the decision. The
lifecycle goes TRAINING -> ANALYSIS -> TRAINING (MAINTAIN) or ADAPTATION where the state
machine allows it. The run is audited (`plan.adaptation_decided`) and emitted
(`plan.adaptation.decided`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from uuid import UUID

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.coaching import CoachingConditions, CoachingLifecycle
from coach.application.events import EventName
from coach.application.executions import ExecutionService
from coach.application.ownership import OwnerScope
from coach.application.plan_generation import PlanGenerationService
from coach.application.plans import PlanService
from coach.application.ports import AuditLog, Clock, EventSink, IdFactory, OwnedRepository
from coach.domain.availability.availability import Availability
from coach.domain.coaching.state import Condition, Trigger
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import DateRange
from coach.domain.onboarding.readiness import Readiness, ReadinessStatus
from coach.domain.plans.adaptation import (
    ADAPTATION_RULES_VERSION,
    AdaptationDecision,
    AdaptationTrigger,
    Decision,
)
from coach.domain.plans.plan import Generator, GeneratorKind, PlanContent, PlanStatus, PlanVersion
from coach.domain.recovery.assessment import RecoveryAssessment, RecoveryPrescription
from coach.domain.recovery.checkin import DailyCheckIn
from coach.planning.adaptation import LOOKBACK_DAYS, PAIN_FREE_DAYS, AdaptationInputs, decide

ASSESSMENT_DAYS = 2  # the assessment in force: today's, else yesterday's
PROVISIONAL_NOTE = "Some preferred inputs are missing now: this adapted version is provisional."


@dataclass(frozen=True, slots=True)
class AdaptationResult:
    decision: AdaptationDecision
    proposed: PlanVersion | None


class AdaptationService:
    def __init__(
        self,
        plans: PlanService,
        executions: ExecutionService,
        generation: PlanGenerationService,
        decisions: OwnedRepository[AdaptationDecision],
        *,
        assessments: Callable[[OwnerScope, date, int], Sequence[RecoveryAssessment]],
        prescriptions: Callable[[OwnerScope, date], Sequence[RecoveryPrescription]],
        checkins: Callable[[OwnerScope, date, int], Sequence[DailyCheckIn]],
        availability: Callable[[OwnerScope], Availability | None],
        readiness: Callable[[OwnerScope], Readiness],
        conditions: CoachingConditions,
        lifecycle: CoachingLifecycle,
        today: Callable[[OwnerScope], date],
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._plans = plans
        self._executions = executions
        self._generation = generation
        self._decisions = decisions
        self._assessments = assessments
        self._prescriptions = prescriptions
        self._checkins = checkins
        self._availability = availability
        self._readiness = readiness
        self._conditions = conditions
        self._lifecycle = lifecycle
        self._today = today
        self._audit = audit
        self._events = events
        self._clock = clock
        self._ids = ids

    def decisions(self, scope: OwnerScope) -> list[AdaptationDecision]:
        return sorted(self._decisions.list_all(scope), key=lambda item: item.decided_at)

    def run(
        self, scope: OwnerScope, trigger: AdaptationTrigger = AdaptationTrigger.ATHLETE_REQUEST
    ) -> AdaptationResult:
        active = self._plans.active(scope)
        if active is None:
            raise DomainValidationError("there is no ACTIVE plan to adapt")
        today = self._today(scope)
        period = DateRange(today - timedelta(days=LOOKBACK_DAYS - 1), today)
        executions, done = self._executions.recent(scope, period)
        recent = self._assessments(scope, today, ASSESSMENT_DAYS)
        outcome = decide(
            AdaptationInputs(
                today=today,
                content=active.content,
                executions=tuple(executions),
                done=done,
                assessment=recent[-1] if recent else None,
                prescriptions=tuple(self._prescriptions(scope, today)),
                checkins=tuple(self._checkins(scope, today, PAIN_FREE_DAYS)),
                availability=self._availability(scope),
                safety_hold=Condition.SAFETY_HOLD in self._conditions.current(scope).conditions,
            )
        )
        self._lifecycle.advance(scope, Trigger.ANALYSIS_STARTED)
        decision_id = self._ids()
        proposed = None
        if outcome.decision is Decision.PROGRESS:
            proposed = self._generation.generate(scope, active.goal_id, decision_id=decision_id)
        elif outcome.decision is not Decision.MAINTAIN and outcome.content is not None:
            proposed = self._propose(scope, active, outcome.content, decision_id)
        self._lifecycle.advance(
            scope, Trigger.DECISION_MAINTAIN if proposed is None else Trigger.DECISION_ADAPT
        )
        stored = self._decisions.add(
            scope,
            AdaptationDecision(
                decision_id=decision_id,
                athlete_id=scope.athlete_id,
                decided_at=self._clock(),
                training_day=today,
                trigger=trigger,
                decision=outcome.decision,
                reasons=outcome.reasons,
                evidence=outcome.evidence,
                interpretation=outcome.interpretation,
                uncertainty_reasons=outcome.uncertainty,
                confidence=outcome.confidence,
                affected_workouts=outcome.affected,
                plan_id=active.plan_id,
                from_version=active.number,
                proposed_version=None if proposed is None else proposed.number,
            ),
        )
        self._audit.append(
            AuditEvent(
                occurred_at=self._clock(),
                actor=ActorKind.SYSTEM,
                action="plan.adaptation_decided",
                outcome=AuditOutcome.SUCCESS,
                athlete_id=scope.athlete_id,
                target_type="ADAPTATION_DECISION",
                target_id=str(decision_id),
                reason_codes=(outcome.decision.value, *(item.value for item in outcome.reasons)),
            )
        )
        self._events.emit(
            EventName.ADAPTATION_DECIDED,
            {"decision": outcome.decision.value, "trigger": trigger.value},
        )
        return AdaptationResult(stored, proposed)

    def _propose(
        self, scope: OwnerScope, active: PlanVersion, content: PlanContent, decision_id: UUID
    ) -> PlanVersion:
        ready = self._readiness(scope)
        if ready.status is ReadinessStatus.PROVISIONAL and not content.provisional:
            content = replace(
                content, provisional=True, assumptions=(*content.assumptions, PROVISIONAL_NOTE)
            )
        header = self._plans.plan(scope, active.plan_id)
        latest = self._plans.version(scope, active.plan_id, header.latest_number)
        return self._plans.propose(
            scope,
            goal_id=active.goal_id,
            content=content,
            generator=Generator(
                kind=GeneratorKind.ENGINE,
                engine_version=ADAPTATION_RULES_VERSION,
                decision_id=decision_id,
            ),
            plan_id=active.plan_id,
            parent=active.number,
            replaces=latest.number if latest.status is PlanStatus.PROPOSED else None,
        )
