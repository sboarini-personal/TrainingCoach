"""Activity field catalogue (spec/integration/canonical-model.spec.md section 6).

Every leaf of the canonical Activity has a field path. The catalogue states each field's
value type and range, the resolution group that decides which source provides it, whether a
source may provide it and whether the athlete may correct it. Composite values (cadence,
power, heart-rate zones, swolf, provider metrics, pain) are one field each.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_optional_text, clean_text
from coach.domain.common.time import (
    TrainingDayBasis,
    ensure_utc,
    format_utc_offset,
    parse_timezone,
    parse_utc_offset,
)
from coach.domain.raw.source import require_sha256

_SUBTYPE = re.compile(r"^[A-Z_]+$")
_PROVIDER = re.compile(r"^[a-z0-9_]+$")
MAX_TEXT_LENGTH: Final = 4000


class HrSensor(StrEnum):
    CHEST_STRAP = "CHEST_STRAP"
    OPTICAL = "OPTICAL"
    OTHER = "OTHER"


class PowerSource(StrEnum):
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class CadenceUnit(StrEnum):
    STEPS_PER_MIN = "STEPS_PER_MIN"
    RPM = "RPM"
    STROKES_PER_MIN = "STROKES_PER_MIN"


class StrokeType(StrEnum):
    FREESTYLE = "FREESTYLE"
    BACKSTROKE = "BACKSTROKE"
    BREASTSTROKE = "BREASTSTROKE"
    BUTTERFLY = "BUTTERFLY"
    MIXED = "MIXED"
    DRILL = "DRILL"


class Terrain(StrEnum):
    ROAD = "ROAD"
    TRAIL = "TRAIL"
    TRACK = "TRACK"
    MIXED = "MIXED"
    INDOOR = "INDOOR"
    WATER = "WATER"
    COURT = "COURT"


class Surface(StrEnum):
    HARD = "HARD"
    CLAY = "CLAY"
    GRASS = "GRASS"
    CARPET = "CARPET"
    OTHER = "OTHER"


class StreamKind(StrEnum):
    TIME_SERIES = "TIME_SERIES"
    LAPS = "LAPS"
    LENGTHS = "LENGTHS"
    SETS = "SETS"


SUBTYPES: Final[Mapping[Sport, frozenset[str]]] = {
    Sport.RUNNING: frozenset({"ROAD", "TRAIL", "TRACK", "TREADMILL", "OTHER"}),
    Sport.CYCLING: frozenset({"ROAD", "MOUNTAIN", "GRAVEL", "INDOOR_TRAINER", "OTHER"}),
    Sport.SWIMMING: frozenset({"POOL", "OPEN_WATER"}),
    Sport.STRENGTH: frozenset({"GYM", "HOME", "OTHER"}),
    Sport.TENNIS: frozenset({"MATCH", "PRACTICE"}),
    Sport.OTHER: frozenset(),
}


def subtype_fits(sport: Sport, subtype: str | None) -> bool:
    return subtype is None or subtype in SUBTYPES[sport]


def check_subtype(sport: Sport, subtype: str | None) -> None:
    if not subtype_fits(sport, subtype):
        raise DomainValidationError(f"subtype {subtype} is not valid for {sport}")


def _optional_non_negative(value: float | None, name: str) -> None:
    if value is not None and require_finite(value, name) < 0:
        raise DomainValidationError(f"{name} must not be negative")


@dataclass(frozen=True, slots=True)
class Cadence:
    average: float | None
    max: float | None
    unit: CadenceUnit

    def __post_init__(self) -> None:
        _optional_non_negative(self.average, "average cadence")
        _optional_non_negative(self.max, "max cadence")


@dataclass(frozen=True, slots=True)
class PowerSummary:
    """Reported power; MEASURED needs a reliable power source (FR-CYC-002)."""

    source: PowerSource
    average_watts: float | None
    max_watts: float | None

    def __post_init__(self) -> None:
        _optional_non_negative(self.average_watts, "average power")
        _optional_non_negative(self.max_watts, "max power")


@dataclass(frozen=True, slots=True)
class HrZones:
    zone_model_id: UUID
    seconds_in_zone: tuple[float, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.seconds_in_zone) <= 10:
            raise DomainValidationError("heart-rate zones need between 1 and 10 zones")
        for seconds in self.seconds_in_zone:
            _optional_non_negative(seconds, "seconds in zone")


@dataclass(frozen=True, slots=True)
class Swolf:
    value: float
    origin: MetricOrigin

    def __post_init__(self) -> None:
        _optional_non_negative(self.value, "swolf")


@dataclass(frozen=True, slots=True)
class ProviderMetric:
    """A provider's own metric, never compared with another provider's (FR-MET-007)."""

    value: float | None
    provider: str
    metric_name: str
    origin: MetricOrigin

    def __post_init__(self) -> None:
        if self.value is not None:
            require_finite(self.value, "provider metric")
        if not _PROVIDER.match(self.provider):
            raise DomainValidationError(f"invalid provider: {self.provider!r}")
        object.__setattr__(
            self, "metric_name", clean_text(self.metric_name, max_length=100, field="metric name")
        )
        if self.origin not in {MetricOrigin.RAW_PROVIDER, MetricOrigin.PROVIDER_DERIVED}:
            raise DomainValidationError("provider metrics are RAW_PROVIDER or PROVIDER_DERIVED")


@dataclass(frozen=True, slots=True)
class Pain:
    flag: bool
    intensity: int | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        if self.intensity is not None and not 0 <= self.intensity <= 10:
            raise DomainValidationError("pain intensity must be between 0 and 10")
        object.__setattr__(
            self,
            "location",
            clean_optional_text(self.location, max_length=100, field="pain location"),
        )


@dataclass(frozen=True, slots=True)
class StreamRef:
    """A stored stream blob; streams are never inlined."""

    kind: StreamKind
    blob_sha256: str

    def __post_init__(self) -> None:
        require_sha256(self.blob_sha256)


class FieldKind(StrEnum):
    NUMBER = "NUMBER"
    INTEGER = "INTEGER"
    TEXT = "TEXT"
    ENUM = "ENUM"
    SUBTYPE = "SUBTYPE"
    INSTANT = "INSTANT"
    LOCAL_DATETIME = "LOCAL_DATETIME"
    DATE = "DATE"
    TIMEZONE = "TIMEZONE"
    UTC_OFFSET = "UTC_OFFSET"
    UUID = "UUID"
    CADENCE = "CADENCE"
    POWER = "POWER"
    HR_ZONES = "HR_ZONES"
    SWOLF = "SWOLF"
    PROVIDER_METRIC = "PROVIDER_METRIC"
    PAIN = "PAIN"


class Group(StrEnum):
    """Resolution group (spec/functional/activities.spec.md section 3)."""

    SPORT = "SPORT"
    SUBTYPE = "SUBTYPE"
    TIMING = "TIMING"
    TOTALS = "TOTALS"
    HEART_RATE = "HEART_RATE"
    SENSOR = "SENSOR"
    PROVIDER_METRIC = "PROVIDER_METRIC"
    METADATA = "METADATA"
    ENVIRONMENT = "ENVIRONMENT"
    DECLARED = "DECLARED"
    SUBJECTIVE = "SUBJECTIVE"
    COMPUTED = "COMPUTED"
    LINKED = "LINKED"


_NOT_FROM_SOURCES: Final = frozenset({Group.COMPUTED, Group.LINKED})


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldSpec:
    path: str
    kind: FieldKind
    group: Group
    nullable: bool = True
    minimum: float | None = None
    maximum: float | None = None
    minimum_exclusive: bool = False
    enum: type[StrEnum] | None = None
    correctable: bool = False

    @property
    def observable(self) -> bool:
        """Whether a source observation may provide the field."""
        return self.group not in _NOT_FROM_SOURCES

    def check(self, value: object) -> object:
        """Return the value in canonical form; raise DomainValidationError if invalid."""
        if value is None:
            if not self.nullable:
                raise DomainValidationError(f"{self.path} must not be UNKNOWN")
            return None
        return _CHECKS[self.kind](self, value)


def _range(spec: FieldSpec, value: float) -> None:
    require_finite(value, spec.path)
    if spec.minimum is not None:
        below = value <= spec.minimum if spec.minimum_exclusive else value < spec.minimum
        if below:
            raise DomainValidationError(f"{spec.path} below its minimum: {value}")
    if spec.maximum is not None and value > spec.maximum:
        raise DomainValidationError(f"{spec.path} above its maximum: {value}")


def _number(spec: FieldSpec, value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainValidationError(f"{spec.path} must be a number")
    _range(spec, value)
    return float(value)


def _integer(spec: FieldSpec, value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(f"{spec.path} must be an integer")
    _range(spec, value)
    return value


def _text(spec: FieldSpec, value: object) -> object:
    if not isinstance(value, str):
        raise DomainValidationError(f"{spec.path} must be text")
    cleaned = clean_optional_text(value, max_length=MAX_TEXT_LENGTH, field=spec.path)
    return spec.check(cleaned) if cleaned is None else cleaned


def _enum(spec: FieldSpec, value: object) -> object:
    if spec.enum is None or not isinstance(value, str):
        raise DomainValidationError(f"{spec.path} must be a code")
    try:
        return spec.enum(value)
    except ValueError as error:
        raise DomainValidationError(f"invalid {spec.path}: {value!r}") from error


def _subtype(spec: FieldSpec, value: object) -> object:
    if not isinstance(value, str) or not _SUBTYPE.match(value):
        raise DomainValidationError(f"invalid {spec.path}: {value!r}")
    return value


def _instant(spec: FieldSpec, value: object) -> object:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{spec.path} must be an instant")
    return ensure_utc(value)


def _local_datetime(spec: FieldSpec, value: object) -> object:
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise DomainValidationError(f"{spec.path} must be a local date-time without offset")
    return value


def _date(spec: FieldSpec, value: object) -> object:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise DomainValidationError(f"{spec.path} must be a date")
    return value


def _timezone(spec: FieldSpec, value: object) -> object:
    if not isinstance(value, str) or len(value) > 64:
        raise DomainValidationError(f"{spec.path} must be an IANA timezone name")
    parse_timezone(value)
    return value


def _utc_offset(spec: FieldSpec, value: object) -> object:
    if not isinstance(value, str):
        raise DomainValidationError(f"{spec.path} must be a UTC offset")
    return format_utc_offset(parse_utc_offset(value))


def _instance_of(kind: type) -> Callable[[FieldSpec, object], object]:
    def check(spec: FieldSpec, value: object) -> object:
        if not isinstance(value, kind):
            raise DomainValidationError(f"{spec.path} must be a {kind.__name__}")
        return value

    return check


_CHECKS: Final[Mapping[FieldKind, Callable[[FieldSpec, object], object]]] = {
    FieldKind.NUMBER: _number,
    FieldKind.INTEGER: _integer,
    FieldKind.TEXT: _text,
    FieldKind.ENUM: _enum,
    FieldKind.SUBTYPE: _subtype,
    FieldKind.INSTANT: _instant,
    FieldKind.LOCAL_DATETIME: _local_datetime,
    FieldKind.DATE: _date,
    FieldKind.TIMEZONE: _timezone,
    FieldKind.UTC_OFFSET: _utc_offset,
    FieldKind.UUID: _instance_of(UUID),
    FieldKind.CADENCE: _instance_of(Cadence),
    FieldKind.POWER: _instance_of(PowerSummary),
    FieldKind.HR_ZONES: _instance_of(HrZones),
    FieldKind.SWOLF: _instance_of(Swolf),
    FieldKind.PROVIDER_METRIC: _instance_of(ProviderMetric),
    FieldKind.PAIN: _instance_of(Pain),
}


def _num(
    path: str,
    group: Group,
    *,
    minimum: float | None = 0.0,
    maximum: float | None = None,
    minimum_exclusive: bool = False,
    correctable: bool = False,
) -> FieldSpec:
    return FieldSpec(
        path=path,
        kind=FieldKind.NUMBER,
        group=group,
        minimum=minimum,
        maximum=maximum,
        minimum_exclusive=minimum_exclusive,
        correctable=correctable,
    )


def _int(
    path: str, group: Group, *, minimum: float, maximum: float | None, correctable: bool = False
) -> FieldSpec:
    return FieldSpec(
        path=path,
        kind=FieldKind.INTEGER,
        group=group,
        minimum=minimum,
        maximum=maximum,
        correctable=correctable,
    )


def _code(path: str, group: Group, enum: type[StrEnum], *, correctable: bool = False) -> FieldSpec:
    return FieldSpec(
        path=path, kind=FieldKind.ENUM, group=group, enum=enum, correctable=correctable
    )


def _of(path: str, kind: FieldKind, group: Group, *, correctable: bool = False) -> FieldSpec:
    return FieldSpec(path=path, kind=kind, group=group, correctable=correctable)


_FIELDS: Final = (
    FieldSpec(
        path="classification.sport",
        kind=FieldKind.ENUM,
        group=Group.SPORT,
        enum=Sport,
        nullable=False,
        correctable=True,
    ),
    _of("classification.subtype", FieldKind.SUBTYPE, Group.SUBTYPE, correctable=True),
    _of("classification.providerType", FieldKind.TEXT, Group.METADATA),
    _of("metadata.name", FieldKind.TEXT, Group.METADATA, correctable=True),
    _of("metadata.description", FieldKind.TEXT, Group.METADATA, correctable=True),
    FieldSpec(
        path="timing.startTimeUtc", kind=FieldKind.INSTANT, group=Group.TIMING, nullable=False
    ),
    _of("timing.originalLocalTime", FieldKind.LOCAL_DATETIME, Group.COMPUTED),
    _of("timing.originalTimezone", FieldKind.TIMEZONE, Group.TIMING),
    _of("timing.utcOffset", FieldKind.UTC_OFFSET, Group.TIMING),
    FieldSpec(
        path="timing.trainingDay",
        kind=FieldKind.DATE,
        group=Group.COMPUTED,
        nullable=False,
        correctable=True,
    ),
    FieldSpec(
        path="timing.trainingDayBasis",
        kind=FieldKind.ENUM,
        group=Group.COMPUTED,
        enum=TrainingDayBasis,
        nullable=False,
    ),
    _num("timing.elapsedSeconds", Group.TOTALS, correctable=True),
    _num("timing.movingSeconds", Group.TOTALS, correctable=True),
    _num("distance.meters", Group.TOTALS, correctable=True),
    _num("elevation.gainMeters", Group.TOTALS, correctable=True),
    _num("elevation.lossMeters", Group.TOTALS, correctable=True),
    _int("cardiovascular.averageHr", Group.HEART_RATE, minimum=0, maximum=260, correctable=True),
    _int("cardiovascular.maxHr", Group.HEART_RATE, minimum=0, maximum=260, correctable=True),
    _code("cardiovascular.hrSensor", Group.HEART_RATE, HrSensor),
    _of("cardiovascular.hrZones", FieldKind.HR_ZONES, Group.COMPUTED),
    _num("performance.averageSpeedMps", Group.SENSOR, correctable=True),
    _num("performance.maxSpeedMps", Group.SENSOR, correctable=True),
    _num("performance.averagePaceSecondsPerKm", Group.COMPUTED),
    _num("performance.averagePaceSecondsPer100m", Group.COMPUTED),
    _of("performance.cadence", FieldKind.CADENCE, Group.SENSOR),
    FieldSpec(path="performance.power", kind=FieldKind.POWER, group=Group.SENSOR, nullable=False),
    _num("performance.runningDynamics.strideLengthMeters", Group.SENSOR),
    _num("performance.runningDynamics.verticalOscillationMm", Group.SENSOR),
    _num("performance.runningDynamics.groundContactTimeMs", Group.SENSOR),
    _int("swimming.lengthCount", Group.SENSOR, minimum=0, maximum=None, correctable=True),
    _code("swimming.strokeType", Group.SENSOR, StrokeType, correctable=True),
    _num("swimming.averageStrokeRate", Group.SENSOR),
    _of("swimming.swolf", FieldKind.SWOLF, Group.SENSOR),
    _of("strength.strengthSessionId", FieldKind.UUID, Group.LINKED),
    _of("training.aerobicEffect", FieldKind.PROVIDER_METRIC, Group.PROVIDER_METRIC),
    _of("training.anaerobicEffect", FieldKind.PROVIDER_METRIC, Group.PROVIDER_METRIC),
    _num("training.load.external.workKilojoules", Group.TOTALS),
    _num("training.load.external.volumeLoadKg", Group.TOTALS),
    _num("training.load.internal.sessionRpeLoad", Group.COMPUTED),
    _of("training.load.internal.providerLoad", FieldKind.PROVIDER_METRIC, Group.PROVIDER_METRIC),
    _num("subjective.rpe", Group.SUBJECTIVE, maximum=10, correctable=True),
    _int("subjective.soreness", Group.SUBJECTIVE, minimum=1, maximum=5, correctable=True),
    _of("subjective.pain", FieldKind.PAIN, Group.SUBJECTIVE),
    _of("subjective.comments", FieldKind.TEXT, Group.SUBJECTIVE),
    _num(
        "environment.temperatureC",
        Group.ENVIRONMENT,
        minimum=-60,
        maximum=60,
        correctable=True,
    ),
    _num("environment.humidityPct", Group.ENVIRONMENT, maximum=100, correctable=True),
    _num("environment.windMps", Group.ENVIRONMENT, correctable=True),
    _num(
        "environment.altitudeMeters",
        Group.ENVIRONMENT,
        minimum=-500,
        maximum=9000,
        correctable=True,
    ),
    _code("environment.terrain", Group.DECLARED, Terrain, correctable=True),
    _code("environment.surface", Group.DECLARED, Surface, correctable=True),
    _num(
        "environment.poolLengthMeters",
        Group.ENVIRONMENT,
        maximum=100,
        minimum_exclusive=True,
        correctable=True,
    ),
)

CATALOG: Final[Mapping[str, FieldSpec]] = {spec.path: spec for spec in _FIELDS}
CORRECTABLE_FIELDS: Final = frozenset(spec.path for spec in _FIELDS if spec.correctable)
OBSERVABLE_FIELDS: Final = frozenset(spec.path for spec in _FIELDS if spec.observable)
SUBJECTIVE_FIELDS: Final = frozenset(
    spec.path for spec in _FIELDS if spec.group is Group.SUBJECTIVE
)
REQUIRED_FIELDS: Final = frozenset(
    {
        "classification.sport",
        "classification.subtype",
        "timing.startTimeUtc",
        "timing.originalLocalTime",
        "timing.originalTimezone",
        "timing.utcOffset",
        "timing.trainingDay",
        "timing.trainingDayBasis",
        "timing.elapsedSeconds",
        "timing.movingSeconds",
        "distance.meters",
    }
)
STREAM_KEYS: Final[Mapping[StreamKind, str]] = {
    StreamKind.TIME_SERIES: "streams.timeSeries",
    StreamKind.LAPS: "streams.laps",
    StreamKind.LENGTHS: "streams.lengths",
    StreamKind.SETS: "streams.sets",
}


def field_spec(path: str) -> FieldSpec:
    spec = CATALOG.get(path)
    if spec is None:
        raise DomainValidationError(f"unknown activity field: {path!r}")
    return spec


def check_values(values: Mapping[str, object]) -> dict[str, object]:
    """Validated copy of ``values`` in catalogue order; unknown paths are rejected."""
    for path in values:
        field_spec(path)
    return {path: spec.check(values[path]) for path, spec in CATALOG.items() if path in values}


def check_streams(streams: tuple[StreamRef, ...]) -> None:
    kinds = [stream.kind for stream in streams]
    if len(kinds) != len(set(kinds)):
        raise DomainValidationError("at most one stream per kind")


def known_number(values: Mapping[str, object], path: str) -> float | None:
    """A known numeric field value; ``None`` when absent or UNKNOWN."""
    value = values.get(path)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainValidationError(f"{path} is not numeric")
    return float(value)


def sport_of(values: Mapping[str, object]) -> Sport:
    sport = values.get("classification.sport")
    if not isinstance(sport, Sport):
        raise DomainValidationError("classification.sport is required")
    return sport


def subtype_of(values: Mapping[str, object]) -> str | None:
    subtype = values.get("classification.subtype")
    return subtype if isinstance(subtype, str) else None


def instant_of(values: Mapping[str, object], path: str) -> datetime:
    value = values.get(path)
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{path} is required")
    return value


def date_of(values: Mapping[str, object], path: str) -> date:
    value = values.get(path)
    if isinstance(value, datetime) or not isinstance(value, date):
        raise DomainValidationError(f"{path} is required")
    return value
