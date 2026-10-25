"""AthleteProfile aggregate (spec/functional/profile.spec.md).

Every attribute is a ``FieldValue`` recording who provided it and when. ``None`` at the
attribute level means never recorded; a ``FieldValue`` with ``value=None`` means recorded as
UNKNOWN. Changes go through the allow-listed ``ProfileUpdate`` (SEC-AUTHZ-003).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from datetime import date
from typing import Any, Final
from uuid import UUID

from coach.domain.athlete.experience import (
    CyclingExperience,
    RunningExperience,
    StrengthExperience,
    SwimmingExperience,
    TennisExperience,
)
from coach.domain.athlete.health import HealthDeclaration
from coach.domain.athlete.physiology import HeartRateZoneModel, Sensor, ZoneMethod
from coach.domain.athlete.resources import Resources
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import FieldValue
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_text
from coach.domain.common.time import parse_timezone

DEFAULT_LANGUAGE: Final = "es"
ADULT_AGE: Final = 18
_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileUpdate:
    """Athlete-editable fields. ``None`` leaves a field unchanged."""

    display_name: FieldValue[str] | None = None
    birth_date: FieldValue[date] | None = None
    declared_age: FieldValue[int] | None = None
    height_cm: FieldValue[float] | None = None
    weight_kg: FieldValue[float] | None = None
    resting_hr: FieldValue[int] | None = None
    max_hr: FieldValue[int] | None = None
    zone_method: FieldValue[ZoneMethod] | None = None
    zone_models: tuple[HeartRateZoneModel, ...] | None = None
    timezone: FieldValue[str] | None = None
    metric_units: FieldValue[bool] | None = None
    language: FieldValue[str] | None = None
    garmin_model: FieldValue[str] | None = None
    sensors: FieldValue[tuple[Sensor, ...]] | None = None
    running_experience: FieldValue[RunningExperience] | None = None
    cycling_experience: FieldValue[CyclingExperience] | None = None
    swimming_experience: FieldValue[SwimmingExperience] | None = None
    strength_experience: FieldValue[StrengthExperience] | None = None
    tennis_experience: FieldValue[TennisExperience] | None = None
    resources: FieldValue[Resources] | None = None
    health: FieldValue[HealthDeclaration] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AthleteProfile:
    athlete_id: UUID
    version: int = 1
    display_name: FieldValue[str] | None = None
    birth_date: FieldValue[date] | None = None
    declared_age: FieldValue[int] | None = None
    height_cm: FieldValue[float] | None = None
    weight_kg: FieldValue[float] | None = None
    resting_hr: FieldValue[int] | None = None
    max_hr: FieldValue[int] | None = None
    zone_method: FieldValue[ZoneMethod] | None = None
    zone_models: tuple[HeartRateZoneModel, ...] = ()
    timezone: FieldValue[str] | None = None
    metric_units: FieldValue[bool] | None = None
    language: FieldValue[str] | None = None
    garmin_model: FieldValue[str] | None = None
    sensors: FieldValue[tuple[Sensor, ...]] | None = None
    running_experience: FieldValue[RunningExperience] | None = None
    cycling_experience: FieldValue[CyclingExperience] | None = None
    swimming_experience: FieldValue[SwimmingExperience] | None = None
    strength_experience: FieldValue[StrengthExperience] | None = None
    tennis_experience: FieldValue[TennisExperience] | None = None
    resources: FieldValue[Resources] | None = None
    health: FieldValue[HealthDeclaration] | None = None

    def __post_init__(self) -> None:
        _check_range(self.height_cm, 100, 250, "height")
        _check_range(self.weight_kg, 30, 250, "weight")
        _check_range(self.resting_hr, 25, 120, "resting heart rate")
        _check_range(self.max_hr, 100, 230, "maximum heart rate")
        _check_range(self.declared_age, ADULT_AGE, 100, "age")
        resting, maximum = _known(self.resting_hr), _known(self.max_hr)
        if resting is not None and maximum is not None and resting >= maximum:
            raise DomainValidationError("resting heart rate must be below maximum heart rate")
        timezone = _known(self.timezone)
        if timezone is not None:
            parse_timezone(timezone)
        language = _known(self.language)
        if language is not None and not _LANGUAGE_TAG.match(language):
            raise DomainValidationError(f"invalid language tag: {language!r}")
        object.__setattr__(self, "display_name", _cleaned(self.display_name, 80, "display name"))
        object.__setattr__(self, "garmin_model", _cleaned(self.garmin_model, 100, "Garmin model"))

    @classmethod
    def new(cls, athlete_id: UUID) -> AthleteProfile:
        return cls(athlete_id=athlete_id)

    @property
    def entity_id(self) -> UUID:
        return self.athlete_id

    def with_version(self, version: int) -> AthleteProfile:
        return replace(self, version=version)

    def apply(self, update: ProfileUpdate) -> AthleteProfile:
        changes: dict[str, Any] = {
            item.name: getattr(update, item.name)
            for item in fields(update)
            if getattr(update, item.name) is not None
        }
        return replace(self, **changes)

    @property
    def effective_language(self) -> str:
        return _known(self.language) or DEFAULT_LANGUAGE

    def experience_for(self, sport: Sport) -> FieldValue[Any] | None:
        by_sport: dict[Sport, FieldValue[Any] | None] = {
            Sport.RUNNING: self.running_experience,
            Sport.CYCLING: self.cycling_experience,
            Sport.SWIMMING: self.swimming_experience,
            Sport.STRENGTH: self.strength_experience,
            Sport.TENNIS: self.tennis_experience,
        }
        return by_sport.get(sport)

    def is_adult(self, *, on: date) -> bool | None:
        """Adult status on a date (assumption A-17); ``None`` when age is unknown."""
        birth = _known(self.birth_date)
        if birth is not None:
            had_birthday = (on.month, on.day) >= (birth.month, birth.day)
            return on.year - birth.year - (0 if had_birthday else 1) >= ADULT_AGE
        age = _known(self.declared_age)
        return None if age is None else age >= ADULT_AGE


def _known[T](field_value: FieldValue[T] | None) -> T | None:
    return None if field_value is None else field_value.value


def _check_range(
    field_value: FieldValue[float] | FieldValue[int] | None, low: float, high: float, name: str
) -> None:
    if field_value is None or field_value.value is None:
        return
    if not low <= field_value.value <= high:
        raise DomainValidationError(f"{name} must be between {low} and {high}")


def _cleaned(
    field_value: FieldValue[str] | None, max_length: int, name: str
) -> FieldValue[str] | None:
    value = _known(field_value)
    if field_value is None or value is None:
        return field_value
    return FieldValue(clean_text(value, max_length=max_length, field=name), field_value.provenance)
