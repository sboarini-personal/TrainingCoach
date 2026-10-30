"""Recovery: daily check-ins (recovery spec section 1; FR-REC-001).

A check-in is the athlete's own declaration: it needs no category consent (the consent types
cover provider data), its free text is screened for disclosed secrets before it is stored
(SEC-SEC-005), and its red flags activate SAFETY_HOLD at once (SAFE-MED-002). Check-ins are
immutable; a new one for the same training day supersedes the earlier one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from coach.application.coaching import CoachingConditions
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, EventSink, IdFactory, OwnedRepository
from coach.application.safety import hold_notice
from coach.application.screening import SecretScreen
from coach.domain.coaching.state import Condition
from coach.domain.recovery.checkin import (
    CheckInPain,
    DailyCheckIn,
    PainOnset,
    RedFlag,
    latest_per_day,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckInAnswers:
    """What the athlete answered; ``None`` is not answered."""

    training_day: date | None = None  # default: today in the athlete's timezone
    sleep_quality: int | None = None
    fatigue: int | None = None
    soreness: int | None = None
    stress: int | None = None
    motivation: int | None = None
    pain_flag: bool | None = None
    pain_intensity: int | None = None
    pain_location: str | None = None
    pain_onset: PainOnset | None = None
    red_flags: tuple[RedFlag, ...] = ()
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CheckInResult:
    checkin: DailyCheckIn
    red_flags: tuple[RedFlag, ...]
    safety_notice: str | None  # fixed text when red flags activated SAFETY_HOLD
    secret_notice: str | None  # fixed text when the free text held a secret (redacted)


class RecoveryService:
    def __init__(
        self,
        checkins: OwnedRepository[DailyCheckIn],
        *,
        conditions: CoachingConditions,
        screen: SecretScreen,
        timezone_of: Callable[[OwnerScope], ZoneInfo],
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._checkins = checkins
        self._conditions = conditions
        self._screen = screen
        self._timezone_of = timezone_of
        self._events = events
        self._clock = clock
        self._ids = ids

    def today(self, scope: OwnerScope) -> date:
        return self._clock().astimezone(self._timezone_of(scope)).date()

    def record_checkin(self, scope: OwnerScope, answers: CheckInAnswers) -> CheckInResult:
        day = answers.training_day or self.today(scope)
        notice = None
        notes, location = answers.notes, answers.pain_location
        if notes is not None:
            screened = self._screen.screen(notes, source="checkin")
            notes, notice = screened.text, screened.notice
        if location is not None:
            screened = self._screen.screen(location, source="checkin")
            location, notice = screened.text, notice or screened.notice
        pain = None
        if answers.pain_intensity is not None:
            pain = CheckInPain(
                intensity=answers.pain_intensity, location=location, onset=answers.pain_onset
            )
        previous = latest_per_day(self._checkins.list_all(scope)).get(day)
        checkin = DailyCheckIn(
            checkin_id=self._ids(),
            athlete_id=scope.athlete_id,
            recorded_at=self._clock(),
            training_day=day,
            sleep_quality=answers.sleep_quality,
            fatigue=answers.fatigue,
            soreness=answers.soreness,
            stress=answers.stress,
            motivation=answers.motivation,
            pain_flag=True if pain is not None else answers.pain_flag,
            pain=pain,
            red_flags=answers.red_flags,
            notes=notes,
            supersedes=None if previous is None else previous.checkin_id,
        )
        stored = self._checkins.add(scope, checkin)
        flags = stored.red_flags_raised()
        safety = None
        if flags:
            self._conditions.activate(scope, Condition.SAFETY_HOLD, reason=flags[0].value)
            safety = hold_notice(flags)
        self._events.emit(EventName.CHECKIN_RECORDED, {"red_flags": str(len(flags))})
        return CheckInResult(stored, flags, safety, notice)

    def checkins(self, scope: OwnerScope, *, until: date, days: int) -> Sequence[DailyCheckIn]:
        """The check-in in force of each training day of the period, oldest first."""
        start = until - timedelta(days=days - 1)
        found = latest_per_day(self._checkins.list_all(scope))
        return [found[day] for day in sorted(found) if start <= day <= until]
