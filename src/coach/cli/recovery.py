"""``coach checkin`` and ``coach safety`` commands (P11; recovery spec section 1, safety spec
sections 2 to 4)."""

from __future__ import annotations

import argparse
from datetime import date
from typing import Final, TextIO

from coach.application.ownership import OwnerScope
from coach.application.recovery import CheckInAnswers
from coach.cli.activities import CommandError
from coach.cli.runtime import Runtime, TimezoneRequiredError
from coach.domain.coaching.state import Actor, Condition
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.recovery.checkin import CheckInItem, DailyCheckIn, PainOnset, RedFlag
from coach.domain.recovery.daily import RecoveryMetric, RecoveryObservation

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


def _today(runtime: Runtime, scope: OwnerScope) -> str:
    try:
        return runtime.recovery.today(scope).isoformat()
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None


def add(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    try:
        day = date.fromisoformat(args.day or _today(runtime, scope))
        answers = CheckInAnswers(
            training_day=day,
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
        result = runtime.recovery.record_checkin(scope, answers)
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
