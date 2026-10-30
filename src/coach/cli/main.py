"""The ``coach`` command line.

Commands that touch athlete data authenticate with the local API token read from the OS
secret store, then resolve the owner scope (SEC-AUTH-001, SEC-AUTHZ-001). Secrets are read
with hidden input and never printed, logged or accepted as arguments (SEC-SEC-001).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, TextIO
from uuid import UUID

from coach.application.authentication import AuthenticationError
from coach.application.authorization import AuthorizationError
from coach.application.credentials import CredentialKind, credential_handle, expiry_state
from coach.application.errors import RateLimitedError
from coach.application.identity import Identity, Principal
from coach.application.ids import new_id
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, CredentialProvider
from coach.application.secrets import SecretValue
from coach.cli import activities as activity_commands
from coach.cli import analytics as analytics_commands
from coach.cli import garmin as garmin_commands
from coach.cli import strength as strength_commands
from coach.cli.runtime import Runtime, build_runtime
from coach.domain.athlete.physiology import SensorType
from coach.domain.common.errors import DomainValidationError
from coach.domain.strength.vocabulary import MovementPattern
from coach.infrastructure.datadir import SyncedDataDirError, ensure_layout, resolve_data_dir
from coach.infrastructure.logs import attach_file_log, detach_file_log
from coach.integrations.garmin.transport import HttpTransport

SECRET_KINDS: Final = {
    "garmin-api-key": CredentialKind.GARMIN_PERSONAL_API_KEY,
    "llm-api-key": CredentialKind.LLM_API_KEY,
}
_NOT_INITIALISED: Final = "not initialised; run `coach init` first"


class CliError(Exception):
    """A user-facing error; its message never contains secrets."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coach", description="Personal multisport coach")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create the installation and its local API token")
    token = commands.add_parser("token", help="local API token").add_subparsers(
        dest="action", required=True
    )
    token.add_parser("rotate", help="replace the local API token")
    secret = commands.add_parser("secret", help="credentials in the OS secret store")
    secret_actions = secret.add_subparsers(dest="action", required=True)
    store = secret_actions.add_parser("set", help="store a credential (input is hidden)")
    store.add_argument("kind", choices=sorted(SECRET_KINDS))
    store.add_argument("--expires-on", type=date.fromisoformat, help="expiry date, YYYY-MM-DD")
    secret_actions.add_parser("list", help="stored credentials and their expiry state")
    audit = commands.add_parser("audit", help="audit log").add_subparsers(
        dest="action", required=True
    )
    audit.add_parser("verify", help="verify the audit hash chain")
    consent = commands.add_parser("consent", help="consents").add_subparsers(
        dest="action", required=True
    )
    consent.add_parser("list", help="consent status per type")
    for action in ("grant", "revoke"):
        change = consent.add_parser(action, help=f"{action} a consent")
        change.add_argument("type", choices=sorted(activity_commands.CONSENT_TYPES))
    profile = commands.add_parser("profile", help="athlete profile").add_subparsers(
        dest="action", required=True
    )
    zone = profile.add_parser("timezone", help="show or set the IANA timezone")
    zone.add_argument("name", nargs="?", help="for example America/Argentina/Buenos_Aires")
    zones = profile.add_parser(
        "hr-zones",
        help="show heart-rate zone models, or add one (same start date: replaced)",
    )
    zones.add_argument(
        "boundaries",
        nargs="*",
        type=int,
        help="2 to 11 ascending bpm, e.g. 100 130 150 170; percentages with --age-predicted",
    )
    zones.add_argument("--valid-from", type=date.fromisoformat, help="YYYY-MM-DD; default today")
    zones.add_argument(
        "--method", choices=sorted(analytics_commands.ZONE_METHODS), help="default custom"
    )
    zones.add_argument(
        "--age-predicted",
        action="store_true",
        help="boundaries are %% of 220 - age on each training day: an estimate (ADR-028)",
    )
    sensors = profile.add_parser(
        "sensor", help="show declared sensors, or declare one (a power meter makes power MEASURED)"
    )
    sensors.add_argument("type", nargs="?", choices=sorted(item.value for item in SensorType))
    sensors.add_argument("--model", help="free text, for example the device name")
    sensors.add_argument(
        "--reliable", action="store_true", help="reliable for training (cycling spec 2)"
    )
    sensors.add_argument("--remove", action="store_true", help="forget this sensor")
    threshold = profile.add_parser("ftp", help="show or set the functional threshold power")
    threshold.add_argument("watts", nargs="?", type=int, help="50 to 600")
    threshold.add_argument("--on", type=date.fromisoformat, help="YYYY-MM-DD; default today")
    threshold.add_argument(
        "--measured", action="store_true", help="from a test with measured power"
    )
    birth = profile.add_parser("birth-date", help="show or set the birth date")
    birth.add_argument("date", nargs="?", type=date.fromisoformat, help="YYYY-MM-DD")
    upload = commands.add_parser("import", help="import FIT, TCX, GPX, CSV or ZIP files")
    upload.add_argument("paths", nargs="+", type=Path)
    activity = commands.add_parser("activity", help="activities").add_subparsers(
        dest="action", required=True
    )
    show = activity.add_parser("show", help="one activity with its metrics")
    show.add_argument("activity_id", type=UUID)
    listing = activity.add_parser("list", help="most recent activities, corrections applied")
    listing.add_argument("--limit", type=int, default=20)
    activity.add_parser(
        "reprocess", help="re-read imported files with the current rules (laps, lengths, sensors)"
    )
    correction = activity.add_parser(
        "correct", help="correct one field, for example subjective.rpe 7 (unknown: UNKNOWN)"
    )
    correction.add_argument("activity_id", type=UUID)
    correction.add_argument("field", help="a field path, for example subjective.rpe")
    correction.add_argument("value", help="the corrected value, or unknown")
    correction.add_argument("--reason", required=True, help="why (kept with the correction)")
    exporting = activity.add_parser("export", help="write activities as CSV (activity template)")
    exporting.add_argument("output", type=Path)
    issue = commands.add_parser("issue", help="data quality issues").add_subparsers(
        dest="action", required=True
    )
    issue.add_parser("list", help="open issues")
    acknowledge = issue.add_parser("ack", help="acknowledge an issue")
    acknowledge.add_argument("issue_id", type=UUID)
    metrics = commands.add_parser("metrics", help="derived metrics").add_subparsers(
        dest="action", required=True
    )
    metrics.add_parser("recompute", help="recompute the metrics of every activity")
    load = commands.add_parser("load", help="training load and volume").add_subparsers(
        dest="action", required=True
    )
    week = load.add_parser("week", help="one ISO week by sport, and across sports")
    week.add_argument("--week", help="ISO week, for example 2026-W44; default this week")
    weeks = load.add_parser("weeks", help="the last weeks, one line each")
    weeks.add_argument("--count", type=int, default=8)
    strength = commands.add_parser("strength", help="strength training").add_subparsers(
        dest="action", required=True
    )
    catalogue = strength.add_parser(
        "exercises", help="list or search the exercise catalogue; one match shows it in full"
    )
    catalogue.add_argument("text", nargs="?", help="an ID, a name, an alias or part of one")
    catalogue.add_argument(
        "--pattern", choices=sorted(pattern.value for pattern in MovementPattern)
    )
    naming = strength.add_parser("alias", help="show, add or remove your own exercise names")
    naming.add_argument("text", nargs="?", help="your name for the exercise")
    naming.add_argument("exercise_id", nargs="?", help="for example ex.bulgarian_split_squat")
    naming.add_argument("--remove", action="store_true", help="forget this alias")
    executed = strength.add_parser("sets", help="the sets of a strength activity as executed")
    executed.add_argument("activity_id", type=UUID)
    logging = strength.add_parser(
        "log",
        help="enter a set (same exercise and set number: replaced); the watch's set wins"
        " only where you leave a value out",
    )
    logging.add_argument("activity_id", type=UUID)
    logging.add_argument("exercise", help="an ID, a catalogue name or one of your aliases")
    logging.add_argument("--set", type=int, dest="set_number", help="default: the next set")
    logging.add_argument("--reps", type=int, help="repetitions completed")
    logging.add_argument("--load", type=float, help="external load in kg (0: bodyweight)")
    logging.add_argument("--rpe", type=float, help="1 to 10 in steps of 0.5")
    logging.add_argument("--rir", type=int, help="repetitions in reserve, 0 to 10")
    logging.add_argument("--target-reps", type=int)
    logging.add_argument("--target-load", type=float)
    logging.add_argument("--target-rpe", type=float)
    logging.add_argument("--remove", action="store_true", help="forget your entry for this set")
    relabelling = strength.add_parser(
        "relabel", help="name an exercise the watch recorded wrongly or did not recognise"
    )
    relabelling.add_argument("activity_id", type=UUID)
    relabelling.add_argument("watch_exercise", help="as `coach strength sets` shows it")
    relabelling.add_argument("exercise", nargs="?", help="an ID, a catalogue name or an alias")
    relabelling.add_argument("--remove", action="store_true", help="forget the relabel")
    watching = strength.add_parser("watch-sets", help="use or ignore the watch's sets")
    watching.add_argument("activity_id", type=UUID)
    watching.add_argument("choice", choices=["use", "ignore"])
    analysis = commands.add_parser("analysis", help="sport analytics").add_subparsers(
        dest="action", required=True
    )
    trending = analysis.add_parser(
        "trend", help="comparable groups of a per-activity metric and their trends"
    )
    trending.add_argument("metric", help="for example run.efficiency_hr or performance.cadence")
    trending.add_argument("--weeks", type=int, default=8, help="1 to 104; default 8")
    trending.add_argument(
        "--stroke", choices=["BACKSTROKE", "BREASTSTROKE", "BUTTERFLY", "FREESTYLE"]
    )
    garmin = commands.add_parser("garmin", help="Garmin Personal API").add_subparsers(
        dest="action", required=True
    )
    garmin.add_parser("status", help="sync state, freshness, capabilities and key expiry")
    synchronise = garmin.add_parser("sync", help="pull new activities from Garmin")
    synchronise.add_argument(
        "--since", type=date.fromisoformat, help="re-scan from this date, YYYY-MM-DD"
    )
    return parser


