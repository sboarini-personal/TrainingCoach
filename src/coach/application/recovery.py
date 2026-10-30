"""Recovery: daily check-ins and daily recovery data (recovery spec sections 1 and 5;
FR-REC-001, FR-REC-005).

A check-in is the athlete's own declaration: it needs no category consent (the consent types
cover provider data), its free text is screened for disclosed secrets before it is stored
(SEC-SEC-005), and its red flags activate SAFETY_HOLD at once (SAFE-MED-002). Check-ins are
immutable; a new one for the same training day supersedes the earlier one. Daily recovery
data is read only for the metrics whose consent is granted now: a revocation takes effect on
reads at once (privacy spec 1).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentGate
from coach.application.errors import ApplicationError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, EventSink, IdFactory, OwnedRepository
from coach.application.safety import hold_notice
from coach.application.screening import SecretScreen
from coach.domain.coaching.state import Condition
from coach.domain.common.errors import DomainValidationError
from coach.domain.recovery.checkin import (
    SEVERE_PAIN,
    CheckInPain,
    DailyCheckIn,
    PainOnset,
    RedFlag,
    latest_per_day,
)
from coach.domain.recovery.daily import RecoveryMetric, RecoveryObservation, in_force
from coach.domain.recovery.warning_phrases import detect, names_chest


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
class _Screened:
    notes: str | None
    location: str | None
    notice: str | None
    explicit: tuple[RedFlag, ...]
    detected: tuple[RedFlag, ...]
    raised: tuple[RedFlag, ...]


class CheckInRefusedError(ApplicationError):
    """The check-in could not be recorded; its red flags already activated SAFETY_HOLD."""

    def __init__(self, reason: str, red_flags: tuple[RedFlag, ...], safety_notice: str) -> None:
        super().__init__(reason)
        self.red_flags = red_flags
        self.safety_notice = safety_notice


@dataclass(frozen=True, slots=True)
class CheckInResult:
    checkin: DailyCheckIn
    red_flags: tuple[RedFlag, ...]
    safety_notice: str | None  # fixed text when red flags activated SAFETY_HOLD
    secret_notice: str | None  # fixed text when the free text held a secret (redacted)
    not_recorded: str | None = None  # why only the red flags were recorded


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
        observations: OwnedRepository[RecoveryObservation] | None = None,
        consents: ConsentGate | None = None,
    ) -> None:
        self._observations = observations
        self._consents = consents
        self._checkins = checkins
        self._conditions = conditions
        self._screen = screen
        self._timezone_of = timezone_of
        self._events = events
        self._clock = clock
        self._ids = ids

    def today(self, scope: OwnerScope) -> date:
        return self._clock().astimezone(self._timezone_of(scope)).date()

    def safeguard(
        self, scope: OwnerScope, answers: CheckInAnswers
    ) -> tuple[tuple[RedFlag, ...], str | None]:
        """Activate SAFETY_HOLD for the red flags the answers raise, before anything else is
        checked: their notice, or ``None`` without red flags (fail-safe, safety spec 2)."""
        raised = self._screened(scope, answers).raised
        return raised, hold_notice(raised) if raised else None

    def _screened(self, scope: OwnerScope, answers: CheckInAnswers) -> _Screened:
        notice = None
        notes, location = answers.notes, answers.pain_location
        if notes is not None:
            screened = self._screen.screen(notes, source="checkin")
            notes, notice = screened.text, screened.notice
        if location is not None:
            screened = self._screen.screen(location, source="checkin")
            location, notice = screened.text, notice or screened.notice
        explicit = tuple(dict.fromkeys(answers.red_flags))
        detected = _detected(answers, notes, location)
        raised = tuple(dict.fromkeys((*explicit, *detected)))
        if raised:
            self._conditions.activate(scope, Condition.SAFETY_HOLD, reason=raised[0].value)
        return _Screened(notes, location, notice, explicit, detected, raised)

    def record_checkin(self, scope: OwnerScope, answers: CheckInAnswers) -> CheckInResult:
        """Record the answers. Red flags activate SAFETY_HOLD before anything else can fail:
        if the other answers are invalid, a check-in with the red flags only is recorded and
        the result says why the rest was not; if the training day cannot be known (no
        timezone), ``CheckInRefusedError`` carries the notice (fail-safe, safety spec 2)."""
        screened = self._screened(scope, answers)
        notes, location, notice = screened.notes, screened.location, screened.notice
        explicit, detected, raised = screened.explicit, screened.detected, screened.raised
        try:
            day = answers.training_day or self.today(scope)
        except ApplicationError as error:
            if raised:
                raise CheckInRefusedError(str(error), raised, hold_notice(raised)) from error
            raise
        previous = latest_per_day(self._checkins.list_all(scope)).get(day)
        record = partial(
            DailyCheckIn,
            checkin_id=self._ids(),
            athlete_id=scope.athlete_id,
            recorded_at=self._clock(),
            training_day=day,
            supersedes=None if previous is None else previous.checkin_id,
        )
        not_recorded = None
        try:
            pain, pain_flag = _pain(answers, location)
            checkin = record(
                sleep_quality=answers.sleep_quality,
                fatigue=answers.fatigue,
                soreness=answers.soreness,
                stress=answers.stress,
                motivation=answers.motivation,
                pain_flag=pain_flag,
                pain=pain,
                red_flags=explicit,
                detected_flags=tuple(flag for flag in detected if flag not in explicit),
                notes=notes,
            )
        except DomainValidationError as error:
            if not raised:
                raise
            not_recorded = str(error)
            checkin = record(
                red_flags=explicit,
                detected_flags=tuple(flag for flag in detected if flag not in explicit),
            )
        stored = self._checkins.add(scope, checkin)
        flags = stored.red_flags_raised()
        safety = hold_notice(flags) if flags else None
        self._events.emit(EventName.CHECKIN_RECORDED, {"red_flags": str(len(flags))})
        return CheckInResult(stored, flags, safety, notice, not_recorded)

    def daily(
        self, scope: OwnerScope, *, until: date, days: int
    ) -> dict[date, dict[RecoveryMetric, RecoveryObservation]]:
        """Per day of the period, the recovery metrics in force whose consent is granted."""
        if self._observations is None or self._consents is None:
            return {}
        start = until - timedelta(days=days - 1)
        found = in_force(
            (item for item in self._observations.list_all(scope) if start <= item.day <= until),
            allowed=self._consents.granted(scope),
        )
        return dict(sorted(found.items()))

    def checkins(self, scope: OwnerScope, *, until: date, days: int) -> Sequence[DailyCheckIn]:
        """The check-in in force of each training day of the period, oldest first."""
        start = until - timedelta(days=days - 1)
        found = latest_per_day(self._checkins.list_all(scope))
        return [found[day] for day in sorted(found) if start <= day <= until]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _detected(
    answers: CheckInAnswers, notes: str | None, location: str | None
) -> tuple[RedFlag, ...]:
    """Red flags found in what the athlete wrote or rated: severe pain not known to be
    gradual, a pain located in the chest, warning phrases in the notes (safety spec 2)."""
    found: list[RedFlag] = []
    intensity = answers.pain_intensity
    severe = _is_int(intensity) and intensity is not None and intensity >= SEVERE_PAIN
    if severe and answers.pain_onset is not PainOnset.GRADUAL:
        found.append(RedFlag.RF_SEVERE_ACUTE_PAIN)
    if names_chest(location):  # whatever the intensity says: fail-safe
        found.append(RedFlag.RF_CHEST_PAIN)
    found.extend(detect(notes))
    return tuple(flag for flag in RedFlag if flag in found)


def _pain(answers: CheckInAnswers, location: str | None) -> tuple[CheckInPain | None, bool | None]:
    """The pain and its flag; intensity 0 is no pain, and a location or onset needs a pain."""
    intensity = answers.pain_intensity
    if intensity is None or (_is_int(intensity) and intensity == 0):
        if location is not None or answers.pain_onset is not None:
            raise DomainValidationError("a pain location or onset needs a pain intensity above 0")
        if intensity == 0 and answers.pain_flag is True:
            raise DomainValidationError("pain flagged with intensity 0")
        return None, False if intensity == 0 else answers.pain_flag
    if answers.pain_flag is False:
        raise DomainValidationError("pain intensity given with the pain flag set to no")
    return CheckInPain(intensity=intensity, location=location, onset=answers.pain_onset), True
