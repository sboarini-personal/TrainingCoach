"""``coach`` commands for metrics, load and heart-rate zones (P8; metrics spec section 9).

Numbers come from the metrics engine; this module only formats them. A total with unknown
contributions is shown as a lower bound with the count of unknown ones, never as the total.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Final, TextIO

from coach.application.analytics import PeriodReport, TotalReport
from coach.application.consent import ConsentRequiredError
from coach.application.errors import NotFoundError
from coach.application.ids import new_id
from coach.application.ownership import OwnerScope
from coach.cli.activities import CONSENT_HINT, TIMEZONE_HINT, CommandError
from coach.cli.runtime import Runtime, TimezoneRequiredError, athlete_timezone
from coach.domain.athlete.physiology import HeartRateZoneModel, ZoneMethod, ZoneSource
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.common.errors import DomainValidationError
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, MetricSubject, SubjectKind
from coach.metrics.subjects import week_key

ZONE_METHODS: Final = {
    method.value.lower().replace("_", "-"): method
    for method in ZoneMethod
    if method is not ZoneMethod.UNKNOWN
}
_LABELS: Final = {
    "any.duration_elapsed": "elapsed",
    "any.duration_moving": "moving",
    "any.distance_total": "distance",
    "any.elevation_gain_total": "elevation",
    "any.srpe_load_total": "sRPE",
}


# ------------------------------------------------------------------ formatting


def clock(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def minutes(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def amount(value: float, unit: str) -> str:
    """A value in its canonical unit, rounded only here (metrics spec section 6)."""
    match unit:
        case "s":
            return clock(value)
        case "m":
            return f"{value / 1000:.2f} km" if value >= 1000 else f"{value:.0f} m"
        case "m/s":
            return f"{value:.2f} m/s ({value * 3.6:.1f} km/h)"
        case "s/km":
            return f"{minutes(value)} /km"
        case "s/100m":
            return f"{minutes(value)} /100m"
        case "AU":
            return f"{value:.0f} AU"
        case _:
            return f"{value:g} {unit}"


def total_text(report: TotalReport) -> str:
    value = report.value
    if value.status is DerivedStatus.COMPUTED and value.value is not None:
        return amount(value.value, report.unit)
    if value.status is DerivedStatus.BELOW_MINIMUM_QUALITY:
        return "low data quality"
    known = len(value.inputs)
    if known == 0:
        return "unknown"
    return f">= {amount(report.known_total, report.unit)} ({report.unknown} unknown)"


def value_text(value: DerivedMetricValue) -> str:
    if value.status is DerivedStatus.COMPUTED and value.value is not None:
        return amount(value.value, value.unit)
    if value.status is DerivedStatus.MISSING_INPUT:
        return "unknown (missing " + ", ".join(value.missing_inputs) + ")"
    return "unknown (data quality below the minimum)"


def _totals(totals: Mapping[str, TotalReport]) -> str:
    return "  ".join(
        f"{_LABELS[metric_id]} {total_text(report)}"
        for metric_id, report in totals.items()
        if metric_id in _LABELS
    )


# ------------------------------------------------------------------ helpers


def _today(runtime: Runtime, scope: OwnerScope) -> date:
    try:
        zone = athlete_timezone(runtime.profiles, scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    return runtime.clock().astimezone(zone).date()


def _week(text: str | None, runtime: Runtime, scope: OwnerScope) -> str:
    if text is None:
        return week_key(_today(runtime, scope))
    try:
        return MetricSubject(SubjectKind.WEEK, text).key
    except DomainValidationError:
        raise CommandError(f"invalid ISO week {text!r}; for example 2026-W44") from None


def _print_week(report: PeriodReport, out: TextIO) -> None:
    period = report.period
    print(
        f"week {report.subject.key} ({period.start} to {period.end}): "
        f"{report.activities} activities",
        file=out,
    )
    for item in report.sports:
        print(
            f"  {item.sport.value:<9} {item.activities:>3}  {_totals(item.totals)}",
            file=out,
        )
    if report.activities:
        print(f"  {'all':<9} {report.activities:>3}  {_totals(report.cross_sport)}", file=out)


# ------------------------------------------------------------------ commands


def load_week(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    week = _week(args.week, runtime, scope)
    try:
        report = runtime.analytics.week(scope, week)
    except ConsentRequiredError:
        raise CommandError(CONSENT_HINT) from None
    _print_week(report, out)
    return 0


def load_weeks(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    if not 1 <= args.count <= 104:
        raise CommandError("count must be between 1 and 104")
    today = _today(runtime, scope)
    try:
        for back in range(args.count - 1, -1, -1):
            report = runtime.analytics.week(scope, week_key(today - timedelta(weeks=back)))
            distances = "  ".join(
                f"{item.sport.value} {total_text(item.totals['any.distance_total'])}"
                for item in report.sports
                if "any.distance_total" in item.totals
            )
            elapsed = report.cross_sport.get("any.duration_elapsed")
            time_text = "-" if elapsed is None else total_text(elapsed)
            print(
                f"{report.subject.key}  {report.activities:>3} activities  {time_text:>10}  "
                f"{distances}",
                file=out,
            )
    except ConsentRequiredError:
        raise CommandError(CONSENT_HINT) from None
    return 0


def recompute(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    try:
        count = runtime.analytics.recompute(scope)
    except ConsentRequiredError:
        raise CommandError(CONSENT_HINT) from None
    print(f"metrics recomputed for {count} activities", file=out)
    return 0


def show_activity(
    runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO
) -> int:
    try:  # metrics first: their read checks the consent before anything is read
        values = runtime.analytics.activity_metrics(scope, args.activity_id)
        view = runtime.activities.effective(scope, args.activity_id).activity
    except NotFoundError:
        raise CommandError("activity not found") from None
    except ConsentRequiredError:
        raise CommandError(CONSENT_HINT) from None
    subtype = view.values.get("classification.subtype") or "-"
    print(f"activity {view.activity_id}", file=out)
    print(
        f"  {view.training_day}  {view.sport.value} {subtype}  status {view.status.value}  "
        f"confidence {view.quality.confidence.value}  sources {len(view.sources)}",
        file=out,
    )
    for path, unit in (
        ("timing.elapsedSeconds", "s"),
        ("timing.movingSeconds", "s"),
        ("distance.meters", "m"),
        ("elevation.gainMeters", "m"),
        ("cardiovascular.averageHr", "bpm"),
    ):
        number = view.number(path)
        print(f"  {path:<26} {'unknown' if number is None else amount(number, unit)}", file=out)
    print("metrics:", file=out)
    for value in sorted(values, key=lambda item: (item.metric_id, item.subject.zone or 0)):
        zone = "" if value.subject.zone is None else f" Z{value.subject.zone}"
        name = f"{value.metric_id}{zone}"
        print(f"  {name:<26} {value_text(value)}", file=out)
    if not values:
        print("  none yet; run: coach metrics recompute", file=out)
    return 0


def _models_text(models: Iterable[HeartRateZoneModel]) -> list[str]:
    return [
        f"from {model.valid_from}  {model.method.value:<20} "
        + " ".join(str(bpm) for bpm in model.boundaries_bpm)
        for model in sorted(models, key=lambda item: item.valid_from)
    ]


def hr_zones(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    stored: AthleteProfile | None
    try:
        stored = runtime.profiles.get(scope, scope.athlete_id)
    except NotFoundError:
        stored = None
    profile = stored or AthleteProfile.new(scope.athlete_id)
    if not args.boundaries:
        lines = _models_text(profile.zone_models)
        for line in lines or ["no heart-rate zone models"]:
            print(line, file=out)
        return 0
    valid_from = args.valid_from or _today(runtime, scope)
    try:
        model = HeartRateZoneModel(
            zone_model_id=new_id(),
            method=ZONE_METHODS[args.method],
            source=ZoneSource.ATHLETE_DECLARED,
            boundaries_bpm=tuple(args.boundaries),
            valid_from=valid_from,
        )
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    kept = tuple(item for item in profile.zone_models if item.valid_from != valid_from)
    updated = profile.apply(ProfileUpdate(zone_models=(*kept, model)))
    if stored is None:
        runtime.profiles.add(scope, updated)
    else:
        runtime.profiles.update(scope, updated, expected_version=stored.version)
    replaced = len(kept) < len(profile.zone_models)
    print(
        f"zone model {'replaced' if replaced else 'added'}: {model.zone_count} zones "
        f"from {valid_from}",
        file=out,
    )
    return recompute(runtime, scope, out)
