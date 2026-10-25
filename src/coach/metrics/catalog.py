"""Shipped metric catalogue (spec/functional/metrics.spec.md sections 7 and 8)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

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
