"""``coach`` commands for what planning needs (P12; onboarding spec 2, profile spec 3 to 5,
goals spec 1 and 2): name, health constraints, resources, experience, availability, events,
goals and readiness. Values are the athlete's declarations; this module only parses and
formats them."""

from __future__ import annotations

import argparse
import re
from dataclasses import replace
from datetime import date, time, timedelta
from typing import Final, TextIO
from uuid import UUID

from coach.application.athlete_inputs import ConstraintAnswers
from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.cli.activities import TIMEZONE_HINT, CommandError
from coach.cli.runtime import Runtime, TimezoneRequiredError
from coach.domain.athlete.experience import (
    CyclingExperience,
    RaceResult,
    RunningExperience,
    SportExperience,
    StrengthExperience,
    SwimmingExperience,
    TennisExperience,
    TennisProfile,
)
from coach.domain.athlete.health import HealthConstraint, Restriction, RestrictionKind
from coach.domain.athlete.resources import (
    Equipment,
    PoolAccess,
    Resources,
    TennisSessionType,
    TennisSlot,
)
from coach.domain.availability.availability import (
    AvailabilityException,
    DayDefaults,
    TimeWindow,
    WeeklyAvailability,
)
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.common.provenance import ValueSource
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday
from coach.domain.goals.feasibility import FeasibilityAssessment
from coach.domain.goals.goal import GoalPriority, GoalStatus
from coach.domain.onboarding.readiness import LABELS

WEEKDAYS: Final = {day.name[:3].lower(): day for day in Weekday}
SPORTS: Final = {sport.value.lower(): sport for sport in Sport if sport is not Sport.OTHER}
_CLOCK: Final = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_WINDOW: Final = re.compile(r"^([a-z]{3}) (\d{1,2}:\d{2})-(\d{1,2}:\d{2})$")
_RACE: Final = re.compile(r"^(5k|10k|half|marathon)=([0-9:]+)@(\d{4}-\d{2}-\d{2})$")
_SLOT: Final = re.compile(r"^([a-z]{3}) (\d{1,2}:\d{2}) (\d+)(?: (match|practice))?$")
GOAL_CLOSE: Final = {
    "achieved": GoalStatus.ACHIEVED,
    "missed": GoalStatus.MISSED,
    "abandoned": GoalStatus.ABANDONED,
}


def _yes_no(text: str | None) -> bool | None:
    return None if text is None else text == "yes"


def duration(text: str) -> float:
    """Seconds from M:SS or H:MM:SS."""
    found = _CLOCK.match(text)
    if found is None:
        raise CommandError(f"invalid duration {text!r}; for example 48:30 or 1:45:00")
    hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


def _clock(text: str) -> time:
    try:
        return time.fromisoformat(text.zfill(5))
    except ValueError:
        raise CommandError(f"invalid time {text!r}; for example 18:30") from None


def _weekday(text: str) -> Weekday:
    if text not in WEEKDAYS:
        raise CommandError(f"invalid weekday {text!r}; one of {', '.join(WEEKDAYS)}")
    return WEEKDAYS[text]


# ------------------------------------------------------------------ parsers


