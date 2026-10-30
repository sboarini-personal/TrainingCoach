"""Heart-rate zone models and sensors (spec/functional/profile.spec.md section 1)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import pairwise
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.text import clean_optional_text

MIN_ZONE_BPM = 30
MAX_ZONE_BPM = 230
MIN_ZONE_PERCENT = 30
MAX_ZONE_PERCENT = 100
# Ages an age-predicted maximum is made for: the profile's adult range (assumption A-17).
PREDICTION_AGES: Final = range(18, 101)


class ZoneMethod(StrEnum):
    PROVIDER = "PROVIDER"
    PERCENT_MAX_HR = "PERCENT_MAX_HR"
    HEART_RATE_RESERVE = "HEART_RATE_RESERVE"
    LACTATE_THRESHOLD_HR = "LACTATE_THRESHOLD_HR"
    CUSTOM = "CUSTOM"
    UNKNOWN = "UNKNOWN"


class ZoneSource(StrEnum):
    """Where zone anchors come from.

    AGE_PREDICTED is a labelled estimate the athlete chose (ADR-028): percentages of 220 minus
    age, for descriptive metrics only, never a reliable anchor for prescribing intensity.
    """

    ATHLETE_DECLARED = "ATHLETE_DECLARED"
    PROVIDER = "PROVIDER"
    CALCULATED_FROM_MEASURED_ANCHORS = "CALCULATED_FROM_MEASURED_ANCHORS"
    AGE_PREDICTED = "AGE_PREDICTED"


def age_on(birth_date: date, day: date) -> int:
    """Completed years on a day."""
    had_birthday = (day.month, day.day) >= (birth_date.month, birth_date.day)
    return day.year - birth_date.year - (0 if had_birthday else 1)


def age_predicted_max_hr(age: int) -> int:
    """220 minus age (Fox, Naughton and Haskell 1971): an estimate, see ADR-028."""
    return 220 - age


def _check_boundaries(values: Sequence[int], low: int, high: int, unit: str) -> None:
    if not 2 <= len(values) <= 11:
        raise DomainValidationError("a zone model needs 2 to 11 boundaries")
    if any(not low <= value <= high for value in values):
        raise DomainValidationError(f"zone boundaries must be {low}-{high} {unit}")
    if any(lower >= upper for lower, upper in pairwise(values)):
        raise DomainValidationError("zone boundaries must be strictly ascending")


@dataclass(frozen=True, slots=True, kw_only=True)
class HeartRateZoneModel:
    """Zone ``i`` spans ``[boundaries[i], boundaries[i + 1])`` in bpm.

    An AGE_PREDICTED model holds percentages of the age-predicted maximum instead of bpm;
    ``applied`` turns them into bpm for a training day (profile spec 1.1).
    """

    zone_model_id: UUID
    method: ZoneMethod
    source: ZoneSource
    valid_from: date
    boundaries_bpm: tuple[int, ...] = ()
    boundaries_percent_max_hr: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.source is not ZoneSource.AGE_PREDICTED:
            if self.boundaries_percent_max_hr:
                raise DomainValidationError("only an age-predicted model holds percentages")
            _check_boundaries(self.boundaries_bpm, MIN_ZONE_BPM, MAX_ZONE_BPM, "bpm")
            return
        if self.method is not ZoneMethod.PERCENT_MAX_HR or self.boundaries_bpm:
            raise DomainValidationError(
                "an age-predicted model holds percentages of maximum heart rate"
            )
        _check_boundaries(self.boundaries_percent_max_hr, MIN_ZONE_PERCENT, MAX_ZONE_PERCENT, "%")

    @property
    def zone_count(self) -> int:
        return len(self.boundaries_bpm or self.boundaries_percent_max_hr) - 1

    @property
    def estimated(self) -> bool:
        return self.source is ZoneSource.AGE_PREDICTED

    @property
    def reliable_anchor(self) -> bool:
        """Whether intensity may be prescribed by these zones (FR-PLN-005; never an estimate)."""
        return not self.estimated

    def applied(self, day: date, *, birth_date: date | None) -> AppliedZones | None:
        """The bpm boundaries on a training day.

        ``None`` for an age-predicted model without a birth date or for an age outside
        ``PREDICTION_AGES``. A boundary of *p* % is the smallest whole bpm at or above *p* %
        of the predicted maximum.
        """
        if not self.estimated:
            return AppliedZones(self, self.boundaries_bpm)
        if birth_date is None:
            return None
        age = age_on(birth_date, day)
        if age not in PREDICTION_AGES:
            return None
        maximum = age_predicted_max_hr(age)
        bpm = tuple(-(-percent * maximum // 100) for percent in self.boundaries_percent_max_hr)
        return AppliedZones(self, bpm, age=age)


@dataclass(frozen=True, slots=True)
class AppliedZones:
    """A zone model's bpm boundaries on one training day (metrics spec 9.2)."""

    model: HeartRateZoneModel
    boundaries_bpm: tuple[int, ...]
    age: int | None = None  # the age an AGE_PREDICTED maximum was predicted from

    @property
    def zone_count(self) -> int:
        return len(self.boundaries_bpm) - 1

    @property
    def max_hr(self) -> int | None:
        return None if self.age is None else age_predicted_max_hr(self.age)


@dataclass(frozen=True, slots=True)
class HeartRateZones:
    """The athlete's zone models and the birth date age-predicted ones need."""

    models: tuple[HeartRateZoneModel, ...] = ()
    birth_date: date | None = None

    def on(self, day: date) -> AppliedZones | None:
        """The model with the latest ``valid_from`` on or before the day, applied to it."""
        valid = [model for model in self.models if model.valid_from <= day]
        model = max(
            valid, key=lambda item: (item.valid_from, str(item.zone_model_id)), default=None
        )
        return None if model is None else model.applied(day, birth_date=self.birth_date)


class SensorType(StrEnum):
    HR_STRAP = "HR_STRAP"
    CYCLING_POWER_METER = "CYCLING_POWER_METER"
    CADENCE_SENSOR = "CADENCE_SENSOR"
    RUNNING_POD = "RUNNING_POD"
    SMART_TRAINER = "SMART_TRAINER"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class Sensor:
    sensor_type: SensorType
    model: str | None = None
    reliable_for_training: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "model", clean_optional_text(self.model, max_length=100, field="sensor model")
        )
