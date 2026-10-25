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
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, TextIO
from uuid import UUID

from coach.application.authentication import AuthenticationError
from coach.application.authorization import AuthorizationError
from coach.application.credentials import CredentialKind, credential_handle, expiry_state
from coach.application.errors import RateLimitedError
from coach.application.identity import Identity
from coach.application.ids import new_id
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, CredentialProvider
from coach.application.secrets import SecretValue
from coach.cli import activities as activity_commands
from coach.cli.runtime import Runtime, build_runtime
from coach.domain.common.errors import DomainValidationError
from coach.infrastructure.datadir import SyncedDataDirError, ensure_layout, resolve_data_dir
from coach.infrastructure.logs import attach_file_log, detach_file_log

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
    upload = commands.add_parser("import", help="import FIT, TCX, GPX, CSV or ZIP files")
    upload.add_argument("paths", nargs="+", type=Path)
    activity = commands.add_parser("activity", help="activities").add_subparsers(
        dest="action", required=True
    )
    listing = activity.add_parser("list", help="most recent activities, corrections applied")
    listing.add_argument("--limit", type=int, default=20)
    exporting = activity.add_parser("export", help="write activities as CSV (activity template)")
    exporting.add_argument("output", type=Path)
    issue = commands.add_parser("issue", help="data quality issues").add_subparsers(
        dest="action", required=True
    )
    issue.add_parser("list", help="open issues")
    acknowledge = issue.add_parser("ack", help="acknowledge an issue")
    acknowledge.add_argument("issue_id", type=UUID)
    return parser


def _session(runtime: Runtime) -> OwnerScope:
    owner = runtime.identities.owner()
    if owner is None:
        raise CliError(_NOT_INITIALISED)
    token = runtime.provider.get(
        credential_handle(CredentialKind.LOCAL_API_TOKEN, owner.athlete_id)
    )
    principal = runtime.authentication.authenticate("cli", token)
    return runtime.authorization.scope_for(principal)


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
        runtime = build_runtime(layout, credentials, clock or (lambda: datetime.now(UTC)))
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
            case ("activity", "list"):
                return activity_commands.list_activities(runtime, _session(runtime), args, stream)
            case ("activity", "export"):
                return activity_commands.export(runtime, _session(runtime), args, stream)
            case ("issue", "list"):
                return activity_commands.list_issues(runtime, _session(runtime), stream)
            case ("issue", "ack"):
                return activity_commands.acknowledge_issue(runtime, _session(runtime), args, stream)
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
