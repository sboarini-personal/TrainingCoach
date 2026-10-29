"""Shipped metric catalogue (spec/functional/metrics.spec.md sections 7 to 9).

Activity bindings name the activity fields each input of a per-activity metric is read from,
in order of preference, and the sports the metric applies to; those metrics are recomputed
from the EFFECTIVE view after every change to an activity (activities spec section 4).
Period totals name the activity field (or the per-activity metric) they add up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from coach.domain.activity.fields import field_spec
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.quality import Confidence
from coach.domain.common.sport import Sport
from coach.metrics.registry import (
    Aggregation,
    Calculation,
    InputSpec,
    MetricDefinition,
    MetricRegistry,
)
from coach.metrics.subjects import DimensionKind, SubjectKind

SECONDS_PER_MINUTE: Final = 60.0
METERS_PER_KILOMETER: Final = 1000.0
MAX_SESSION_SECONDS: Final = 86_400.0
MAX_DISTANCE_METERS: Final = 1_000_000.0
FOSTER_2001: Final = (
    "Foster C, Florhaug JA, Franklin J, et al. A new approach to monitoring exercise "
    "training. J Strength Cond Res. 2001;15(1):109-115"
)
SI_BROCHURE: Final = (
    "BIPM. The International System of Units (SI), 9th edition, 2019: speed is distance "
    "travelled per unit time; pace is its reciprocal"
)
SEILER_2006: Final = (
    "Seiler KS, Kjerland GØ. Quantifying training intensity distribution in elite endurance "
    'athletes: is there evidence for an "optimal" distribution? Scand J Med Sci Sports. '
    "2006;16(1):49-56 (time-in-zone method)"
)
PERIODS: Final = frozenset({SubjectKind.DAY, SubjectKind.WEEK})
DISTANCE_SPORTS: Final = frozenset({Sport.RUNNING, Sport.CYCLING, Sport.SWIMMING})


def _session_rpe_load(inputs: Mapping[str, float]) -> float:
    return inputs["rpe"] * inputs["session_duration_seconds"] / SECONDS_PER_MINUTE


SRPE_LOAD: Final = MetricDefinition(
    metric_id="any.srpe_load",
    name="Session RPE load",
    sport=None,
    unit="AU",
    method="Session RPE (CR-10) multiplied by session duration in minutes",
    required_inputs=(
        InputSpec("rpe", "CR-10", 0, 10, description="RPE reported for the whole session"),
        InputSpec(
            "session_duration_seconds",
            "s",
            0,
            MAX_SESSION_SECONDS,
            minimum_exclusive=True,
            description="Total session duration",
        ),
    ),
    minimum_data_quality=Confidence.MEDIUM,
    calculation_version="1.0.0",
    origin=MetricOrigin.STANDARD_CALCULATION,
    reference=FOSTER_2001,
    calculate=_session_rpe_load,
)

_DISTANCE: Final = InputSpec(
    "distance_meters", "m", 0, MAX_DISTANCE_METERS, minimum_exclusive=True, description="Distance"
)
_DURATION: Final = InputSpec(
    "duration_seconds",
    "s",
    0,
    MAX_SESSION_SECONDS,
    minimum_exclusive=True,
    description="Moving time, else elapsed time",
)


def _speed(inputs: Mapping[str, float]) -> float:
    return inputs["distance_meters"] / inputs["duration_seconds"]


def _pace_per_km(inputs: Mapping[str, float]) -> float:
    return inputs["duration_seconds"] / (inputs["distance_meters"] / METERS_PER_KILOMETER)


def _pace_per_100m(inputs: Mapping[str, float]) -> float:
    return inputs["duration_seconds"] / (inputs["distance_meters"] / 100.0)


def _speed_metric(
    metric_id: str, name: str, sport: Sport | None, unit: str, method: str, calculate: Calculation
) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        name=name,
        sport=sport,
        unit=unit,
        method=method,
        required_inputs=(_DISTANCE, _DURATION),
        minimum_data_quality=Confidence.MEDIUM,
        calculation_version="1.0.0",
        origin=MetricOrigin.STANDARD_CALCULATION,
        reference=SI_BROCHURE,
        calculate=calculate,
    )


SPEED_AVG: Final = _speed_metric(
    "any.speed_avg", "Average speed", None, "m/s", "Distance divided by duration", _speed
)
RUN_PACE_AVG: Final = _speed_metric(
    "run.pace_avg",
    "Average running pace",
    Sport.RUNNING,
    "s/km",
    "Duration divided by distance in kilometres",
    _pace_per_km,
)
SWIM_PACE_100M_AVG: Final = _speed_metric(
    "swim.pace_100m_avg",
    "Average swimming pace per 100 m",
    Sport.SWIMMING,
    "s/100m",
    "Duration divided by distance in hundreds of metres",
    _pace_per_100m,
)


def _seconds_in_zone(inputs: Mapping[str, float]) -> float:
    return inputs["seconds_in_zone"]


HR_ZONE_TIME: Final = MetricDefinition(
    metric_id="any.hr_zone_time",
    name="Time in heart-rate zone",
    sport=None,
    unit="s",
    method=(
        "Intervals between consecutive heart-rate samples, at most 20 s long, summed by the "
        "zone of the heart rate at their start (metrics spec 9.2)"
    ),
    required_inputs=(
        InputSpec("seconds_in_zone", "s", 0, MAX_SESSION_SECONDS),
        InputSpec("zone_lower_bpm", "bpm", 30, 230),
        InputSpec("zone_upper_bpm", "bpm", 30, 230),
    ),
    minimum_data_quality=Confidence.MEDIUM,
    calculation_version="1.0.0",
    origin=MetricOrigin.STANDARD_CALCULATION,
    reference=SEILER_2006,
    calculate=_seconds_in_zone,
    dimension=DimensionKind.HR_ZONE,
)


def _total(
    metric_id: str,
    name: str,
    unit: str,
    spec: InputSpec,
    *,
    cross_sport: bool,
    origin: MetricOrigin = MetricOrigin.APPLICATION_DERIVED,
    reference: str | None = None,
) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        name=name,
        sport=None,
        unit=unit,
        method=(
            "Sum over the ACTIVE activities of the period (training day), within one sport "
            + ("or across sports" if cross_sport else "only")
        ),
        required_inputs=(spec,),
        minimum_data_quality=Confidence.LOW,
        calculation_version="1.0.0",
        origin=origin,
        reference=reference,
        aggregation=Aggregation.SUM,
        subject_kinds=PERIODS,
        dimension=DimensionKind.SPORT,
        cross_sport=cross_sport,
    )


DURATION_ELAPSED: Final = _total(
    "any.duration_elapsed",
    "Elapsed time",
    "s",
    InputSpec("elapsed_seconds", "s", 0, MAX_SESSION_SECONDS),
    cross_sport=True,
)
DURATION_MOVING: Final = _total(
    "any.duration_moving",
    "Moving time",
    "s",
    InputSpec("moving_seconds", "s", 0, MAX_SESSION_SECONDS),
    cross_sport=True,
)
DISTANCE_TOTAL: Final = _total(
    "any.distance_total",
    "Distance",
    "m",
    InputSpec("distance_meters", "m", 0, MAX_DISTANCE_METERS),
    cross_sport=False,
)
ELEVATION_GAIN_TOTAL: Final = _total(
    "any.elevation_gain_total",
    "Elevation gain",
    "m",
    InputSpec("elevation_gain_meters", "m", 0, 100_000),
    cross_sport=False,
)
SRPE_LOAD_TOTAL: Final = _total(
    "any.srpe_load_total",
    "Session RPE load",
    "AU",
    InputSpec("session_load", "AU", 0, 10 * MAX_SESSION_SECONDS / SECONDS_PER_MINUTE),
    cross_sport=True,
    origin=MetricOrigin.STANDARD_CALCULATION,
    reference=FOSTER_2001 + " (weekly load as the sum of session loads)",
)

CATALOG: Final = MetricRegistry(
    (
        SRPE_LOAD,
        SPEED_AVG,
        RUN_PACE_AVG,
        SWIM_PACE_100M_AVG,
        HR_ZONE_TIME,
        DURATION_ELAPSED,
        DURATION_MOVING,
        DISTANCE_TOTAL,
        ELEVATION_GAIN_TOTAL,
        SRPE_LOAD_TOTAL,
    )
)


@dataclass(frozen=True, slots=True)
class ActivityBinding:
    """Metric input name -> activity fields that may supply it (first known one wins).

    ``target`` is the activity field the EFFECTIVE view fills with the current value;
    ``sports`` limits the metric to activities of those sports (all sports when ``None``).
    """

    definition: MetricDefinition
    inputs: tuple[tuple[str, tuple[str, ...]], ...]
    target: str | None = None
    sports: frozenset[Sport] | None = None

    def __post_init__(self) -> None:
        names = {spec.name for spec in self.definition.required_inputs}
        if {name for name, _ in self.inputs} != names or len(self.inputs) != len(names):
            raise DomainValidationError("a binding maps every input of its metric once")
        for _, paths in self.inputs:
            if not paths:
                raise DomainValidationError("a binding names at least one field per input")
            for path in paths:
                field_spec(path)
        if self.target is not None:
            field_spec(self.target)
        if self.definition.sport is not None and self.sports not in (
            None,
            frozenset({self.definition.sport}),
        ):
            raise DomainValidationError("a sport metric applies to its own sport")

    def applies_to(self, sport: Sport) -> bool:
        if self.definition.sport is not None:
            return sport is self.definition.sport
        return self.sports is None or sport in self.sports


_SPEED_INPUTS: Final = (
    ("distance_meters", ("distance.meters",)),
    ("duration_seconds", ("timing.movingSeconds", "timing.elapsedSeconds")),
)
ACTIVITY_BINDINGS: Final = (
    ActivityBinding(
        SRPE_LOAD,
        (("rpe", ("subjective.rpe",)), ("session_duration_seconds", ("timing.elapsedSeconds",))),
        target="training.load.internal.sessionRpeLoad",
    ),
    ActivityBinding(SPEED_AVG, _SPEED_INPUTS, sports=DISTANCE_SPORTS),
    ActivityBinding(RUN_PACE_AVG, _SPEED_INPUTS),
    ActivityBinding(SWIM_PACE_100M_AVG, _SPEED_INPUTS),
)


@dataclass(frozen=True, slots=True)
class PeriodTotal:
    """A period total and what each activity contributes: a field, or a per-activity metric."""

    definition: MetricDefinition
    field: str | None = None
    activity_metric: MetricDefinition | None = None
    sports: frozenset[Sport] | None = None

    def __post_init__(self) -> None:
        if self.sports is not None and self.definition.cross_sport:
            raise DomainValidationError("a cross-sport total applies to every sport")
        if self.definition.aggregation is None:
            raise DomainValidationError("a period total is an aggregation")
        if (self.field is None) == (self.activity_metric is None):
            raise DomainValidationError("a total adds up a field or a per-activity metric")
        if self.field is not None:
            field_spec(self.field)

    def applies_to(self, sport: Sport) -> bool:
        return self.sports is None or sport in self.sports


PERIOD_TOTALS: Final = (
    PeriodTotal(DURATION_ELAPSED, field="timing.elapsedSeconds"),
    PeriodTotal(DURATION_MOVING, field="timing.movingSeconds"),
    PeriodTotal(DISTANCE_TOTAL, field="distance.meters", sports=DISTANCE_SPORTS),
    PeriodTotal(
        ELEVATION_GAIN_TOTAL,
        field="elevation.gainMeters",
        sports=frozenset({Sport.RUNNING, Sport.CYCLING}),
    ),
    PeriodTotal(SRPE_LOAD_TOTAL, activity_metric=SRPE_LOAD),
)
