"""``coach`` commands for metrics, load and heart-rate zones (P8; metrics spec section 9).

Numbers come from the metrics engine; this module only formats them. A total with unknown
contributions is shown as a lower bound with the count of unknown ones, never as the total,
and an estimated value (for example from age-predicted zones, ADR-028) says so.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, timedelta
from typing import Final, TextIO

from coach.application.analytics import PeriodReport, TotalReport
from coach.application.consent import ConsentRequiredError
from coach.application.errors import NotFoundError
from coach.application.ids import new_id
from coach.application.ownership import OwnerScope
from coach.cli.activities import CONSENT_HINT, TIMEZONE_HINT, CommandError
from coach.cli.runtime import Runtime, TimezoneRequiredError, athlete_timezone
from coach.domain.athlete.experience import CyclingExperience, FtpValue
from coach.domain.athlete.physiology import (
    PREDICTION_AGES,
    HeartRateZoneModel,
    Sensor,
    SensorType,
    ZoneMethod,
    ZoneSource,
    age_on,
)
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import FieldValue
from coach.domain.common.quality import UncertaintyReason
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
    "cyc.work_total": "work",
}
_LEFT_OUT: Final = {"cyc.work_total": "without measured power"}


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
        case "kJ":
            return f"{value:.0f} kJ"
        case "%":
            return f"{value:.1f} %"
        case "m/beat":
            return f"{value:.2f} m/beat"
        case "m/stroke":
            return f"{value:.2f} m/stroke"
        case "score":
            return f"{value:.1f}"
        case _:
            return f"{value:g} {unit}"


def total_text(report: TotalReport) -> str:
    text = _total_value_text(report)
    if report.left_out:
        text += f" ({report.left_out} {_LEFT_OUT.get(report.metric_id, 'left out')})"
    return text


def _total_value_text(report: TotalReport) -> str:
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
        estimated = UncertaintyReason.ESTIMATED_VALUE in value.uncertainty_reasons
        return amount(value.value, value.unit) + (" (estimated)" if estimated else "")
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
    for value in sorted(
        values,
        key=lambda item: (item.metric_id, item.subject.zone or 0, item.subject.dimension or ""),
    ):
        dimension = "" if value.subject.dimension is None else f" {value.subject.dimension}"
        name = f"{value.metric_id}{dimension}"
        print(f"  {name:<36} {value_text(value)}", file=out)
    if not values:
        print("  none yet; run: coach metrics recompute", file=out)
    return 0


def _model_text(model: HeartRateZoneModel) -> str:
    if model.estimated:
        values = " ".join(str(percent) for percent in model.boundaries_percent_max_hr)
        values += " % of 220 - age (estimate)"
    else:
        values = " ".join(str(bpm) for bpm in model.boundaries_bpm)
    return f"from {model.valid_from}  {model.method.value:<20} {values}"


def _stored_profile(runtime: Runtime, scope: OwnerScope) -> AthleteProfile | None:
    try:
        return runtime.profiles.get(scope, scope.athlete_id)
    except NotFoundError:
        return None


def _save(
    runtime: Runtime, scope: OwnerScope, stored: AthleteProfile | None, updated: AthleteProfile
) -> None:
    if stored is None:
        runtime.profiles.add(scope, updated)
    else:
        runtime.profiles.update(scope, updated, expected_version=stored.version)


def _zone_model(
    args: argparse.Namespace, profile: AthleteProfile, valid_from: date
) -> HeartRateZoneModel:
    if not args.age_predicted:
        return HeartRateZoneModel(
            zone_model_id=new_id(),
            method=ZONE_METHODS[args.method or "custom"],
            source=ZoneSource.ATHLETE_DECLARED,
            boundaries_bpm=tuple(args.boundaries),
            valid_from=valid_from,
        )
    if args.method not in {None, "percent-max-hr"}:
        raise CommandError("age-predicted zones use --method percent-max-hr")
    if profile.heart_rate_zones.birth_date is None:
        raise CommandError("set the birth date first: coach profile birth-date YYYY-MM-DD")
    return HeartRateZoneModel(
        zone_model_id=new_id(),
        method=ZoneMethod.PERCENT_MAX_HR,
        source=ZoneSource.AGE_PREDICTED,
        boundaries_percent_max_hr=tuple(args.boundaries),
        valid_from=valid_from,
    )


def hr_zones(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    stored = _stored_profile(runtime, scope)
    profile = stored or AthleteProfile.new(scope.athlete_id)
    if not args.boundaries:
        models = sorted(profile.zone_models, key=lambda item: item.valid_from)
        for line in [_model_text(model) for model in models] or ["no heart-rate zone models"]:
            print(line, file=out)
        return 0
    today = _today(runtime, scope) if args.age_predicted else None
    valid_from = args.valid_from or today or _today(runtime, scope)
    try:
        model = _zone_model(args, profile, valid_from)
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    kept = tuple(item for item in profile.zone_models if item.valid_from != valid_from)
    updated = profile.apply(ProfileUpdate(zone_models=(*kept, model)))
    _save(runtime, scope, stored, updated)
    replaced = len(kept) < len(profile.zone_models)
    estimate = ", an estimate (% of 220 - age)" if model.estimated else ""
    print(
        f"zone model {'replaced' if replaced else 'added'}: {model.zone_count} zones "
        f"from {valid_from}{estimate}",
        file=out,
    )
    applied = None
    if today is not None and model.estimated:
        applied = model.applied(today, birth_date=updated.heart_rate_zones.birth_date)
    if applied is not None:
        print(
            f"today: 220 - age {applied.age} = {applied.max_hr} bpm, boundaries "
            + " ".join(str(bpm) for bpm in applied.boundaries_bpm),
            file=out,
        )
    return recompute(runtime, scope, out)


def sensor(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    """Show, set or remove a declared sensor (profile spec 1; cycling spec 2)."""
    stored = _stored_profile(runtime, scope)
    profile = stored or AthleteProfile.new(scope.athlete_id)
    current = tuple(profile.sensors.value or ()) if profile.sensors is not None else ()
    if args.type is None:
        for item in current or ():
            reliable = "reliable" if item.reliable_for_training else "not reliable"
            model = "" if item.model is None else f"  {item.model}"
            print(f"{item.sensor_type.value:<20} {reliable}{model}", file=out)
        if not current:
            print("no sensors declared", file=out)
        return 0
    kind = SensorType(args.type)
    kept = tuple(item for item in current if item.sensor_type is not kind)
    if args.remove:
        sensors = kept
    else:
        try:
            added = Sensor(kind, args.model, reliable_for_training=args.reliable)
        except DomainValidationError as error:
            raise CommandError(str(error)) from None
        sensors = (*kept, added)
    now = runtime.clock()
    updated = profile.apply(ProfileUpdate(sensors=FieldValue.declared(sensors, at=now)))
    _save(runtime, scope, stored, updated)
    state = "removed" if args.remove else ("reliable" if args.reliable else "not reliable")
    print(f"sensor {kind.value} {state}", file=out)
    return recompute(runtime, scope, out)


def ftp(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    """Show or set the FTP (cycling spec 3, 6.1)."""
    stored = _stored_profile(runtime, scope)
    profile = stored or AthleteProfile.new(scope.athlete_id)
    cycling = profile.cycling_experience.value if profile.cycling_experience else None
    if args.watts is None:
        known = None if cycling is None else cycling.ftp
        if known is None:
            print("not set", file=out)
        else:
            how = "measured" if known.measured else "not measured"
            print(f"{known.watts} W on {known.on.isoformat()} ({how})", file=out)
        return 0
    on = args.on or _today(runtime, scope)
    try:
        value = FtpValue(args.watts, on, measured=args.measured)
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    experience = replace(cycling or CyclingExperience(), ftp=value)
    now = runtime.clock()
    update = ProfileUpdate(cycling_experience=FieldValue.declared(experience, at=now))
    _save(runtime, scope, stored, profile.apply(update))
    print(f"FTP set to {value.watts} W on {on.isoformat()}", file=out)
    return recompute(runtime, scope, out)


def birth_date(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    """Show or set the birth date; age-predicted zones follow it (profile spec 1.1)."""
    stored = _stored_profile(runtime, scope)
    profile = stored or AthleteProfile.new(scope.athlete_id)
    if args.date is None:
        known = profile.heart_rate_zones.birth_date
        print("not set" if known is None else known.isoformat(), file=out)
        return 0
    now = runtime.clock()
    if age_on(args.date, now.date()) not in PREDICTION_AGES:
        raise CommandError("age must be 18 to 100 (assumption A-17)")
    updated = profile.apply(ProfileUpdate(birth_date=FieldValue.declared(args.date, at=now)))
    _save(runtime, scope, stored, updated)
    print(f"birth date set to {args.date}", file=out)
    if any(model.estimated for model in updated.zone_models):
        return recompute(runtime, scope, out)
    return 0