def _principal(runtime: Runtime) -> Principal:
    owner = runtime.identities.owner()
    if owner is None:
        raise CliError(_NOT_INITIALISED)
    token = runtime.provider.get(
        credential_handle(CredentialKind.LOCAL_API_TOKEN, owner.athlete_id)
    )
    return runtime.authentication.authenticate("cli", token)


def _session(runtime: Runtime) -> OwnerScope:
    return runtime.authorization.scope_for(_principal(runtime))


def _init(runtime: Runtime, _args: argparse.Namespace, out: TextIO) -> int:
    if runtime.identities.owner() is not None:
        print(f"already initialised: data in {runtime.layout.root}", file=out)
        return 0
    identity = Identity(user_id=new_id(), athlete_id=new_id(), created_at=runtime.clock())
    runtime.identities.register_owner(identity)
    runtime.credentials.rotate_local_token(OwnerScope(identity.athlete_id))
    print(f"installation initialised: data in {runtime.layout.root}", file=out)
    print("local API token stored in the OS secret store", file=out)
    return 0


def _rotate_token(runtime: Runtime, _args: argparse.Namespace, out: TextIO) -> int:
    owner = runtime.identities.owner()
    if owner is None:
        raise CliError(_NOT_INITIALISED)
    handle = credential_handle(CredentialKind.LOCAL_API_TOKEN, owner.athlete_id)
    if runtime.provider.get(handle) is None:
        # Lost token: the OS user already owns the secret store (authentication.spec.md 1.1).
        scope = OwnerScope(owner.athlete_id)
    else:
        scope = _session(runtime)
    runtime.credentials.rotate_local_token(scope)
    print("local API token rotated; the previous token no longer works", file=out)
    return 0


