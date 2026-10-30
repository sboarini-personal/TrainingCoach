"""The day's recovery assessment and prescription (recovery spec sections 2 to 4;
FR-REC-002 to FR-REC-005).

Assessing reads the recovery metrics in force whose consent is granted now (a metric without
it is NO_CONSENT and the assessment lists MISSING_DATA, AT-CON-001), the check-ins in force
and the SAFETY_HOLD condition, and applies assessment rules 1.0.0
(``coach.domain.recovery.assessment``). An assessment equal to the day's one in force is not
stored again; another result is stored and, for a major decision, so is its prescription. A
re-assessment of the day withdraws the earlier prescription of that day. RECOVERY_BLOCK
follows the prescriptions in force: REST or ACTIVE_RECOVERY activates it, and the
re-evaluation (an assessment on or after ``reevaluateAfter``, or a re-assessment withdrawing
it) clears it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentGate
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, EventSink, IdFactory, OwnedRepository
from coach.application.recovery import RecoveryService
from coach.domain.coaching.state import Condition
from coach.domain.recovery.assessment import (
    BASELINE_DAYS,
    HISTORY_DAYS,
    RecoveryAssessment,
    RecoveryPrescription,
    assess,
    in_force_assessments,
    prescribe,
    prescriptions_in_force,
)
from coach.domain.recovery.daily import METRICS, RecoveryMetric, RecoveryObservation

REEVALUATED: Final = "REEVALUATED"


@dataclass(frozen=True, slots=True)
class AssessmentResult:
    assessment: RecoveryAssessment
    prescriptions: tuple[RecoveryPrescription, ...]  # in force today, most conservative first
    recovery_block: bool
    stored: bool  # a new assessment was stored


class RecoveryAssessmentService:
    def __init__(
        self,
        recovery: RecoveryService,
        assessments: OwnedRepository[RecoveryAssessment],
        prescriptions: OwnedRepository[RecoveryPrescription],
        *,
        consents: ConsentGate,
        conditions: CoachingConditions,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._recovery = recovery
        self._assessments = assessments
        self._prescriptions = prescriptions
        self._consents = consents
        self._conditions = conditions
        self._events = events
        self._clock = clock
        self._ids = ids

    def assess(self, scope: OwnerScope) -> AssessmentResult:
        """Assess today (the athlete's timezone) and bring RECOVERY_BLOCK up to date."""
        day = self._recovery.today(scope)
        granted = self._consents.granted(scope)
        consented = frozenset(
            metric for metric in RecoveryMetric if METRICS[metric].consent in granted
        )
        readings: dict[RecoveryMetric, dict[date, RecoveryObservation]] = {}
        for when, metrics in self._recovery.daily(scope, until=day, days=HISTORY_DAYS).items():
            for metric, observation in metrics.items():
                readings.setdefault(metric, {})[when] = observation
        checkins = {
            item.training_day: item
            for item in self._recovery.checkins(scope, until=day, days=BASELINE_DAYS)
        }
        conditions = self._conditions.current(scope).conditions
        candidate = assess(
            assessment_id=self._ids(),
            athlete_id=scope.athlete_id,
            day=day,
            readings=readings,
            consented=consented,
            checkins=checkins,
            safety_hold=Condition.SAFETY_HOLD in conditions,
            assessed_at=self._clock(),
        )
        in_force = in_force_assessments(self._assessments.list_all(scope))
        current = in_force.get(day)
        stored = current is None or not current.same_result(candidate)
        if stored:
            current = self._assessments.add(scope, candidate)
            in_force[day] = current
            self._events.emit(
                EventName.RECOVERY_ASSESSED,
                {"status": current.status.value, "confidence": current.confidence.value},
            )
            prescription = prescribe(current, prescription_id=self._ids(), created_at=self._clock())
            if prescription is not None:
                self._prescriptions.add(scope, prescription)
                self._events.emit(EventName.RECOVERY_PRESCRIBED, {"type": prescription.type.value})
        assert current is not None  # noqa: S101 - stored or found above
        found = prescriptions_in_force(self._prescriptions.list_all(scope), in_force, day)
        block = self._recovery_block(scope, found)
        return AssessmentResult(current, found, block, stored)

    def prescriptions(self, scope: OwnerScope, day: date) -> tuple[RecoveryPrescription, ...]:
        """The prescriptions in force on a day, most conservative first (for planning)."""
        in_force = in_force_assessments(self._assessments.list_all(scope))
        return prescriptions_in_force(self._prescriptions.list_all(scope), in_force, day)

    def history(self, scope: OwnerScope, *, until: date, days: int) -> Sequence[RecoveryAssessment]:
        """The assessment in force of each day of the period, oldest first."""
        start = until - timedelta(days=days - 1)
        found = in_force_assessments(self._assessments.list_all(scope))
        return [found[day] for day in sorted(found) if start <= day <= until]

    def _recovery_block(
        self, scope: OwnerScope, prescriptions: Sequence[RecoveryPrescription]
    ) -> bool:
        blocking = [item for item in prescriptions if item.blocks_intensity]
        active = Condition.RECOVERY_BLOCK in self._conditions.current(scope).conditions
        if blocking and not active:
            self._conditions.activate(
                scope, Condition.RECOVERY_BLOCK, reason=blocking[0].type.value
            )
        elif not blocking and active:
            self._conditions.clear(scope, Condition.RECOVERY_BLOCK, reason=REEVALUATED)
        return bool(blocking)
