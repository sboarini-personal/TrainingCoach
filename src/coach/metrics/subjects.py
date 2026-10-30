"""What a derived value describes (spec/functional/metrics.spec.md section 4).

A subject is a kind and a key (an activity, a day, an ISO week, an exercise) and, from P8, an
optional dimension: a sport for per-sport period totals, a zone (heart rate or power) for
time in zones, from P9 a swimming stroke type, or from P10 a muscle group. The dimension is
part of the subject's identity.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.activity.fields import StrokeType
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange
from coach.domain.strength.vocabulary import MuscleGroup

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_WEEK = re.compile(r"^(?P<year>[0-9]{4})-W(?P<week>0[1-9]|[1-4][0-9]|5[0-3])$")
_EXERCISE = re.compile(r"^ex\.[a-z0-9_]+$")
_ZONE = re.compile(r"^Z(?P<zone>[1-9]|10)$")
MAX_ZONES: Final = 10
# Stroke types a per-stroke swimming metric describes; MIXED and DRILL lengths are left out.
STROKES: Final = (
    StrokeType.FREESTYLE,
    StrokeType.BACKSTROKE,
    StrokeType.BREASTSTROKE,
    StrokeType.BUTTERFLY,
)


class SubjectKind(StrEnum):
    ACTIVITY = "ACTIVITY"
    DAY = "DAY"
    WEEK = "WEEK"
    EXERCISE = "EXERCISE"


class DimensionKind(StrEnum):
    SPORT = "SPORT"
    ZONE = "ZONE"  # heart-rate or power zones, by metric (HR_ZONE until P9)
    STROKE = "STROKE"  # a swimming stroke type (P9)
    MUSCLE = "MUSCLE"  # a muscle group (P10)


PERIOD_KINDS: Final = frozenset({SubjectKind.DAY, SubjectKind.WEEK})


def _valid_day(key: str) -> bool:
    try:
        return date.fromisoformat(key).isoformat() == key
    except ValueError:
        return False


def _valid_week(key: str) -> bool:
    match = _WEEK.match(key)
    if match is None:
        return False
    try:
        date.fromisocalendar(int(match["year"]), int(match["week"]), 1)
    except ValueError:  # week 53 of a year that has 52
        return False
    return True


_KEY_RULES: Final[dict[SubjectKind, Callable[[str], bool]]] = {
    SubjectKind.ACTIVITY: lambda key: bool(_UUID.match(key)),
    SubjectKind.DAY: _valid_day,
    SubjectKind.WEEK: _valid_week,
    SubjectKind.EXERCISE: lambda key: bool(_EXERCISE.match(key)),
}


def dimension_kind(code: str) -> DimensionKind:
    """The kind of a dimension code: a sport, a zone ``Z1`` to ``Z10``, a stroke type or a
    muscle group (the vocabularies share no code)."""
    if _ZONE.match(code):
        return DimensionKind.ZONE
    if code in Sport.__members__.values():
        return DimensionKind.SPORT
    if code in STROKES:
        return DimensionKind.STROKE
    if code in MuscleGroup.__members__.values():
        return DimensionKind.MUSCLE
    raise DomainValidationError(f"invalid subject dimension: {code!r}")


def zone_code(zone: int) -> str:
    if not 1 <= zone <= MAX_ZONES:
        raise DomainValidationError("zones are numbered 1 to 10")
    return f"Z{zone}"


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass(frozen=True, slots=True)
class MetricSubject:
    kind: SubjectKind
    key: str
    dimension: str | None = None

    def __post_init__(self) -> None:
        if not _KEY_RULES[self.kind](self.key):
            raise DomainValidationError(f"invalid {self.kind} subject key: {self.key!r}")
        if self.dimension is not None:
            dimension_kind(self.dimension)

    @classmethod
    def activity(
        cls, activity_id: UUID, *, zone: int | None = None, stroke: StrokeType | None = None
    ) -> MetricSubject:
        if zone is not None and stroke is not None:
            raise DomainValidationError("a subject has one dimension")
        dimension = None if zone is None else zone_code(zone)
        if stroke is not None:
            dimension = stroke.value
        return cls(SubjectKind.ACTIVITY, str(activity_id), dimension)

    @classmethod
    def day(cls, day: date, *, sport: Sport | None = None) -> MetricSubject:
        return cls(SubjectKind.DAY, day.isoformat(), None if sport is None else sport.value)

    @classmethod
    def week(
        cls, day: date, *, sport: Sport | None = None, muscle: MuscleGroup | None = None
    ) -> MetricSubject:
        if sport is not None and muscle is not None:
            raise DomainValidationError("a subject has one dimension")
        dimension = None if sport is None else sport.value
        if muscle is not None:
            dimension = muscle.value
        return cls(SubjectKind.WEEK, week_key(day), dimension)

    @property
    def dimension_kind(self) -> DimensionKind | None:
        return None if self.dimension is None else dimension_kind(self.dimension)

    @property
    def sport(self) -> Sport | None:
        if self.dimension is None or self.dimension_kind is not DimensionKind.SPORT:
            return None
        return Sport(self.dimension)

    @property
    def stroke(self) -> StrokeType | None:
        if self.dimension is None or self.dimension_kind is not DimensionKind.STROKE:
            return None
        return StrokeType(self.dimension)

    @property
    def muscle(self) -> MuscleGroup | None:
        if self.dimension is None or self.dimension_kind is not DimensionKind.MUSCLE:
            return None
        return MuscleGroup(self.dimension)

    @property
    def zone(self) -> int | None:
        match = None if self.dimension is None else _ZONE.match(self.dimension)
        return None if match is None else int(match["zone"])

    @property
    def storage_key(self) -> str:
        """The key and dimension together, as one current value exists per both."""
        return self.key if self.dimension is None else f"{self.key}/{self.dimension}"

    def period(self) -> DateRange:
        """The calendar dates of a DAY or WEEK subject (an ISO week runs Monday to Sunday)."""
        if self.kind is SubjectKind.DAY:
            day = date.fromisoformat(self.key)
            return DateRange(day, day)
        if self.kind is SubjectKind.WEEK:
            match = _WEEK.match(self.key)
            if match is None:  # pragma: no cover - checked when the subject was built
                raise DomainValidationError(f"invalid week: {self.key!r}")
            monday = date.fromisocalendar(int(match["year"]), int(match["week"]), 1)
            return DateRange(monday, monday + timedelta(days=6))
        raise DomainValidationError(f"{self.kind} subjects have no period")

    def with_dimension(self, dimension: str | None) -> MetricSubject:
        return MetricSubject(self.kind, self.key, dimension)