def _set_secret(
    runtime: Runtime, args: argparse.Namespace, out: TextIO, read_secret: Callable[[str], str]
) -> int:
    scope = _session(runtime)
    kind = SECRET_KINDS[args.kind]
    value = read_secret(f"{args.kind} (input hidden): ")
    if not value:
        raise CliError("empty value; nothing stored")
    try:
        record = runtime.credentials.store(
            scope, kind, SecretValue(value), expires_on=args.expires_on
        )
    except DomainValidationError as error:
        raise CliError(str(error)) from None
    state = expiry_state(record.expires_on, today=runtime.clock().date())
    print(f"{kind.value} stored ({state.value})", file=out)
    return 0


def _list_secrets(runtime: Runtime, _args: argparse.Namespace, out: TextIO) -> int:
    statuses = runtime.credentials.status(_session(runtime))
    if not statuses:
        print("no credentials stored", file=out)
    for status in statuses:
        expires = "no expiry" if status.expires_on is None else f"expires {status.expires_on}"
        print(f"{status.kind.value}  {expires}  {status.state.value}", file=out)
    return 0


def _verify_audit(runtime: Runtime, _args: argparse.Namespace, out: TextIO) -> int:
    _session(runtime)
    result = runtime.audit.verify()
    if result.valid:
        print(f"audit log intact: {result.event_count} events", file=out)
        return 0
    print(
        f"audit log BROKEN at sequence {result.first_invalid_sequence} ({result.problem})", file=out
    )
    return 1


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    credentials: CredentialProvider | None = None,
    read_secret: Callable[[str], str] = getpass.getpass,
    out: TextIO | None = None,
    clock: Clock | None = None,
    transport: HttpTransport | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    stream = out if out is not None else sys.stdout
    args = _parser().parse_args(argv)
    try:
        data_dir = resolve_data_dir(env if env is not None else os.environ)
    except SyncedDataDirError as error:
        print(f"error: {error}", file=stream)
        return 2
    if args.command != "init" and not (data_dir / "coach.sqlite3").is_file():
        print(f"error: {_NOT_INITIALISED}", file=stream)
        return 2
    layout = ensure_layout(data_dir)
    handler = attach_file_log(layout.root / "logs" / "coach.log")
    try:
        if credentials is None:
            from coach.infrastructure.keyring_credentials import KeyringCredentialProvider

            credentials = KeyringCredentialProvider()
        runtime = build_runtime(
            layout,
            credentials,
            clock or (lambda: datetime.now(UTC)),
            transport=transport,
            sleep=sleep or time.sleep,
        )
        match (args.command, getattr(args, "action", None)):
            case ("init", _):
                return _init(runtime, args, stream)
            case ("token", "rotate"):
                return _rotate_token(runtime, args, stream)
            case ("secret", "set"):
                return _set_secret(runtime, args, stream, read_secret)
            case ("secret", "list"):
                return _list_secrets(runtime, args, stream)
            case ("audit", "verify"):
                return _verify_audit(runtime, args, stream)
            case ("consent", "list"):
                return activity_commands.list_consents(runtime, _session(runtime), stream)
            case ("consent", _):
                return activity_commands.change_consent(runtime, _session(runtime), args, stream)
            case ("profile", "timezone"):
                return activity_commands.timezone(runtime, _session(runtime), args, stream)
            case ("import", _):
                return activity_commands.import_files(runtime, _session(runtime), args, stream)
            case ("profile", "hr-zones"):
                return analytics_commands.hr_zones(runtime, _session(runtime), args, stream)
            case ("profile", "sensor"):
                return analytics_commands.sensor(runtime, _session(runtime), args, stream)
            case ("profile", "ftp"):
                return analytics_commands.ftp(runtime, _session(runtime), args, stream)
            case ("profile", "birth-date"):
                return analytics_commands.birth_date(runtime, _session(runtime), args, stream)
            case ("activity", "show"):
                return analytics_commands.show_activity(runtime, _session(runtime), args, stream)
            case ("metrics", "recompute"):
                return analytics_commands.recompute(runtime, _session(runtime), stream)
            case ("strength", "exercises"):
                return strength_commands.exercises(runtime, _session(runtime), args, stream)
            case ("strength", "alias"):
                return strength_commands.alias(runtime, _session(runtime), args, stream)
            case ("strength", "sets"):
                return strength_commands.sets(runtime, _session(runtime), args, stream)
            case ("strength", "log"):
                return strength_commands.log(runtime, _session(runtime), args, stream)
            case ("strength", "relabel"):
                return strength_commands.relabel(runtime, _session(runtime), args, stream)
            case ("strength", "watch-sets"):
                return strength_commands.watch_sets(runtime, _session(runtime), args, stream)
            case ("analysis", "trend"):
                return analytics_commands.trend(runtime, _session(runtime), args, stream)
            case ("load", "week"):
                return analytics_commands.load_week(runtime, _session(runtime), args, stream)
            case ("load", "weeks"):
                return analytics_commands.load_weeks(runtime, _session(runtime), args, stream)
            case ("activity", "correct"):
                principal = _principal(runtime)
                scope = runtime.authorization.scope_for(principal)
                return activity_commands.correct(runtime, principal, scope, args, stream)
            case ("activity", "reprocess"):
                return activity_commands.reprocess(runtime, _session(runtime), stream)
            case ("activity", "list"):
                return activity_commands.list_activities(runtime, _session(runtime), args, stream)
            case ("activity", "export"):
                return activity_commands.export(runtime, _session(runtime), args, stream)
            case ("issue", "list"):
                return activity_commands.list_issues(runtime, _session(runtime), stream)
            case ("issue", "ack"):
                return activity_commands.acknowledge_issue(runtime, _session(runtime), args, stream)
            case ("garmin", "status"):
                return garmin_commands.status(runtime, _session(runtime), stream)
            case ("garmin", "sync"):
                return garmin_commands.sync(runtime, _session(runtime), args, stream)
            case _:  # pragma: no cover - argparse rejects anything else
                return 2
    except (CliError, activity_commands.CommandError) as error:
        print(f"error: {error}", file=stream)
        return 2
    except (AuthenticationError, AuthorizationError, RateLimitedError) as error:
        print(f"error: {error}", file=stream)
        return 2
    finally:
        detach_file_log(handler)
