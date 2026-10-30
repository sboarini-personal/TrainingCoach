"""Per-activity metrics engine (FR-MET-001, FR-MET-004; metrics spec sections 8, 9.1, 9.2).

Recomputes, from the EFFECTIVE view of a live activity, every per-activity metric that
applies to its sport, and the time in heart-rate zones when a zone model applies (an
age-predicted model is applied to the training day and labelled as an estimate). It also
supplies the EFFECTIVE view with the fields filled from current values
(``training.load.internal.sessionRpeLoad``, ``cardiovascular.hrZones``). Values are stored
through ``DerivedMetricService``: unchanged snapshots store nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from coach.application.derived_metrics import DerivedMetricService
from coach.application.ownership import OwnerScope
from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import STREAM_KEYS, HrZones, StreamKind
from coach.domain.activity.streams import TimeSeries
from coach.domain.athlete.physiology import AppliedZones, HeartRateZones
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.metrics.catalog import ACTIVITY_BINDINGS, HR_ZONE_TIME, ActivityBinding
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, MetricInput, MetricSubject
from coach.metrics.subjects import MAX_ZONES
from coach.metrics.zones import MINIMUM_COVERAGE, time_in_zones

HR_ZONES_FIELD: Final = "cardiovascular.hrZones"
_SERIES_KEY: Final = STREAM_KEYS[StreamKind.TIME_SERIES]
_HEART_RATE_PREFIX: Final = "cardiovascular."

type ZoneSettings = Callable[[OwnerScope], HeartRateZones]
# None when the stored series cannot be read (missing or damaged blob): its zones are UNKNOWN.
type SeriesReader = Callable[[OwnerScope, str], TimeSeries | None]


@dataclass(frozen=True, slots=True)
class _SeriesZones:
    seconds: tuple[float, ...] | None
    evidence: EvidenceRef
    low_coverage: bool


def field_evidence(view: Activity, path: str) -> EvidenceRef:
    """Evidence of an activity field; a corrected field names its correction (lineage)."""
    origin = view.provenance.get(path)
    override = None if origin is None else origin.override_id
    note = None if override is None else f"override {override}"
    return EvidenceRef(EvidenceKind.ACTIVITY, view.activity_id, field=path, note=note)


class ActivityMetricsEngine:
    def __init__(
        self,
        metrics: DerivedMetricService,
        *,
        bindings: Sequence[ActivityBinding] = ACTIVITY_BINDINGS,
        zones: ZoneSettings | None = None,
        time_series: SeriesReader | None = None,
    ) -> None:
        self._metrics = metrics
        self._bindings = tuple(bindings)
        self._zone_settings = zones
        self._time_series = time_series

    # ------------------------------------------------------------------ writes

    def recompute(self, scope: OwnerScope, view: Activity) -> list[DerivedMetricValue]:
        """Per-activity metrics of a live activity's EFFECTIVE view.

        A metric that no longer applies (another sport, a zone the current model lacks, no
        model at all) has its current value retired as NOT_APPLICABLE (metrics spec 4).
        """
        stored = []
        subject = MetricSubject.activity(view.activity_id)
        for binding in self._bindings:
            if binding.applies_to(view.sport):
                stored.append(self._compute_binding(scope, binding, view))
            else:
                self._metrics.retire(scope, binding.definition.metric_id, subject)
        zones = self._applied(scope, view)
        if zones is not None:
            stored.extend(self._compute_zones(scope, view, zones))
        first_unused = 1 if zones is None else zones.zone_count + 1
        for zone in range(first_unused, MAX_ZONES + 1):
            self._metrics.retire(
                scope, HR_ZONE_TIME.metric_id, MetricSubject.activity(view.activity_id, zone=zone)
            )
        return stored

    def _compute_binding(
        self, scope: OwnerScope, binding: ActivityBinding, view: Activity
    ) -> DerivedMetricValue:
        anomalous = {anomaly.field for anomaly in view.quality.anomalies}
        specs = {spec.name: spec for spec in binding.definition.required_inputs}
        inputs = {}
        for name, paths in binding.inputs:
            reasons: set[UncertaintyReason] = set()
            chosen = next((path for path in paths if view.number(path) is not None), paths[0])
            value = view.number(chosen)
            if chosen != paths[0]:
                reasons.add(UncertaintyReason.ESTIMATED_VALUE)
            if chosen in anomalous:
                reasons.add(UncertaintyReason.SENSOR_QUALITY)
            if value is not None:
                try:
                    specs[name].check(value)
                except DomainValidationError:
                    value = None
                    reasons.add(UncertaintyReason.SENSOR_QUALITY)
            inputs[name] = MetricInput(
                value, field_evidence(view, chosen), view.quality.confidence, frozenset(reasons)
            )
        return self._metrics.compute(
            scope,
            binding.definition.metric_id,
            MetricSubject.activity(view.activity_id),
            inputs,
        )

    def _compute_zones(
        self, scope: OwnerScope, view: Activity, zones: AppliedZones
    ) -> list[DerivedMetricValue]:
        heart_rate_anomaly = any(
            anomaly.field.startswith(_HEART_RATE_PREFIX) for anomaly in view.quality.anomalies
        )
        base = {UncertaintyReason.SENSOR_QUALITY} if heart_rate_anomaly else set()
        series = self._zone_seconds(scope, view, zones)
        (seconds_spec,) = [
            spec for spec in HR_ZONE_TIME.required_inputs if spec.name == "seconds_in_zone"
        ]
        stored = []
        for index in range(zones.zone_count):
            reasons = set(base)
            seconds = None if series.seconds is None else series.seconds[index]
            if series.low_coverage:
                reasons.add(UncertaintyReason.MISSING_DATA)
            if seconds is not None and seconds > seconds_spec.maximum:
                seconds = None  # more than a day in one zone: not a credible series
                reasons.add(UncertaintyReason.SENSOR_QUALITY)
            inputs = {
                "seconds_in_zone": MetricInput(
                    seconds, series.evidence, view.quality.confidence, frozenset(reasons)
                ),
                "zone_lower_bpm": _boundary(zones, index),
                "zone_upper_bpm": _boundary(zones, index + 1),
            }
            stored.append(
                self._metrics.compute(
                    scope,
                    HR_ZONE_TIME.metric_id,
                    MetricSubject.activity(view.activity_id, zone=index + 1),
                    inputs,
                )
            )
        return stored

    def _zone_seconds(self, scope: OwnerScope, view: Activity, zones: AppliedZones) -> _SeriesZones:
        stream = next((item for item in view.streams if item.kind is StreamKind.TIME_SERIES), None)
        origin = view.provenance.get(_SERIES_KEY)
        if stream is None or origin is None or self._time_series is None:
            evidence = EvidenceRef(EvidenceKind.ACTIVITY, view.activity_id, field=_SERIES_KEY)
            return _SeriesZones(None, evidence, low_coverage=False)
        evidence = EvidenceRef(
            EvidenceKind.RAW_SOURCE,
            origin.source_id,
            field=_SERIES_KEY,
            note=f"sha256 {stream.blob_sha256}",
        )
        stored = self._stored_zones(scope, view, zones, evidence)
        if stored is not None:
            return stored  # the series and the model are unchanged: no need to read it again
        series = self._time_series(scope, stream.blob_sha256)
        if series is None or series.heart_rate_bpm is None:
            return _SeriesZones(None, evidence, low_coverage=False)
        times = time_in_zones(series.offset_seconds, series.heart_rate_bpm, zones.boundaries_bpm)
        coverage = times.coverage
        low = coverage is not None and coverage < MINIMUM_COVERAGE
        return _SeriesZones(times.seconds, evidence, low_coverage=low)

    def _stored_zones(
        self, scope: OwnerScope, view: Activity, zones: AppliedZones, evidence: EvidenceRef
    ) -> _SeriesZones | None:
        """The series-derived part of the current zone values, when they used this series."""
        seconds: list[float] = []
        low_coverage = False
        for zone in range(1, zones.zone_count + 1):
            subject = MetricSubject.activity(view.activity_id, zone=zone)
            current = self._metrics.current(scope, HR_ZONE_TIME.metric_id, subject)
            if current is None or not _from_model(current, zones, zone):
                return None
            record = next((item for item in current.inputs if item.name == "seconds_in_zone"), None)
            if record is None or record.evidence != evidence:
                return None
            seconds.append(record.value)
            if current.status is not DerivedStatus.MISSING_INPUT:
                low_coverage = UncertaintyReason.MISSING_DATA in current.uncertainty_reasons
        return _SeriesZones(tuple(seconds), evidence, low_coverage=low_coverage)

    # ------------------------------------------------------------------ reads

    def targets(self, scope: OwnerScope, activity: Activity) -> dict[str, object]:
        """Fields of the EFFECTIVE view filled from current values (absent when not applicable)."""
        filled: dict[str, object] = {}
        subject = MetricSubject.activity(activity.activity_id)
        for binding in self._bindings:
            if binding.target is None or not binding.applies_to(activity.sport):
                continue
            current = self._metrics.current(scope, binding.definition.metric_id, subject)
            filled[binding.target] = None if current is None else current.value
        zones = self._applied(scope, activity)
        if zones is not None:
            filled[HR_ZONES_FIELD] = self._zones(scope, activity, zones)
        return filled

    def _zones(self, scope: OwnerScope, activity: Activity, zones: AppliedZones) -> HrZones | None:
        seconds = []
        for zone in range(1, zones.zone_count + 1):
            current = self._metrics.current(
                scope,
                HR_ZONE_TIME.metric_id,
                MetricSubject.activity(activity.activity_id, zone=zone),
            )
            if current is None or current.status is not DerivedStatus.COMPUTED:
                return None
            if not _from_model(current, zones, zone) or current.value is None:
                return None
            seconds.append(current.value)
        return HrZones(zones.model.zone_model_id, tuple(seconds))

    def current(self, scope: OwnerScope, activity: Activity) -> list[DerivedMetricValue]:
        """Current values of the per-activity metrics that apply to the activity now."""
        subject = MetricSubject.activity(activity.activity_id)
        found = []
        for binding in self._bindings:
            if binding.applies_to(activity.sport):
                current = self._metrics.current(scope, binding.definition.metric_id, subject)
                if current is not None:
                    found.append(current)
        zones = self._applied(scope, activity)
        if zones is not None:
            for zone in range(1, zones.zone_count + 1):
                current = self._metrics.current(
                    scope,
                    HR_ZONE_TIME.metric_id,
                    MetricSubject.activity(activity.activity_id, zone=zone),
                )
                if current is not None and _from_model(current, zones, zone):
                    found.append(current)
        return found

    def _applied(self, scope: OwnerScope, activity: Activity) -> AppliedZones | None:
        """The zone model of the activity's training day, applied to it (profile spec 1.1)."""
        if self._zone_settings is None:
            return None
        return self._zone_settings(scope).on(activity.training_day)


