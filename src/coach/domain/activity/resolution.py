"""Field-level resolution of an Activity from its sources' observations (FR-ACT-005).

``resolve`` is a pure function of the set of observations. Sources are ranked per field
group (spec/functional/activities.spec.md section 3) and ties are broken by raw SHA-256, so
the result never depends on the order in which sources arrived. One value is chosen per
field; totals are never summed (FR-ACT-008).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Final
from zoneinfo import ZoneInfo

from coach.domain.activity.activity import FieldProvenance
from coach.domain.activity.fields import (
    CATALOG,
    STREAM_KEYS,
    Group,
    StreamKind,
    StreamRef,
    instant_of,
    known_number,
    sport_of,
)
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.activity.timing import activity_time
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import Anomaly
from coach.domain.common.sport import Sport

RESOLUTION_RULES_VERSION: Final = "1.0.0"

_FIT, _PROVIDER = SourceKind.FIT_FILE, SourceKind.PROVIDER_RECORD
_OTHER, _ATHLETE = SourceKind.OTHER_FILE, SourceKind.ATHLETE_ENTRY
_MEASURED: Final = (_FIT, _PROVIDER, _OTHER, _ATHLETE)

ORDER: Final[Mapping[Group, tuple[SourceKind, ...]]] = {
    Group.SPORT: (_PROVIDER, _FIT, _OTHER, _ATHLETE),
    Group.SUBTYPE: (_PROVIDER, _FIT, _OTHER, _ATHLETE),
    Group.TIMING: _MEASURED,
    Group.TOTALS: _MEASURED,
    Group.HEART_RATE: _MEASURED,
    Group.SENSOR: _MEASURED,
    Group.PROVIDER_METRIC: (_PROVIDER, _FIT, _OTHER),
    Group.METADATA: (_PROVIDER, _ATHLETE, _FIT, _OTHER),
    Group.ENVIRONMENT: _MEASURED,
    Group.DECLARED: (_ATHLETE, _PROVIDER, _FIT, _OTHER),
    Group.SUBJECTIVE: (_ATHLETE,),
}
RULES: Final[Mapping[Group, str]] = {
    Group.SPORT: "SPORT_MOST_SPECIFIC",
    Group.SUBTYPE: "SUBTYPE_MATCHING_SPORT",
    Group.TIMING: "TIMING_PREFER_FIT",
    Group.TOTALS: "TOTALS_PREFER_FIT",
    Group.HEART_RATE: "HR_PREFER_FIT",
    Group.SENSOR: "SENSOR_PREFER_FIT",
    Group.PROVIDER_METRIC: "PROVIDER_METRIC_PREFER_RECORD",
    Group.METADATA: "METADATA_PREFER_PROVIDER",
    Group.ENVIRONMENT: "ENVIRONMENT_PREFER_FIT",
    Group.DECLARED: "DECLARED_PREFER_ATHLETE",
    Group.SUBJECTIVE: "SUBJECTIVE_ATHLETE_ONLY",
}
STREAM_ORDER: Final = (_FIT, _PROVIDER, _OTHER)
STREAMS_RULE: Final = "STREAMS_PREFER_FIT"
OFFSET_FROM_TIMEZONE_RULE: Final = "OFFSET_FROM_TIMEZONE"
LOCAL_TIME_RULE: Final = "LOCAL_TIME_FROM_OFFSET"
TRAINING_DAY_ACTIVITY_RULE: Final = "TRAINING_DAY_ACTIVITY_TIME"
TRAINING_DAY_ATHLETE_RULE: Final = "TRAINING_DAY_ATHLETE_TIMEZONE"
PACE_RULE: Final = "PACE_FROM_SPEED"

SPEED_FIELD: Final = "performance.averageSpeedMps"
PACE_FIELDS: Final[Mapping[Sport, tuple[str, float]]] = {
    Sport.RUNNING: ("performance.averagePaceSecondsPerKm", 1000.0),
    Sport.SWIMMING: ("performance.averagePaceSecondsPer100m", 100.0),
}

type Provenances = MutableMapping[str, FieldProvenance]
type _Take = Callable[[str, Observation, str], None]


@dataclass(frozen=True, slots=True)
class Resolution:
    values: Mapping[str, object]
    provenance: Mapping[str, FieldProvenance]
    streams: tuple[StreamRef, ...]
    anomalies: tuple[Anomaly, ...]


def current_versions(observations: Iterable[Observation]) -> list[Observation]:
    """Observations that take part in resolution: one payload per provider record (latest)."""
    latest: dict[tuple[str, str], Observation] = {}
    others: list[Observation] = []
    for observation in observations:
        record = observation.provider_record
        if record is None:
            others.append(observation)
            continue
        current = latest.get(record)
        newer = current is None or (observation.received_at, observation.raw_sha256) > (
            current.received_at,
            current.raw_sha256,
        )
        if newer:
            latest[record] = observation
    return [*others, *latest.values()]


def ranked(
    observations: Iterable[Observation], order: Sequence[SourceKind], path: str
) -> list[Observation]:
    """Observations providing ``path`` in the group's order, ties by raw SHA-256."""
    rank = {kind: index for index, kind in enumerate(order)}
    candidates = [item for item in observations if item.kind in rank and path in item.values]
    return sorted(candidates, key=lambda item: (rank[item.kind], item.raw_sha256, item.part))


def _first_known(candidates: Sequence[Observation], path: str) -> Observation | None:
    """First candidate with a known value; else the first (UNKNOWN); else ``None``."""
    for candidate in candidates:
        if candidate.values[path] is not None:
            return candidate
    return candidates[0] if candidates else None