def add_profile_parsers(profile: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    name = profile.add_parser("name", help="show or set your display name")
    name.add_argument("text", nargs="?")
    health = profile.add_parser("health", help="declared health constraints (never a diagnosis)")
    health_actions = health.add_subparsers(dest="health_action", required=True)
    health_actions.add_parser("show", help="the current declaration")
    health_actions.add_parser("none", help="declare that there are no constraints")
    health_actions.add_parser("confirm", help="re-confirm the declaration (every 30 days)")
    add = health_actions.add_parser("add", help="add a constraint")
    add.add_argument("--description", required=True, help="in your words (never send secrets)")
    add.add_argument("--region", action="append", default=[], help="a body region name")
    add.add_argument("--sport", action="append", default=[], choices=sorted(SPORTS))
    add.add_argument("--no-impact", action="store_true")
    add.add_argument("--no-overhead", action="store_true")
    add.add_argument("--no-heavy-lower-body", action="store_true")
    add.add_argument("--max-rpe", type=int)
    add.add_argument("--no-sport", action="append", default=[], choices=sorted(SPORTS))
    add.add_argument("--until", type=date.fromisoformat, help="YYYY-MM-DD")
    add.add_argument("--professional", action="store_true", help="a professional advised it")
    remove = health_actions.add_parser("remove", help="remove a constraint")
    remove.add_argument("constraint_id")
    resources = profile.add_parser("resources", help="show or set equipment and resources")
    resources.add_argument("--gym", choices=["yes", "no"])
    resources.add_argument(
        "--equipment",
        help="what you can use, at home or at a gym; comma-separated: "
        + ",".join(item.value for item in Equipment),
    )
    resources.add_argument("--pool", help="pool length in metres, or none")
    resources.add_argument("--bicycle", choices=["yes", "no"])
    resources.add_argument("--smart-trainer", choices=["yes", "no"])
    resources.add_argument(
        "--tennis", action="append", default=[], help='a weekly slot: "tue 19:00 90 match"'
    )
    resources.add_argument("--no-tennis", action="store_true", help="no tennis schedule")
    experience = profile.add_parser("experience", help="show or set your experience in a sport")
    experience.add_argument("sport", choices=sorted(SPORTS))
    experience.add_argument("--unknown", action="store_true", help="answer: I do not know")
    experience.add_argument("--years", type=float)
    experience.add_argument("--weekly-km", type=float)
    experience.add_argument("--longest-km", type=float)
    experience.add_argument("--usual-km", type=float, help="cycling: usual ride")
    experience.add_argument(
        "--race",
        action="append",
        default=[],
        help="running: 10k=48:30@2026-09-10 (5k, 10k, half, marathon)",
    )
    experience.add_argument("--power-meter", choices=["yes", "no"], help="cycling")
    experience.add_argument("--weekly-m", type=float, help="swimming")
    experience.add_argument("--longest-m", type=float, help="swimming")
    experience.add_argument("--pace-100m", help="swimming: typical pace, for example 1:55")
    experience.add_argument("--pool-length", type=float, help="swimming")
    experience.add_argument("--sessions-per-week", type=float, help="strength, tennis")
    experience.add_argument("--exercise", action="append", default=[], help="strength: ex.<id>")
    experience.add_argument("--rpe-familiar", choices=["yes", "no"], help="strength")
    experience.add_argument("--rir-familiar", choices=["yes", "no"], help="strength")
    experience.add_argument("--minutes", type=int, help="tennis: typical duration")
    experience.add_argument("--rpe", type=float, help="tennis: typical RPE")
    experience.add_argument("--profile", choices=["match", "practice", "mixed"], help="tennis")


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    availability = commands.add_parser("availability", help="when you can train").add_subparsers(
        dest="action", required=True
    )
    show = availability.add_parser("show", help="effective availability of the next days")
    show.add_argument("--days", type=int, default=14)
    weekly = availability.add_parser("set", help="your weekly availability")
    weekly.add_argument("--days", type=int, required=True, help="days per week, 0 to 7")
    weekly.add_argument("--max-hours", type=float, required=True, help="hours per week")
    weekly.add_argument("--rest-day", action="append", default=[], help="mon ... sun")
    weekly.add_argument(
        "--window", action="append", default=[], help='a time window: "tue 18:00-20:00"'
    )
    weekly.add_argument("--double-sessions", action="store_true")
    weekly.add_argument("--restrictions", help="free text (never send secrets)")
    defaults = availability.add_parser("default", help="a default day without a window")
    defaults.add_argument("--minutes", type=int, required=True)
    defaults.add_argument("--sport", action="append", default=[], choices=sorted(SPORTS))
    exception = availability.add_parser("exception", help="one day's exception")
    exception.add_argument("day", type=date.fromisoformat)
    state = exception.add_mutually_exclusive_group(required=True)
    state.add_argument("--unavailable", action="store_true")
    state.add_argument("--minutes", type=int)
    exception.add_argument("--sport", action="append", default=[], choices=sorted(SPORTS))
    exception.add_argument("--reason", help="free text (never send secrets)")
    clear = availability.add_parser("clear", help="remove one day's exception")
    clear.add_argument("day", type=date.fromisoformat)
    event = commands.add_parser("event", help="sport events").add_subparsers(
        dest="action", required=True
    )
    event_add = event.add_parser("add", help="add an event")
    event_add.add_argument("--name", required=True)
    event_add.add_argument("--sport", required=True, choices=sorted(SPORTS))
    event_add.add_argument("--date", required=True, type=date.fromisoformat)
    event_add.add_argument("--priority", default="A", choices=["A", "B", "C"])
    event_add.add_argument("--distance-km", type=float)
    event_add.add_argument("--location")
    event_add.add_argument("--conditions")
    event.add_parser("list", help="your events")
    goal = commands.add_parser("goal", help="goals").add_subparsers(dest="action", required=True)
    goal_add = goal.add_parser("add", help="add a goal (DRAFT until activated)")
    goal_add.add_argument("--sport", required=True, choices=sorted(SPORTS))
    goal_add.add_argument("--date", required=True, type=date.fromisoformat, help="target date")
    goal_add.add_argument("--priority", default="A", choices=["A", "B", "C"])
    goal_add.add_argument("--distance-km", type=float)
    goal_add.add_argument("--target-time", help="for example 45:00 or 1:45:00")
    goal_add.add_argument("--event", help="the event ID")
    goal.add_parser("list", help="your goals")
    activate = goal.add_parser("activate", help="activate a goal")
    activate.add_argument("goal_id")
    feasible = goal.add_parser(
        "feasibility", help="whether the goal looks feasible (never a guarantee)"
    )
    feasible.add_argument("goal_id", nargs="?", help="default: the primary goal")
    close = goal.add_parser("close", help="close a goal")
    close.add_argument("goal_id")
    close.add_argument("--as", dest="outcome", required=True, choices=sorted(GOAL_CLOSE))
    commands.add_parser("readiness", help="whether a plan can be generated, and what is missing")


# ------------------------------------------------------------------ profile


def name(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    if args.text is None:
        known = runtime.inputs.profile(scope).display_name
        print("not set" if known is None or known.value is None else known.value, file=out)
        return 0
    try:
        _profile, notice = runtime.inputs.set_display_name(scope, args.text)
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    if notice is not None:
        print(notice, file=out)
    print("name set", file=out)
    return 0


def _constraint_line(item: HealthConstraint) -> str:
    rules = ", ".join(
        f"{rule.kind.value}"
        + (f" {rule.max_rpe}" if rule.max_rpe is not None else "")
        + (f" {rule.sport.value}" if rule.sport is not None else "")
        for rule in item.restrictions
    )
    regions = ", ".join(item.affected_regions) or "-"
    until = "" if item.valid_until is None else f" until {item.valid_until}"
    return f"{item.constraint_id}  {item.description}  regions {regions}  {rules or '-'}{until}"


def health(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    inputs = runtime.inputs
    notice = None
    try:
        match args.health_action:
            case "show":
                declared = inputs.profile(scope).health
                if declared is None:
                    print("not declared", file=out)
                    return 0
                when = declared.provenance.recorded_at.date()
                found = inputs.constraints(scope)
                print(f"declared on {when}: {len(found) or 'no'} constraints", file=out)
                for item in found:
                    print(f"  {_constraint_line(item)}", file=out)
                return 0
            case "none":
                inputs.declare_health(scope, [])
            case "confirm":
                inputs.confirm_health(scope)
            case "remove":
                inputs.remove_constraint(scope, UUID(args.constraint_id))
            case _:
                restrictions = [
                    Restriction(kind)
                    for kind, flag in (
                        (RestrictionKind.NO_IMPACT, args.no_impact),
                        (RestrictionKind.NO_OVERHEAD, args.no_overhead),
                        (RestrictionKind.NO_HEAVY_LOWER_BODY, args.no_heavy_lower_body),
                    )
                    if flag
                ]
                if args.max_rpe is not None:
                    restrictions.append(Restriction(RestrictionKind.MAX_RPE, max_rpe=args.max_rpe))
                restrictions += [
                    Restriction(RestrictionKind.NO_SPORT, sport=SPORTS[item])
                    for item in args.no_sport
                ]
                answers = ConstraintAnswers(
                    description=args.description,
                    regions=tuple(args.region),
                    sports=frozenset(SPORTS[item] for item in args.sport),
                    restrictions=tuple(restrictions),
                    valid_until=args.until,
                    professional_advice=args.professional,
                )
                _profile, notice = inputs.add_constraint(scope, answers)
    except (DomainValidationError, ValueError) as error:
        raise CommandError(str(error)) from None
    except NotFoundError:
        raise CommandError("no such constraint") from None
    if notice is not None:
        print(notice, file=out)
    print(f"health declaration recorded: {len(inputs.constraints(scope))} constraints", file=out)
    return 0


def _slot(text: str) -> TennisSlot:
    found = _SLOT.match(text.strip().lower())
    if found is None:
        raise CommandError(f'invalid tennis slot {text!r}; for example "tue 19:00 90 match"')
    day, start, minutes, kind = found.groups()
    moment = _clock(start)
    return TennisSlot(
        weekday=_weekday(day),
        start_minute=moment.hour * 60 + moment.minute,
        duration_minutes=int(minutes),
        session_type=TennisSessionType(kind.upper()) if kind else TennisSessionType.UNKNOWN,
    )


def _resources_text(resources: Resources) -> str:
    pool = resources.pool
    pool_text = (
        "-" if pool is None else ("none" if not pool.available else f"{pool.length_m or '?'} m")
    )
    equipment = ",".join(sorted(item.value for item in resources.strength_equipment)) or "-"
    slots = "; ".join(
        f"{slot.weekday.name[:3].lower()} "
        f"{slot.start_minute // 60:02d}:{slot.start_minute % 60:02d}"
        f" {slot.duration_minutes} min {slot.session_type.value}"
        for slot in resources.tennis_schedule
    )
    return (
        f"gym {resources.gym}, equipment {equipment}, pool {pool_text}, bicycle "
        f"{resources.bicycle}, smart trainer {resources.smart_trainer}, tennis {slots or 'none'}"
    )


def resources(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    known = runtime.inputs.profile(scope).resources
    current = Resources() if known is None or known.value is None else known.value
    changes = (
        args.gym,
        args.equipment,
        args.pool,
        args.bicycle,
        args.smart_trainer,
        args.tennis,
        args.no_tennis,
    )
    if not any(changes):
        print("not declared" if known is None else _resources_text(current), file=out)
        return 0
    try:
        updated = current
        if args.gym is not None:
            updated = replace(updated, gym=_yes_no(args.gym))
        if args.equipment is not None:
            items = frozenset(Equipment(item.strip().upper()) for item in args.equipment.split(","))
            updated = replace(updated, strength_equipment=items)
        if args.pool is not None:
            pool = PoolAccess(False) if args.pool == "none" else PoolAccess(True, float(args.pool))
            updated = replace(updated, pool=pool)
        if args.bicycle is not None:
            updated = replace(updated, bicycle=_yes_no(args.bicycle))
        if args.smart_trainer is not None:
            updated = replace(updated, smart_trainer=_yes_no(args.smart_trainer))
        if args.no_tennis:
            updated = replace(updated, tennis_schedule=())
        elif args.tennis:
            updated = replace(updated, tennis_schedule=tuple(_slot(item) for item in args.tennis))
        runtime.inputs.set_resources(scope, updated)
    except (DomainValidationError, ValueError) as error:
        raise CommandError(str(error)) from None
    print(f"resources recorded: {_resources_text(updated)}", file=out)
    return 0


def _race(text: str) -> tuple[str, RaceResult]:
    found = _RACE.match(text.strip().lower())
    if found is None:
        raise CommandError(f"invalid race {text!r}; for example 10k=48:30@2026-09-10")
    distance, clock, day = found.groups()
    return distance, RaceResult(
        duration(clock), date.fromisoformat(day), ValueSource.ATHLETE_DECLARED
    )


def experience(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    sport = SPORTS[args.sport]
    try:
        if args.unknown:
            runtime.inputs.record_unknown_experience(scope, sport)
            print(f"{sport.value} experience recorded as unknown", file=out)
            return 0
        match sport:
            case Sport.RUNNING:
                races = dict(_race(item) for item in args.race)
                made: SportExperience = RunningExperience(
                    years_experience=args.years,
                    weekly_distance_km=args.weekly_km,
                    longest_distance_km=args.longest_km,
                    recent_5k=races.get("5k"),
                    recent_10k=races.get("10k"),
                    recent_half_marathon=races.get("half"),
                    recent_marathon=races.get("marathon"),
                )
            case Sport.CYCLING:
                known = runtime.inputs.profile(scope).cycling_experience
                ftp = None if known is None or known.value is None else known.value.ftp
                made = CyclingExperience(
                    years_experience=args.years,
                    weekly_distance_km=args.weekly_km,
                    usual_ride_distance_km=args.usual_km,
                    longest_ride_km=args.longest_km,
                    ftp=ftp,  # set with coach profile ftp
                    power_meter_available=_yes_no(args.power_meter),
                )
            case Sport.SWIMMING:
                made = SwimmingExperience(
                    years_experience=args.years,
                    weekly_distance_m=args.weekly_m,
                    typical_pace_100m_seconds=None
                    if args.pace_100m is None
                    else duration(args.pace_100m),
                    longest_distance_m=args.longest_m,
                    pool_length_m=args.pool_length,
                )
            case Sport.STRENGTH:
                made = StrengthExperience(
                    years_experience=args.years,
                    sessions_per_week=args.sessions_per_week,
                    familiar_exercise_ids=tuple(args.exercise),
                    rpe_familiar=_yes_no(args.rpe_familiar),
                    rir_familiar=_yes_no(args.rir_familiar),
                )
            case _:
                made = TennisExperience(
                    sessions_per_week=args.sessions_per_week,
                    typical_duration_minutes=args.minutes,
                    typical_rpe=args.rpe,
                    profile=None if args.profile is None else TennisProfile(args.profile.upper()),
                )
        runtime.inputs.set_experience(scope, made)
    except (DomainValidationError, ValueError) as error:
        raise CommandError(str(error)) from None
    print(f"{sport.value} experience recorded", file=out)
    return 0


# ------------------------------------------------------------------ availability


def _window(text: str) -> tuple[Weekday, TimeWindow]:
    found = _WINDOW.match(text.strip().lower())
    if found is None:
        raise CommandError(f'invalid window {text!r}; for example "tue 18:00-20:00"')
    day, start, end = found.groups()
    return _weekday(day), TimeWindow(_clock(start), _clock(end))


def availability(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    inputs = runtime.inputs
    notice = None
    try:
        match args.action:
            case "show":
                return _show_availability(runtime, scope, args.days, out)
            case "set":
                windows: dict[Weekday, list[TimeWindow]] = {}
                for item in args.window:
                    day, window = _window(item)
                    windows.setdefault(day, []).append(window)
                weekly = WeeklyAvailability(
                    days_per_week=args.days,
                    maximum_hours_per_week=args.max_hours,
                    preferred_rest_days=frozenset(_weekday(item) for item in args.rest_day),
                    daily_windows={day: tuple(found) for day, found in windows.items()},
                    double_sessions_allowed=args.double_sessions,
                    restrictions=args.restrictions,
                )
                _saved, notice = inputs.set_weekly(scope, weekly)
            case "default":
                sports = frozenset(SPORTS[item] for item in args.sport) or None
                inputs.set_defaults(scope, DayDefaults(args.minutes, sports))
            case "exception":
                sports = frozenset(SPORTS[item] for item in args.sport) or None
                exception = AvailabilityException(
                    day=args.day,
                    available=not args.unavailable,
                    maximum_duration_minutes=None if args.unavailable else args.minutes,
                    allowed_sports=sports,
                    reason=args.reason,
                )
                _saved, notice = inputs.set_exception(scope, exception)
            case _:
                inputs.remove_exception(scope, args.day)
    except (DomainValidationError, ValueError) as error:
        raise CommandError(str(error)) from None
    if notice is not None:
        print(notice, file=out)
    print("availability recorded", file=out)
    return 0


def _show_availability(runtime: Runtime, scope: OwnerScope, days: int, out: TextIO) -> int:
    if not 1 <= days <= 366:
        raise CommandError("days must be 1 to 366")
    found = runtime.inputs.availability(scope)
    if found is None:
        print("not declared", file=out)
        return 0
    try:
        today = runtime.recovery.today(scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    if found.recurring is not None:
        weekly = found.recurring
        rest = ", ".join(day.name[:3].lower() for day in sorted(weekly.preferred_rest_days)) or "-"
        print(
            f"weekly: {weekly.days_per_week} days, at most {weekly.maximum_hours_per_week:g} h, "
            f"rest days {rest}",
            file=out,
        )
    for offset in range(days):
        day = today + timedelta(days=offset)
        effective = found.resolve(day)
        if not effective.plannable:
            state = "undefined"
        elif not effective.available:
            state = "unavailable"
        else:
            sports = (
                ""
                if effective.allowed_sports is None
                else " " + ",".join(sorted(item.value for item in effective.allowed_sports))
            )
            state = f"{effective.maximum_duration_minutes} min{sports}"
        print(f"  {day} {day.strftime('%a').lower()}  {state} ({effective.basis.value})", file=out)
    return 0


# ------------------------------------------------------------------ events and goals


def event(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    if args.action == "list":
        for item in runtime.inputs.events(scope):
            distance = (
                "" if item.distance_meters is None else f" {item.distance_meters / 1000:g} km"
            )
            print(
                f"{item.event_id}  {item.event_date}  {item.priority.value}  "
                f"{item.sport.value}{distance}  {item.name}",
                file=out,
            )
        return 0
    try:
        made, notice = runtime.inputs.add_event(
            scope,
            name=args.name,
            sport=SPORTS[args.sport],
            event_date=args.date,
            priority=GoalPriority(args.priority),
            distance_meters=None if args.distance_km is None else args.distance_km * 1000,
            location=args.location,
            expected_conditions=args.conditions,
        )
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    if notice is not None:
        print(notice, file=out)
    print(f"event added: {made.event_id}", file=out)
    return 0


def goal(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    inputs = runtime.inputs
    try:
        match args.action:
            case "list":
                for item in inputs.goals(scope):
                    distance = (
                        ""
                        if item.distance_meters is None
                        else f" {item.distance_meters / 1000:g} km"
                    )
                    target = (
                        ""
                        if item.target_time_seconds is None
                        else f" in {timedelta(seconds=round(item.target_time_seconds))}"
                    )
                    print(
                        f"{item.goal_id}  {item.target_date}  {item.priority.value}  "
                        f"{item.status.value}  {item.sport.value}{distance}{target}",
                        file=out,
                    )
                return 0
            case "add":
                made = inputs.add_goal(
                    scope,
                    sport=SPORTS[args.sport],
                    target_date=args.date,
                    priority=GoalPriority(args.priority),
                    distance_meters=None if args.distance_km is None else args.distance_km * 1000,
                    target_time_seconds=None
                    if args.target_time is None
                    else duration(args.target_time),
                    event_id=None if args.event is None else UUID(args.event),
                )
                print(f"goal added (DRAFT): {made.goal_id}", file=out)
                return 0
            case "feasibility":
                found = inputs.feasibility(
                    scope, None if args.goal_id is None else UUID(args.goal_id)
                )
                _print_feasibility(found, out)
                return 0
            case "activate":
                changed = inputs.change_goal(scope, UUID(args.goal_id), GoalStatus.ACTIVE)
            case _:
                changed = inputs.change_goal(scope, UUID(args.goal_id), GOAL_CLOSE[args.outcome])
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    except NotFoundError:
        raise CommandError("goal or event not found") from None
    except (DomainValidationError, InvalidTransitionError, ValueError) as error:
        raise CommandError(str(error)) from None
    print(f"goal {changed.goal_id} is {changed.status.value}", file=out)
    return 0


def _time(seconds: float | None) -> str:
    return "unknown" if seconds is None else str(timedelta(seconds=round(seconds)))


def _print_feasibility(found: FeasibilityAssessment, out: TextIO) -> None:
    print(
        f"feasibility: {found.classification.value}, confidence {found.confidence.value} "
        f"(rules {found.rules_version})",
        file=out,
    )
    current = found.current
    if current is not None:
        print(
            f"  current state: {_time(current.seconds)} predicted from "
            f"{_time(current.source_seconds)} over {current.source_distance_m / 1000:g} km on "
            f"{current.source_date} (Riegel 1981)",
            file=out,
        )
    if found.gap_seconds is not None:
        print(f"  gap: {_time(max(found.gap_seconds, 0))} to gain", file=out)
    hours = "unknown" if found.hours_per_week is None else f"{found.hours_per_week:g} h"
    print(f"  available: {found.weeks_available:.1f} weeks, {hours} a week", file=out)
    if found.required_percent_per_week is not None and found.required_seconds_per_week:
        print(
            f"  required progression: {found.required_seconds_per_week:.0f} s a week "
            f"({found.required_percent_per_week:.2f} % a week)",
            file=out,
        )
    for reason in found.reasons:
        print(f"  reason: {reason.value}", file=out)
    if found.presents_alternatives and found.desired_seconds is not None:
        print(f"  desired goal: {_time(found.desired_seconds)}", file=out)
        print(f"  realistic goal: {_time(found.realistic_seconds)}", file=out)
        print(f"  minimum satisfactory goal: {_time(found.minimum_seconds)}", file=out)
    print("an estimate from your data, never a guarantee of the outcome", file=out)


def readiness(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    report = runtime.inputs.readiness(scope)
    found = report.readiness
    print(f"readiness: {found.status.value} (rules {found.rules_version})", file=out)
    for item in found.missing_required:
        print(f"  missing (required): {LABELS[item]}", file=out)
    for item in found.missing_preferred:
        print(f"  missing (preferred): {LABELS[item]}", file=out)
    if report.sufficiency is not None:
        if report.sufficiency.sufficient:
            print("history for the goal sport: sufficient", file=out)
        else:
            gaps = ", ".join(item.value for item in report.sufficiency.gaps)
            print(f"history for the goal sport: insufficient ({gaps})", file=out)
    print(f"coaching state: {report.lifecycle.value}", file=out)
    return 0
