"""Metric registry (FR-MET-002, FR-MET-003, FR-MET-007; spec/functional/metrics.spec.md).

Application metrics live in application namespaces and carry their calculation in code.
Provider metrics live in their provider's namespace, name the provider's own metric and have
no calculation in code, so an application value can never be presented as a provider value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.quality import Confidence
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_text

APPLICATION_NAMESPACES: Final = frozenset(
    {"any", "run", "cyc", "swim", "str", "ten", "rec", "plan", "goal"}
)
PROVIDER_ORIGINS: Final = frozenset({MetricOrigin.RAW_PROVIDER, MetricOrigin.PROVIDER_DERIVED})

_METRIC_ID = re.compile(r"^(?P<namespace>[a-z]+)\.[a-z0-9_]+$")
_INPUT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

type Calculation = Callable[[Mapping[str, float]], float]


@dataclass(frozen=True, slots=True)
class InputSpec:
    """A required input: name, unit and valid range (minimum exclusive when stated)."""

    name: str
    unit: str
    minimum: float
    maximum: float
    minimum_exclusive: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not _INPUT_NAME.match(self.name):
            raise DomainValidationError(f"invalid input name: {self.name!r}")
        require_finite(self.minimum, "input minimum")
        require_finite(self.maximum, "input maximum")
        if self.minimum > self.maximum:
            raise DomainValidationError(f"input {self.name}: minimum above maximum")

    def check(self, value: float) -> float:
        require_finite(value, self.name)
        below = value <= self.minimum if self.minimum_exclusive else value < self.minimum
        if below or value > self.maximum:
            raise DomainValidationError(f"input {self.name} out of range: {value}")
        return value


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricDefinition:
    metric_id: str
    name: str
    sport: Sport | None  # None: any sport
    unit: str
    method: str
    required_inputs: tuple[InputSpec, ...]
    minimum_data_quality: Confidence
    calculation_version: str
    origin: MetricOrigin
    reference: str | None = None
    provider: str | None = None
    provider_metric_name: str | None = None
    calculate: Calculation | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        match = _METRIC_ID.match(self.metric_id)
        if match is None:
            raise DomainValidationError(f"invalid metric id: {self.metric_id!r}")
        if not _SEMVER.match(self.calculation_version):
            raise DomainValidationError("calculation version must be MAJOR.MINOR.PATCH")
        clean_text(self.name, max_length=100, field="metric name")
        clean_text(self.unit, max_length=20, field="unit")
        clean_text(self.method, max_length=500, field="method")
        names = [spec.name for spec in self.required_inputs]
        if len(names) != len(set(names)):
            raise DomainValidationError("input names must be unique")
        if self.origin in PROVIDER_ORIGINS:
            self._check_provider_metric(match["namespace"])
        else:
            self._check_application_metric(match["namespace"])

    def _check_provider_metric(self, namespace: str) -> None:
        if not self.provider or not self.provider_metric_name:
            raise DomainValidationError("provider metrics name the provider and its metric")
        if self.provider != namespace or namespace in APPLICATION_NAMESPACES:
            raise DomainValidationError("a provider metric lives in its own provider namespace")
        if self.calculate is not None:
            raise DomainValidationError("provider metrics are not calculated by the application")

    def _check_application_metric(self, namespace: str) -> None:
        if namespace not in APPLICATION_NAMESPACES:
            raise DomainValidationError(
                f"application metrics use an application namespace: {namespace}"
            )
        if self.provider is not None or self.provider_metric_name is not None:
            raise DomainValidationError("application metrics are never labelled with a provider")
        if self.calculate is None:
            raise DomainValidationError("application metrics are calculated in code")
        if self.origin is MetricOrigin.STANDARD_CALCULATION and not self.reference:
            raise DomainValidationError("a standard calculation must cite its reference")

    @property
    def is_application_metric(self) -> bool:
        return self.origin not in PROVIDER_ORIGINS


class MetricRegistry:
    """Immutable set of metric definitions keyed by unique metric ID."""

    def __init__(self, definitions: Iterable[MetricDefinition]) -> None:
        by_id: dict[str, MetricDefinition] = {}
        for definition in definitions:
            if definition.metric_id in by_id:
                raise DomainValidationError(f"duplicate metric id: {definition.metric_id}")
            by_id[definition.metric_id] = definition
        self._by_id: Final = by_id

    def get(self, metric_id: str) -> MetricDefinition:
        return self._by_id[metric_id]

    def __contains__(self, metric_id: object) -> bool:
        return metric_id in self._by_id

    def __iter__(self) -> Iterator[MetricDefinition]:
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)
