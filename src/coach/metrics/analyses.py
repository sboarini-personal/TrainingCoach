"""Stream analyses: per-activity metrics read from a session's streams (metrics spec 10).

An analysis turns the content of one stream (time series, laps or pool lengths) into the
named input values of its metric, or says that the session does not qualify (``None``). The
metrics engine adds evidence, confidence and the activity's reasons, and stores the value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from coach.domain.activity.fields import StreamKind
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.metrics.registry import MetricDefinition


@dataclass(frozen=True, slots=True)
class Analysis:
    """Input values of a metric from a stream (``None`` is UNKNOWN) and the reasons the
    stream adds to every input."""

    values: Mapping[str, float | None]
    reasons: frozenset[UncertaintyReason] = frozenset()


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamAnalysis:
    """How one metric is read from one stream kind.

    ``unknown_without_stream``: a session without the stream is UNKNOWN (MISSING_INPUT);
    otherwise it does not qualify. ``anomaly_fields``: activity field prefixes whose
    anomalies add SENSOR_QUALITY. ``stream_reasons``: the reasons ``analyse`` may add, kept
    when a stored value is reused for the same stream.
    """

    definition: MetricDefinition
    stream: StreamKind
    sports: frozenset[Sport]
    analyse: Callable[[Any], Analysis | None]
    unknown_without_stream: bool
    anomaly_fields: tuple[str, ...] = ()
    stream_reasons: frozenset[UncertaintyReason] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.definition.calculate is None:
            raise DomainValidationError("a stream analysis feeds a calculated metric")
        if self.definition.sport is not None and self.sports != frozenset({self.definition.sport}):
            raise DomainValidationError("a sport metric applies to its own sport")

    def applies_to(self, sport: Sport) -> bool:
        return sport in self.sports

    @property
    def input_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.definition.required_inputs)

    def unknown(self) -> Analysis:
        return Analysis(dict.fromkeys(self.input_names))
