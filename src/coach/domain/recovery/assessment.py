"""Recovery assessment and prescription, rules 1.0.0 (recovery spec sections 2 to 4;
FR-REC-002 to FR-REC-004; parameters are initial values under assumption A-18).

The signals of the assessed training day, each against the athlete's own reference:

- a provider metric, only with its consent: the day's reading against its rolling baseline,
  the 28 days before the day, usable from 7 readings. It deviates when worse than the
  baseline mean by more than one standard deviation (with a floor per metric, so a very
  steady baseline does not turn noise into a deviation) and is favourable when better by as
  much;
- a check-in item, against the documented anchors of its scale (recovery spec 1): fatigue,
  soreness or stress 4 or more, sleep quality or motivation 2 or less;
- pain: any pain the athlete reports.

Signals of one physiology count once: the autonomic group (resting heart rate, overnight HRV
and Body Battery, which is computed from them) and the sleep group (measured sleep and sleep
quality). A major decision needs two independent deviating groups (REDUCE; three or more:
REST), one signal deviating on three consecutive days (REDUCE) or the pain rule (pain of 7 or
more that is not a red flag: REDUCE). One isolated deviation is an observation (MONITOR,
AT-REC-001). Wearables never cancel what the athlete reports: both count, so the more
conservative wins, and a disagreement is reported (AT-REC-002). Pain below the red-flag
threshold is a signal; restricting load on its region is the policy engine's (safety spec 5,
P12). Pain reported for more than 7 days, or growing, is reported so that professional
evaluation is suggested. A safety hold decides REST by itself and its own rules apply. These
are training-load decisions, never diagnoses.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.time import ensure_utc
from coach.domain.recovery.checkin import (
    HIGHER_IS_BETTER,
    SEVERE_PAIN,
    CheckInItem,
    DailyCheckIn,
)
from coach.domain.recovery.daily import METRICS, RecoveryMetric, RecoveryObservation

ASSESSMENT_RULES_VERSION: Final = "1.0.0"
BASELINE_DAYS: Final = 28
MIN_BASELINE_READINGS: Final = 7
DEVIATION_SD: Final = 1.0
PERSISTENT_DAYS: Final = 3
PAIN_EPISODE_DAYS: Final = 7  # pain beyond this suggests professional evaluation
HISTORY_DAYS: Final = BASELINE_DAYS + PERSISTENT_DAYS  # the readings an assessment reads
SD_FLOOR: Final[Mapping[RecoveryMetric, float]] = {
    RecoveryMetric.RESTING_HR: 2.0,
    RecoveryMetric.HRV_NIGHTLY: 3.0,
    RecoveryMetric.SLEEP_DURATION: 1800.0,
    RecoveryMetric.BODY_BATTERY_CHARGED: 5.0,
}
WORSE_BELOW: Final = 2  # sleep quality, motivation: 2 or less deviates
WORSE_ABOVE: Final = 4  # fatigue, soreness, stress: 4 or more deviates
PAIN_SIGNAL: Final = "pain"
WEARABLE: Final = frozenset(metric.value for metric in RecoveryMetric)


class Group(StrEnum):
    AUTONOMIC = "AUTONOMIC"
    SLEEP = "SLEEP"
    FATIGUE = "FATIGUE"
    SORENESS = "SORENESS"
    STRESS = "STRESS"
    MOTIVATION = "MOTIVATION"
    PAIN = "PAIN"


GROUP_OF: Final[Mapping[str, Group]] = {
    RecoveryMetric.RESTING_HR.value: Group.AUTONOMIC,
    RecoveryMetric.HRV_NIGHTLY.value: Group.AUTONOMIC,
    RecoveryMetric.BODY_BATTERY_CHARGED.value: Group.AUTONOMIC,
    RecoveryMetric.SLEEP_DURATION.value: Group.SLEEP,
    CheckInItem.SLEEP_QUALITY.value: Group.SLEEP,
    CheckInItem.FATIGUE.value: Group.FATIGUE,
    CheckInItem.SORENESS.value: Group.SORENESS,
    CheckInItem.STRESS.value: Group.STRESS,
    CheckInItem.MOTIVATION.value: Group.MOTIVATION,
    PAIN_SIGNAL: Group.PAIN,
}


class SignalState(StrEnum):
    DEVIATING = "DEVIATING"
    NORMAL = "NORMAL"
    FAVOURABLE = "FAVOURABLE"
    NO_BASELINE = "NO_BASELINE"  # fewer than 7 readings in the 28 days before
    MISSING = "MISSING"
    NO_CONSENT = "NO_CONSENT"


ASSESSED: Final = frozenset({SignalState.DEVIATING, SignalState.NORMAL, SignalState.FAVOURABLE})


class RecoveryStatus(StrEnum):
    NORMAL = "NORMAL"
    MONITOR = "MONITOR"  # one isolated deviation: an observation, no major decision
    REDUCE = "REDUCE"
    REST = "REST"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Reason(StrEnum):
    SAFETY_HOLD = "SAFETY_HOLD"
    THREE_GROUPS = "THREE_GROUPS"
    SEVERE_PAIN = "SEVERE_PAIN"
    TWO_GROUPS = "TWO_GROUPS"
    PERSISTENT_SIGNAL = "PERSISTENT_SIGNAL"
    ISOLATED_SIGNAL = "ISOLATED_SIGNAL"
    NO_DEVIATION = "NO_DEVIATION"
    NO_SIGNAL = "NO_SIGNAL"
    # observations reported with any status
    WEARABLE_SUBJECTIVE_CONFLICT = "WEARABLE_SUBJECTIVE_CONFLICT"
    PAIN_REPORTED = "PAIN_REPORTED"
    PAIN_PERSISTENT = "PAIN_PERSISTENT"
    PAIN_WORSENING = "PAIN_WORSENING"


@dataclass(frozen=True, slots=True, kw_only=True)
class Signal:
    name: str  # a RecoveryMetric, a check-in item or "pain"
    state: SignalState
    value: float | None = None
    baseline_mean: float | None = None
    baseline_sd: float | None = None  # with the metric's floor applied
    baseline_readings: int = 0
    consecutive_days: int = 0  # deviating days in a row, the assessed day included
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if self.name not in GROUP_OF:
            raise DomainValidationError("unknown recovery signal")

    @property
    def group(self) -> Group:
        return GROUP_OF[self.name]

    @property
    def wearable(self) -> bool:
        return self.name in WEARABLE


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryAssessment:
    assessment_id: UUID
    athlete_id: UUID
    day: date
    status: RecoveryStatus
    reasons: tuple[Reason, ...]  # the decisive reason first, then the observations
    signals: tuple[Signal, ...]
    pain: int | None  # the day's pain intensity
    confidence: Confidence
    uncertainty_reasons: frozenset[UncertaintyReason]
    assessed_at: datetime
    rules_version: str = ASSESSMENT_RULES_VERSION
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "assessed_at", ensure_utc(self.assessed_at))
        if not self.reasons:
            raise DomainValidationError("an assessment states its reason")

    @property
    def entity_id(self) -> UUID:
        return self.assessment_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)

    @property
    def major(self) -> bool:
        return self.status in (RecoveryStatus.REDUCE, RecoveryStatus.REST)

    @property
    def conflict(self) -> bool:
        return Reason.WEARABLE_SUBJECTIVE_CONFLICT in self.reasons

    @property
    def evidence(self) -> tuple[EvidenceRef, ...]:
        return tuple(dict.fromkeys(ref for signal in self.signals for ref in signal.evidence))

    def same_result(self, other: RecoveryAssessment) -> bool:
        """The same day, status, reasons and signals: an unchanged re-assessment."""
        mine = (self.day, self.status, self.reasons, self.signals, self.rules_version)
        return mine == (other.day, other.status, other.reasons, other.signals, other.rules_version)


# --------------------------------------------------------------------------- signals


def _direction(metric: RecoveryMetric, value: float, mean: float, sd: float) -> SignalState:
    gap = (value - mean) / sd
    if not METRICS[metric].higher_is_better:
        gap = -gap
    if gap < -DEVIATION_SD:
        return SignalState.DEVIATING
    if gap > DEVIATION_SD:
        return SignalState.FAVOURABLE
    return SignalState.NORMAL


def _baseline(
    readings: Mapping[date, RecoveryObservation], day: date
) -> tuple[float, float, int] | None:
    """Mean, standard deviation and count of the readings of the 28 days before the day."""
    window = [
        readings[earlier].value
        for offset in range(1, BASELINE_DAYS + 1)
        if (earlier := day - timedelta(days=offset)) in readings
    ]
    if len(window) < MIN_BASELINE_READINGS:
        return None
    return statistics.fmean(window), statistics.pstdev(window), len(window)


def _metric_state(
    metric: RecoveryMetric, readings: Mapping[date, RecoveryObservation], day: date
) -> SignalState:
    observation = readings.get(day)
    if observation is None:
        return SignalState.MISSING
    baseline = _baseline(readings, day)
    if baseline is None:
        return SignalState.NO_BASELINE
    mean, sd, _ = baseline
    return _direction(metric, observation.value, mean, max(sd, SD_FLOOR[metric]))


def _streak(deviates: Callable[[date], bool], day: date) -> int:
    count = 0
    while count < PERSISTENT_DAYS and deviates(day - timedelta(days=count)):
        count += 1
    return count


def _wearable(
    metric: RecoveryMetric, readings: Mapping[date, RecoveryObservation], day: date
) -> Signal:
    state = _metric_state(metric, readings, day)
    observation = readings.get(day)
    if observation is None:
        return Signal(name=metric.value, state=state)
    baseline = _baseline(readings, day)
    mean, sd, count = baseline if baseline is not None else (None, None, 0)
    return Signal(
        name=metric.value,
        state=state,
        value=observation.value,
        baseline_mean=mean,
        baseline_sd=None if sd is None else max(sd, SD_FLOOR[metric]),
        baseline_readings=count,
        consecutive_days=_streak(
            lambda earlier: _metric_state(metric, readings, earlier) is SignalState.DEVIATING, day
        ),
        evidence=(EvidenceRef(EvidenceKind.DAILY_RECOVERY, observation.observation_id),),
    )


def _item_deviates(item: CheckInItem, checkin: DailyCheckIn | None) -> bool | None:
    value = None if checkin is None else checkin.item(item)
    if value is None:
        return None
    return value <= WORSE_BELOW if HIGHER_IS_BETTER[item] else value >= WORSE_ABOVE


def _subjective(item: CheckInItem, checkins: Mapping[date, DailyCheckIn], day: date) -> Signal:
    checkin = checkins.get(day)
    deviates = _item_deviates(item, checkin)
    if checkin is None or deviates is None:
        return Signal(name=item.value, state=SignalState.MISSING)
    value = checkin.item(item)
    return Signal(
        name=item.value,
        state=SignalState.DEVIATING if deviates else SignalState.NORMAL,
        value=None if value is None else float(value),
        consecutive_days=_streak(
            lambda earlier: _item_deviates(item, checkins.get(earlier)) is True, day
        ),
        evidence=(EvidenceRef(EvidenceKind.DAILY_CHECKIN, checkin.checkin_id),),
    )


def _pain_deviates(checkin: DailyCheckIn | None) -> bool | None:
    if checkin is None or checkin.pain_flag is None:
        return None
    if not checkin.pain_flag:
        return False
    return checkin.pain is None or checkin.pain.intensity > 0


def _pain(checkins: Mapping[date, DailyCheckIn], day: date) -> Signal:
    checkin = checkins.get(day)
    deviates = _pain_deviates(checkin)
    if checkin is None or deviates is None:
        return Signal(name=PAIN_SIGNAL, state=SignalState.MISSING)
    return Signal(
        name=PAIN_SIGNAL,
        state=SignalState.DEVIATING if deviates else SignalState.NORMAL,
        value=None if checkin.pain is None else float(checkin.pain.intensity),
        consecutive_days=_streak(
            lambda earlier: _pain_deviates(checkins.get(earlier)) is True, day
        ),
        evidence=(EvidenceRef(EvidenceKind.DAILY_CHECKIN, checkin.checkin_id),),
    )


def _pain_episode(checkins: Mapping[date, DailyCheckIn], day: date) -> list[Reason]:
    """Pain reported for more than 7 days, or growing since the episode began (safety spec 5).

    The episode is the run of check-ins reporting pain up to the day; a check-in saying there
    is no pain ends it, one that does not answer the question does not."""
    if _pain_deviates(checkins.get(day)) is not True:
        return []
    episode = [day]
    for earlier in sorted((item for item in checkins if item < day), reverse=True):
        state = _pain_deviates(checkins[earlier])
        if state is False:
            break
        if state is True:
            episode.append(earlier)
    reasons = []
    if (day - episode[-1]).days >= PAIN_EPISODE_DAYS:
        reasons.append(Reason.PAIN_PERSISTENT)
    intensities = [
        pain.intensity for when in reversed(episode) if (pain := checkins[when].pain) is not None
    ]
    if len(intensities) > 1 and intensities[-1] > intensities[0]:
        reasons.append(Reason.PAIN_WORSENING)
    return reasons


# --------------------------------------------------------------------------- assessment


def _conflict(signals: Sequence[Signal]) -> bool:
    """A wearable and the athlete disagree: one deviates while the other does not."""
    wearables = [item.state for item in signals if item.wearable and item.state in ASSESSED]
    athlete = [
        item.state
        for item in signals
        if not item.wearable and item.group is not Group.PAIN and item.state in ASSESSED
    ]
    if not wearables or not athlete:
        return False
    return (SignalState.DEVIATING in wearables) != (SignalState.DEVIATING in athlete)


def _decision(
    signals: Sequence[Signal], pain: int | None, *, safety_hold: bool
) -> tuple[RecoveryStatus, Reason]:
    deviating = [item for item in signals if item.state is SignalState.DEVIATING]
    groups = {item.group for item in deviating}
    if safety_hold:
        return RecoveryStatus.REST, Reason.SAFETY_HOLD
    if len(groups) >= 3:
        return RecoveryStatus.REST, Reason.THREE_GROUPS
    if pain is not None and pain >= SEVERE_PAIN:
        return RecoveryStatus.REDUCE, Reason.SEVERE_PAIN
    if len(groups) == 2:
        return RecoveryStatus.REDUCE, Reason.TWO_GROUPS
    if any(item.consecutive_days >= PERSISTENT_DAYS for item in deviating):
        return RecoveryStatus.REDUCE, Reason.PERSISTENT_SIGNAL
    if groups:
        return RecoveryStatus.MONITOR, Reason.ISOLATED_SIGNAL
    if any(item.state in ASSESSED for item in signals):
        return RecoveryStatus.NORMAL, Reason.NO_DEVIATION
    return RecoveryStatus.INSUFFICIENT_DATA, Reason.NO_SIGNAL


def _confidence(signals: Sequence[Signal]) -> Confidence:
    assessed = [item for item in signals if item.state in ASSESSED]
    groups = {item.group for item in assessed}
    both = any(item.wearable for item in assessed) and any(not item.wearable for item in assessed)
    if len(groups) >= 3 and both:
        return Confidence.HIGH
    if len(groups) >= 2:
        return Confidence.MEDIUM
    return Confidence.LOW


def assess(
    *,
    assessment_id: UUID,
    athlete_id: UUID,
    day: date,
    readings: Mapping[RecoveryMetric, Mapping[date, RecoveryObservation]],
    consented: frozenset[RecoveryMetric],
    checkins: Mapping[date, DailyCheckIn],
    safety_hold: bool,
    assessed_at: datetime,
) -> RecoveryAssessment:
    """The day's assessment from the readings in force of the consented metrics (a metric
    without consent is NO_CONSENT, whatever was passed) and the check-ins in force."""
    signals: list[Signal] = []
    for metric in RecoveryMetric:
        if metric not in consented:
            signals.append(Signal(name=metric.value, state=SignalState.NO_CONSENT))
        else:
            signals.append(_wearable(metric, readings.get(metric, {}), day))
    signals.extend(_subjective(item, checkins, day) for item in CheckInItem)
    signals.append(_pain(checkins, day))
    today = checkins.get(day)
    pain = None if today is None or today.pain is None else today.pain.intensity
    status, decisive = _decision(signals, pain, safety_hold=safety_hold)
    reasons = [decisive]
    if _conflict(signals):
        reasons.append(Reason.WEARABLE_SUBJECTIVE_CONFLICT)
    if _pain_deviates(today) is True:
        reasons.append(Reason.PAIN_REPORTED)
        reasons.extend(_pain_episode(checkins, day))
    uncertain: set[UncertaintyReason] = set()
    if any(item.state in (SignalState.MISSING, SignalState.NO_CONSENT) for item in signals):
        uncertain.add(UncertaintyReason.MISSING_DATA)
    if any(item.state is SignalState.NO_BASELINE for item in signals):
        uncertain.add(UncertaintyReason.INSUFFICIENT_HISTORY)
    return RecoveryAssessment(
        assessment_id=assessment_id,
        athlete_id=athlete_id,
        day=day,
        status=status,
        reasons=tuple(reasons),
        signals=tuple(signals),
        pain=pain,
        confidence=_confidence(signals),
        uncertainty_reasons=frozenset(uncertain),
        assessed_at=assessed_at,
    )


def in_force_assessments(
    assessments: Iterable[RecoveryAssessment],
) -> dict[date, RecoveryAssessment]:
    """The assessment in force for each day: the latest one."""
    found: dict[date, RecoveryAssessment] = {}
    for item in sorted(
        assessments, key=lambda entry: (entry.assessed_at, str(entry.assessment_id))
    ):
        found[item.day] = item
    return found


# --------------------------------------------------------------------------- prescription


class PrescriptionType(StrEnum):
    REST = "REST"
    ACTIVE_RECOVERY = "ACTIVE_RECOVERY"
    EASY_SESSION = "EASY_SESSION"
    REDUCED_VOLUME = "REDUCED_VOLUME"


SEVERITY: Final = (
    PrescriptionType.REST,
    PrescriptionType.ACTIVE_RECOVERY,
    PrescriptionType.EASY_SESSION,
    PrescriptionType.REDUCED_VOLUME,
)
BLOCKING: Final = frozenset({PrescriptionType.REST, PrescriptionType.ACTIVE_RECOVERY})
PRESCRIBED: Final[Mapping[Reason, tuple[PrescriptionType, int]]] = {
    Reason.THREE_GROUPS: (PrescriptionType.REST, 1),
    Reason.SEVERE_PAIN: (PrescriptionType.ACTIVE_RECOVERY, 1),
    Reason.TWO_GROUPS: (PrescriptionType.EASY_SESSION, 2),
    Reason.PERSISTENT_SIGNAL: (PrescriptionType.REDUCED_VOLUME, 2),
}
MAX_DURATION_DAYS: Final = 14


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPrescription:
    """A training-workload decision, not medical treatment (recovery spec 4)."""

    prescription_id: UUID
    athlete_id: UUID
    assessment_id: UUID
    day: date
    type: PrescriptionType
    reason: Reason
    evidence: tuple[EvidenceRef, ...]
    duration_days: int
    reevaluate_after: date
    created_at: datetime
    rules_version: str = ASSESSMENT_RULES_VERSION
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if not self.evidence:
            raise DomainValidationError("a prescription states its evidence")
        if (
            isinstance(self.duration_days, bool)
            or not isinstance(self.duration_days, int)
            or not 1 <= self.duration_days <= MAX_DURATION_DAYS
        ):
            raise DomainValidationError(f"a prescription lasts 1 to {MAX_DURATION_DAYS} days")
        if self.reevaluate_after != self.day + timedelta(days=self.duration_days):
            raise DomainValidationError("a prescription is re-evaluated when its duration ends")

    @property
    def entity_id(self) -> UUID:
        return self.prescription_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)

    @property
    def blocks_intensity(self) -> bool:
        """REST and ACTIVE_RECOVERY activate RECOVERY_BLOCK until re-evaluation."""
        return self.type in BLOCKING

    def covers(self, day: date) -> bool:
        return self.day <= day < self.reevaluate_after


def prescribe(
    assessment: RecoveryAssessment, *, prescription_id: UUID, created_at: datetime
) -> RecoveryPrescription | None:
    """The prescription a major decision calls for; none otherwise, and none under a safety
    hold, whose own rules apply (safety spec 3)."""
    decided = PRESCRIBED.get(assessment.reasons[0])
    if decided is None or not assessment.major:
        return None
    kind, duration = decided
    deviating = tuple(
        ref
        for signal in assessment.signals
        if signal.state is SignalState.DEVIATING
        for ref in signal.evidence
    )
    evidence = (EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, assessment.assessment_id),)
    return RecoveryPrescription(
        prescription_id=prescription_id,
        athlete_id=assessment.athlete_id,
        assessment_id=assessment.assessment_id,
        day=assessment.day,
        type=kind,
        reason=assessment.reasons[0],
        evidence=evidence + tuple(dict.fromkeys(deviating)),
        duration_days=duration,
        reevaluate_after=assessment.day + timedelta(days=duration),
        created_at=created_at,
    )


def prescriptions_in_force(
    prescriptions: Iterable[RecoveryPrescription],
    assessments: Mapping[date, RecoveryAssessment],
    day: date,
) -> tuple[RecoveryPrescription, ...]:
    """The prescriptions that cover the day and whose assessment is still the one in force
    for its day (a re-assessment of that day withdraws them), most conservative first."""
    found = [
        item
        for item in prescriptions
        if item.covers(day)
        and (current := assessments.get(item.day)) is not None
        and current.assessment_id == item.assessment_id
    ]
    return tuple(sorted(found, key=lambda item: (SEVERITY.index(item.type), item.day)))
