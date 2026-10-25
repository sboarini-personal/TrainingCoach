"""Shipped metric catalogue (spec/functional/metrics.spec.md sections 7 and 8).

Activity bindings name the activity field each input of an activity metric is read from;
those metrics are recomputed from the EFFECTIVE view after every change to an activity
(spec/functional/activities.spec.md section 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from coach.domain.activity.fields import field_spec
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.quality import Confidence
from coach.metrics.registry import InputSpec, MetricDefinition, MetricRegistry

SECONDS_PER_MINUTE: Final = 60.0


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
            86_400,
            minimum_exclusive=True,
            description="Total session duration",
        ),
    ),
    minimum_data_quality=Confidence.MEDIUM,
    calculation_version="1.0.0",
    origin=MetricOrigin.STANDARD_CALCULATION,
    reference=(
        "Foster C, Florhaug JA, Franklin J, et al. A new approach to monitoring exercise "
        "training. J Strength Cond Res. 2001;15(1):109-115"
    ),
    calculate=_session_rpe_load,
)

CATALOG: Final = MetricRegistry((SRPE_LOAD,))


@dataclass(frozen=True, slots=True)
class ActivityBinding:
    """Metric input name -> activity field path, for metrics computed per activity.

    ``target`` is the activity field the EFFECTIVE view fills with the current value.
    """

    definition: MetricDefinition
    inputs: tuple[tuple[str, str], ...]
    target: str | None = None

    def __post_init__(self) -> None:
        names = {spec.name for spec in self.definition.required_inputs}
        if {name for name, _ in self.inputs} != names or len(self.inputs) != len(names):
            raise DomainValidationError("a binding maps every input of its metric once")
        for _, path in self.inputs:
            field_spec(path)
        if self.target is not None:
            field_spec(self.target)


ACTIVITY_BINDINGS: Final = (
    ActivityBinding(
        SRPE_LOAD,
        (("rpe", "subjective.rpe"), ("session_duration_seconds", "timing.elapsedSeconds")),
        target="training.load.internal.sessionRpeLoad",
    ),
)
