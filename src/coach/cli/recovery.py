"""``coach checkin`` and ``coach safety`` commands (P11; recovery spec section 1, safety spec
sections 2 to 4)."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, timedelta
from typing import Final, TextIO

from coach.application.ownership import OwnerScope
from coach.application.recovery import CheckInAnswers, CheckInRefusedError
from coach.application.recovery_assessment import AssessmentResult
from coach.cli.activities import CONSENT_TYPES, CommandError
from coach.cli.runtime import Runtime, TimezoneRequiredError
from coach.domain.coaching.state import Actor, Condition
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.recovery.assessment import Reason, Signal, SignalState
from coach.domain.recovery.checkin import CheckInItem, DailyCheckIn, PainOnset, RedFlag
from coach.domain.recovery.daily import METRICS, RecoveryMetric, RecoveryObservation

TIMEZONE_HINT: Final = "set your timezone first: coach profile timezone <IANA name>"
SCALE_HELP: Final = {
    CheckInItem.SLEEP_QUALITY: "1 very poor, 3 normal, 5 excellent",
    CheckInItem.FATIGUE: "1 none, 3 moderate, 5 extreme",
    CheckInItem.SORENESS: "1 none, 3 moderate, 5 severe, limits movement",
    CheckInItem.STRESS: "1 none, 3 moderate, 5 extreme",
    CheckInItem.MOTIVATION: "1 none, 3 normal, 5 very high",
}
RED_FLAGS: Final = {flag.value.removeprefix("RF_").lower(): flag for flag in RedFlag}


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    checkin = commands.add_parser("checkin", help="daily check-in").add_subparsers(
        dest="action", required=True
    )
    adding = checkin.add_parser("add", help="record today's check-in (or --day)")
    adding.add_argument("--day", help="training day, YYYY-MM-DD; default today")
    for item, flag in (
        (CheckInItem.SLEEP_QUALITY, "--sleep"),
        (CheckInItem.FATIGUE, "--fatigue"),
        (CheckInItem.SORENESS, "--soreness"),
        (CheckInItem.STRESS, "--stress"),
        (CheckInItem.MOTIVATION, "--motivation"),
    ):
        adding.add_argument(flag, type=int, help=SCALE_HELP[item])
    adding.add_argument("--pain", type=int, help="pain intensity 0 to 10 (0: no pain)")
    adding.add_argument("--pain-location", help="where, in your words")
    adding.add_argument("--pain-onset", choices=["sudden", "gradual"])
    adding.add_argument(
        "--red-flag",
        action="append",
        choices=sorted(RED_FLAGS),
        default=[],
        help="a warning sign you have (repeatable): it pauses intensive training",
    )
    adding.add_argument("--notes", help="free text (never send secrets)")
    listing = checkin.add_parser("list", help="recent check-ins")
    listing.add_argument("--days", type=int, default=7)
    recovery = commands.add_parser("recovery", help="daily recovery data").add_subparsers(
        dest="action", required=True
    )
    data = recovery.add_parser("data", help="daily metrics from your devices, by consent")
    data.add_argument("--days", type=int, default=14)
    recovery.add_parser("status", help="assess today's recovery (training load, not medical)")
    safety = commands.add_parser("safety", help="safety hold").add_subparsers(
        dest="action", required=True
    )
    safety.add_parser("status", help="whether intensive training is paused")
    release = safety.add_parser(
        "release", help="lift the safety hold once a professional has cleared you"
    )
    release.add_argument(
        "--cleared-by-professional", action="store_true", help="a professional cleared you"
    )


def add(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    answers = CheckInAnswers(
        sleep_quality=args.sleep,
        fatigue=args.fatigue,
        soreness=args.soreness,
        stress=args.stress,
        motivation=args.motivation,
        pain_flag=None if args.pain is None else args.pain > 0,
        pain_intensity=args.pain if args.pain else None,
        pain_location=args.pain_location,
        pain_onset=None if args.pain_onset is None else PainOnset(args.pain_onset.upper()),
        red_flags=tuple(RED_FLAGS[name] for name in args.red_flag),
        notes=args.notes,
    )
    if args.day is not None:
        try:
            answers = replace(answers, training_day=date.fromisoformat(args.day))
        except ValueError:
            _flags, notice = runtime.recovery.safeguard(scope, answers)  # never lost (fail-safe)
            if notice is not None:
                print(notice, file=out)
            raise CommandError("the day is a date, YYYY-MM-DD") from None
    try:
        result = runtime.recovery.record_checkin(scope, answers)
    except CheckInRefusedError as refused:
        print(refused.safety_notice, file=out)
        if isinstance(refused.__cause__, TimezoneRequiredError):
            raise CommandError(TIMEZONE_HINT) from None
        raise CommandError(str(refused)) from None
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    except (DomainValidationError, ValueError) as error:
        raise CommandError(str(error)) from None
    if result.not_recorded is None:
        print(f"check-in recorded for {result.checkin.training_day}", file=out)
    else:
        print(
            f"warning signs recorded for {result.checkin.training_day}; the other answers were "
            f"not recorded ({result.not_recorded}): correct them and add the check-in again",
            file=out,
        )
    if result.secret_notice is not None:
        print(result.secret_notice, file=out)
    if result.safety_notice is not None:
        print(result.safety_notice, file=out)
    return 0 if result.not_recorded is None else 1


def _line(item: DailyCheckIn) -> str:
    values = "  ".join(
        f"{entry.value} {'-' if item.item(entry) is None else item.item(entry)}"
        for entry in CheckInItem
    )
    pain = "pain -" if item.pain_flag is None else "pain no"
    if item.pain is not None:
        pain = f"pain {item.pain.intensity}"
    flags = ", ".join(flag.value for flag in item.red_flags_raised())
    return f"{item.training_day}  {values}  {pain}" + (f"  RED FLAGS {flags}" if flags else "")


def list_checkins(
    runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO
) -> int:
    if not 1 <= args.days <= 366:
        raise CommandError("days must be 1 to 366")
    try:
        until = runtime.recovery.today(scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    found = runtime.recovery.checkins(scope, until=until, days=args.days)
    for item in found:
        print(_line(item), file=out)
    if not found:
        print("no check-ins in these days", file=out)
    return 0


def status(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    active = Condition.SAFETY_HOLD in runtime.conditions.current(scope).conditions
    print("safety hold: active" if active else "safety hold: none", file=out)
    return 0


def release(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    if not args.cleared_by_professional:
        raise CommandError(
            "release it only once a healthcare professional has cleared you to train: "
            "coach safety release --cleared-by-professional"
        )
    if Condition.SAFETY_HOLD not in runtime.conditions.current(scope).conditions:
        raise CommandError("there is no safety hold to release")
    try:
        runtime.conditions.clear(
            scope,
            Condition.SAFETY_HOLD,
            reason="CLEARED_BY_PROFESSIONAL",
            actor=Actor.ATHLETE,
        )
    except InvalidTransitionError as error:  # pragma: no cover - the athlete may release
        raise CommandError(str(error)) from None
    print("safety hold released; training resumes conservatively (safety spec 4)", file=out)
    return 0


def _value(observation: RecoveryObservation | None) -> str:
    if observation is None:
        return "-"
    if observation.metric is RecoveryMetric.SLEEP_DURATION:
        minutes = round(observation.value / 60)
        return f"{minutes // 60}:{minutes % 60:02d}"
    return f"{observation.value:g}"


def data(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    if not 1 <= args.days <= 366:
        raise CommandError("days must be 1 to 366")
    try:
        until = runtime.recovery.today(scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    found = runtime.recovery.daily(scope, until=until, days=args.days)
    print(
        f"{'day':<10}  {'resting HR':>10}  {'HRV ms':>6}  {'sleep':>5}  {'Body Battery +':>14}",
        file=out,
    )
    for day, metrics in found.items():
        print(
            f"{day}  {_value(metrics.get(RecoveryMetric.RESTING_HR)):>10}  "
            f"{_value(metrics.get(RecoveryMetric.HRV_NIGHTLY)):>6}  "
            f"{_value(metrics.get(RecoveryMetric.SLEEP_DURATION)):>5}  "
            f"{_value(metrics.get(RecoveryMetric.BODY_BATTERY_CHARGED)):>14}",
            file=out,
        )
    if not found:
        print("no recovery data in these days (coach garmin sync, with the consents)", file=out)
    return 0


_CONSENT_NAMES: Final = {value: name for name, value in CONSENT_TYPES.items()}
_EVALUATION: Final = "consider an evaluation by a healthcare professional"
OBSERVATIONS: Final = {
    Reason.SAFETY_HOLD: "safety hold active: intensive training is paused (coach safety status)",
    Reason.ISOLATED_SIGNAL: "one signal deviates: an observation, training does not change yet",
    Reason.WEARABLE_SUBJECTIVE_CONFLICT: (
        "your devices and your check-in disagree: the more conservative of the two counts"
    ),
    Reason.SEVERE_PAIN: f"severe pain: active recovery only; {_EVALUATION}",
    Reason.PAIN_REPORTED: "pain reported: it counts as a recovery signal",
    Reason.PAIN_PERSISTENT: f"pain reported for more than 7 days: {_EVALUATION}",
    Reason.PAIN_WORSENING: f"pain growing since it began: {_EVALUATION}",
}


def _amount(name: str, value: float) -> str:
    if name == RecoveryMetric.SLEEP_DURATION.value:
        minutes = round(value / 60)
        return f"{minutes // 60}:{minutes % 60:02d}"
    return f"{value:.1f}".removesuffix(".0")


def _signal_line(signal: Signal) -> str:
    detail = "-"
    if signal.state is SignalState.NO_CONSENT:
        consent = _CONSENT_NAMES[METRICS[RecoveryMetric(signal.name)].consent]
        detail = f"needs consent: coach consent grant {consent}"
    elif signal.value is not None:
        detail = _amount(signal.name, signal.value)
        if signal.baseline_mean is not None and signal.baseline_sd is not None:
            mean = _amount(signal.name, signal.baseline_mean)
            spread = _amount(signal.name, signal.baseline_sd)
            detail += f" (baseline {mean} +/- {spread}, {signal.baseline_readings} days)"
        if signal.consecutive_days > 1:
            detail += f", {signal.consecutive_days} days in a row"
    return f"  {signal.name:<21} {signal.state.value:<11} {detail}"


def assessment(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    try:
        result: AssessmentResult = runtime.recovery_assessment.assess(scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    found = result.assessment
    decisive = found.reasons[0].value
    print(
        f"recovery {found.day}: {found.status.value} ({decisive}), "
        f"confidence {found.confidence.value}",
        file=out,
    )
    for signal in found.signals:
        print(_signal_line(signal), file=out)
    for reason in found.reasons:
        if reason in OBSERVATIONS:
            print(f"observation: {OBSERVATIONS[reason]}", file=out)
    if found.uncertainty_reasons:
        reasons = ", ".join(sorted(item.value for item in found.uncertainty_reasons))
        print(f"uncertainty: {reasons}", file=out)
    for prescription in result.prescriptions:
        last = prescription.reevaluate_after - timedelta(days=1)
        print(
            f"prescription: {prescription.type.value} through {last} "
            f"({prescription.reason.value}), re-evaluated on {prescription.reevaluate_after}",
            file=out,
        )
    print(f"recovery block: {'active' if result.recovery_block else 'none'}", file=out)
    print("training-load guidance, not a medical assessment", file=out)
    return 0
