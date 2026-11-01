"""The planning context of the plan policies, assembled from stored state (policy spec 5).

The athlete's availability, resources and health declaration as stored, the athlete's local
today, the end of the recovery block (the latest re-evaluation date of the blocking
prescriptions in force today), the regions recent pain restricts (pain rules 1.0.0) and the
ACTIVE version's workouts already done today (execution rules 1.0.0), which are history.
Nothing comes from the caller of a plan action.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date
from uuid import UUID

from coach.application.athlete_inputs import AthleteInputsService
from coach.application.consent import ConsentRequiredError
from coach.application.ownership import OwnerScope
from coach.domain.recovery.assessment import RecoveryPrescription
from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.recovery.pain import (
    PAIN_RESTRICTION_DAYS,
    PainRestriction,
    pain_restriction,
)
from coach.policy.plans import PlanningContext


class PlanningContextSource:
    def __init__(
        self,
        inputs: AthleteInputsService,
        prescriptions: Callable[[OwnerScope, date], Sequence[RecoveryPrescription]],
        *,
        today: Callable[[OwnerScope], date],
        checkins: Callable[[OwnerScope, date, int], Sequence[DailyCheckIn]] | None = None,
        done: Callable[[OwnerScope, date], Iterable[UUID]] | None = None,
    ) -> None:
        self._checkins = checkins
        self._done = done
        self._inputs = inputs
        self._prescriptions = prescriptions
        self._today = today

    def __call__(self, scope: OwnerScope) -> PlanningContext:
        day = self._today(scope)
        profile = self._inputs.profile(scope)
        blocking = [item for item in self._prescriptions(scope, day) if item.blocks_intensity]
        return PlanningContext(
            today=day,
            availability=self._inputs.availability(scope),
            health=None if profile.health is None else profile.health.value,
            resources=None if profile.resources is None else profile.resources.value,
            recovery_block_until=max((item.reevaluate_after for item in blocking), default=None),
            pain=PainRestriction()
            if self._checkins is None
            else pain_restriction(self._checkins(scope, day, PAIN_RESTRICTION_DAYS), day),
            done=self._done_from(scope, day),
        )

    def _done_from(self, scope: OwnerScope, day: date) -> frozenset[UUID]:
        """Work already done, which the policies read as history; without the activities
        consent nothing counts as done, so every workout is checked (strict)."""
        if self._done is None:
            return frozenset()
        try:
            return frozenset(self._done(scope, day))
        except ConsentRequiredError:
            return frozenset()