def resolve(observations: Iterable[Observation], *, athlete_timezone: ZoneInfo) -> Resolution:
    sources = current_versions(observations)
    if not sources:
        raise DomainValidationError("resolution needs at least one observation")
    if len({source.athlete_id for source in sources}) != 1:
        raise DomainValidationError("observations of one activity belong to one athlete")
    raw_ids = [source.raw_source_id for source in sources]
    if len(raw_ids) != len(set(raw_ids)):
        raise DomainValidationError("a raw source links at most one part to an activity")

    values: dict[str, object] = {}
    provenance: dict[str, FieldProvenance] = {}

    def take(path: str, chosen: Observation, rule: str) -> None:
        values[path] = chosen.values[path]
        provenance[path] = FieldProvenance(
            chosen.raw_source_id, rule, chosen.source_paths.get(path)
        )

    _resolve_classification(sources, take)
    for path, spec in CATALOG.items():
        if spec.group in {Group.SPORT, Group.SUBTYPE} or spec.group not in ORDER:
            continue
        chosen = _first_known(ranked(sources, ORDER[spec.group], path), path)
        if chosen is not None:
            take(path, chosen, RULES[spec.group])
    values.setdefault("classification.subtype", None)
    for path in ("timing.elapsedSeconds", "timing.movingSeconds", "distance.meters"):
        values.setdefault(path, None)
    _resolve_time(values, provenance, athlete_timezone)
    derive_pace(values, provenance)
    streams = _resolve_streams(sources, provenance)
    anomalies = sorted(
        {anomaly for source in sources for anomaly in source.anomalies},
        key=lambda anomaly: (anomaly.code, anomaly.field, anomaly.detail),
    )
    return Resolution(values, provenance, streams, tuple(anomalies))


def _resolve_classification(sources: Sequence[Observation], take: _Take) -> None:
    by_order = ranked(sources, ORDER[Group.SPORT], "classification.sport")
    specific = [source for source in by_order if source.sport is not Sport.OTHER]
    chosen = (specific or by_order)[0]
    take("classification.sport", chosen, RULES[Group.SPORT])
    same_sport = [source for source in sources if source.sport is chosen.sport]
    subtype_source = _first_known(
        ranked(same_sport, ORDER[Group.SUBTYPE], "classification.subtype"),
        "classification.subtype",
    )
    if subtype_source is not None:
        take("classification.subtype", subtype_source, RULES[Group.SUBTYPE])


def _resolve_time(
    values: dict[str, object], provenance: Provenances, athlete_timezone: ZoneInfo
) -> None:
    offset = values.get("timing.utcOffset")
    timezone = values.get("timing.originalTimezone")
    time = activity_time(
        instant_of(values, "timing.startTimeUtc"),
        utc_offset=offset if isinstance(offset, str) else None,
        timezone=timezone if isinstance(timezone, str) else None,
        athlete_timezone=athlete_timezone,
    )
    values["timing.originalTimezone"] = time.original_timezone
    values["timing.utcOffset"] = time.utc_offset
    values["timing.originalLocalTime"] = time.original_local_time
    values["timing.trainingDay"] = time.training_day
    values["timing.trainingDayBasis"] = time.basis
    if time.offset_from_timezone:
        zone_source = provenance["timing.originalTimezone"].source_id
        provenance["timing.utcOffset"] = FieldProvenance(zone_source, OFFSET_FROM_TIMEZONE_RULE)
    if time.original_local_time is not None:
        offset_source = provenance["timing.utcOffset"].source_id
        provenance["timing.originalLocalTime"] = FieldProvenance(offset_source, LOCAL_TIME_RULE)
    start_source = provenance["timing.startTimeUtc"].source_id
    day_rule = (
        TRAINING_DAY_ACTIVITY_RULE
        if time.original_local_time is not None
        else TRAINING_DAY_ATHLETE_RULE
    )
    provenance["timing.trainingDay"] = FieldProvenance(start_source, day_rule)
    provenance["timing.trainingDayBasis"] = FieldProvenance(start_source, day_rule)


def derive_pace(values: dict[str, object], provenance: Provenances) -> None:
    """Recompute pace from speed for the current sport (1000 / speed, 100 / speed)."""
    for path, _ in PACE_FIELDS.values():
        values.pop(path, None)
        provenance.pop(path, None)
    pace = PACE_FIELDS.get(sport_of(values))
    if pace is None or SPEED_FIELD not in values:
        return
    path, meters = pace
    speed = known_number(values, SPEED_FIELD)
    values[path] = meters / speed if speed else None
    origin = provenance.get(SPEED_FIELD)
    if origin is not None:
        provenance[path] = FieldProvenance(
            origin.source_id, PACE_RULE, override_id=origin.override_id
        )


def _resolve_streams(
    sources: Sequence[Observation], provenance: Provenances
) -> tuple[StreamRef, ...]:
    rank = {kind: index for index, kind in enumerate(STREAM_ORDER)}
    chosen: list[StreamRef] = []
    for kind in StreamKind:
        offers = sorted(
            (
                (rank[source.kind], source.raw_sha256, source.part, source, stream)
                for source in sources
                if source.kind in rank
                for stream in source.streams
                if stream.kind is kind
            ),
            key=lambda offer: offer[:3],
        )
        if offers:
            source, stream = offers[0][3], offers[0][4]
            chosen.append(stream)
            provenance[STREAM_KEYS[kind]] = FieldProvenance(source.raw_source_id, STREAMS_RULE)
    return tuple(chosen)
