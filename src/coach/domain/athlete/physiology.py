"""Heart-rate zone models and sensors (spec/functional/profile.spec.md section 1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import pairwise
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.text import clean_optional_text

MIN_ZONE_BPM = 30
MAX_ZONE_BPM = 230


class ZoneMethod(StrEnum):
    PROVIDER = "PROVIDER"
    PERCENT_MAX_HR = "PERCENT_MAX_HR"
    HEART_RATE_RESERVE = "HEART_RATE_RESERVE"
    LACTATE_THRESHOLD_HR = "LACTATE_THRESHOLD_HR"
    CUSTOM = "CUSTOM"
    UNKNOWN = "UNKNOWN"


class ZoneSource(StrEnum):
    """Where zone anchors come from. Age-predicted anchors are deliberately not representable."""

    ATHLETE_DECLARED = "ATHLETE_DECLARED"
    PROVIDER = "PROVIDER"
    CALCULATED_FROM_MEASURED_ANCHORS = "CALCULATED_FROM_MEASURED_ANCHORS"


@dataclass(frozen=True, slots=True)
class HeartRateZoneModel:
    """Zone ``i`` spans ``[boundaries_bpm[i], boundaries_bpm[i + 1])``."""

    zone_model_id: UUID
    method: ZoneMethod
    source: ZoneSource
    boundaries_bpm: tuple[int, ...]
    valid_from: date

    def __post_init__(self) -> None:
        boundaries = self.boundaries_bpm
        if not 2 <= len(boundaries) <= 11:
            raise DomainValidationError("a zone model needs 2 to 11 boundaries")
        if any(not MIN_ZONE_BPM <= bpm <= MAX_ZONE_BPM for bpm in boundaries):
            raise DomainValidationError(
                f"zone boundaries must be {MIN_ZONE_BPM}-{MAX_ZONE_BPM} bpm"
            )
        if any(lower >= upper for lower, upper in pairwise(boundaries)):
            raise DomainValidationError("zone boundaries must be strictly ascending")

    @property
    def zone_count(self) -> int:
        return len(self.boundaries_bpm) - 1


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