def _boundary(zones: AppliedZones, index: int) -> MetricInput:
    """A zone boundary input; an age-predicted one is an estimate naming its age (ADR-028)."""
    model = zones.model
    bpm = float(zones.boundaries_bpm[index])
    if zones.age is None:
        evidence = EvidenceRef(
            EvidenceKind.HR_ZONE_MODEL, model.zone_model_id, field=f"boundariesBpm[{index}]"
        )
        return MetricInput(bpm, evidence, Confidence.HIGH)
    percent = model.boundaries_percent_max_hr[index]
    evidence = EvidenceRef(
        EvidenceKind.HR_ZONE_MODEL,
        model.zone_model_id,
        field=f"boundariesPercentMaxHr[{index}]",
        note=f"{percent}% of 220 - age {zones.age} = {zones.max_hr} bpm",
    )
    estimated = frozenset({UncertaintyReason.ESTIMATED_VALUE})
    return MetricInput(bpm, evidence, Confidence.MEDIUM, estimated)


def _from_model(value: DerivedMetricValue, zones: AppliedZones, zone: int) -> bool:
    """A zone value belongs to the model, and the zone, whose boundaries it used."""
    expected = {
        "zone_lower_bpm": float(zones.boundaries_bpm[zone - 1]),
        "zone_upper_bpm": float(zones.boundaries_bpm[zone]),
    }
    used = {
        record.name: record.value
        for record in value.inputs
        if record.evidence.kind is EvidenceKind.HR_ZONE_MODEL
        and record.evidence.ref_id == zones.model.zone_model_id
    }
    return used == expected
