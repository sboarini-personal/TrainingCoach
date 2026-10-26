"""``coach garmin`` commands: sync activities and show the integration status (P7).

Output shows codes, dates and counts only. The API key is stored with
``coach secret set garmin-api-key`` (hidden input); nothing here reads or prints it.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Final, TextIO

from coach.application.consent import ConsentRequiredError
from coach.application.credentials import CredentialKind, CredentialStatus, ExpiryState
from coach.application.ownership import OwnerScope
from coach.application.sync import CapabilityUnavailableError, SyncOutcome, SyncReport
from coach.cli.activities import CONSENT_HINT, TIMEZONE_HINT, CommandError
from coach.cli.runtime import Runtime, TimezoneRequiredError, athlete_timezone
from coach.domain.coaching.state import Condition
from coach.domain.integration.status import IntegrationStatus

PROVIDER: Final = "garmin"
KEY_HINT: Final = (
    "store your Garmin Personal API key first (input is hidden): "
    "coach secret set garmin-api-key --expires-on YYYY-MM-DD"
)
RENEW_HINT: Final = (
    "create a new key in Garmin Connect and store it with: coach secret set garmin-api-key"
)


def _instant(value: datetime | None) -> str:
    return "never" if value is None else value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _key(runtime: Runtime, scope: OwnerScope) -> CredentialStatus | None:
    kind = CredentialKind.GARMIN_PERSONAL_API_KEY
    return next((item for item in runtime.credentials.status(scope) if item.kind is kind), None)


def _key_line(key: CredentialStatus | None) -> str:
    if key is None:
        return "key: not stored"
    expiry = "no expiry recorded" if key.expires_on is None else f"expires {key.expires_on}"
    return f"key: {expiry} ({key.state.value})"


def _reminder(key: CredentialStatus | None, out: TextIO) -> None:
    """SEC-SEC-004: remind 7 days before expiry, and after."""
    if key is not None and key.state in {ExpiryState.EXPIRING, ExpiryState.EXPIRED}:
        when = "expired" if key.state is ExpiryState.EXPIRED else f"expires on {key.expires_on}"
        print(f"warning: the Garmin key {when}; {RENEW_HINT}", file=out)


def _state(status: IntegrationStatus) -> str:
    if status.reason is None:
        return status.sync_state.value
    detail = "" if status.detail is None else f" {status.detail}"
    return f"{status.sync_state.value} ({status.reason.value}{detail})"


def status(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    key = _key(runtime, scope)
    current = runtime.sync.status(scope, PROVIDER)
    print(f"provider: {PROVIDER}", file=out)
    print(_key_line(key), file=out)
    if current is None:
        print("sync: NEVER_SYNCED", file=out)
        _reminder(key, out)
        return 0
    print(f"access tier: {current.capabilities.access_tier.value}", file=out)
    print(f"sync: {_state(current)}", file=out)
    print(f"last successful sync: {_instant(current.last_success_at)}", file=out)
    print(f"last attempt: {_instant(current.last_attempt_at)}", file=out)
    if current.not_before is not None and current.postponed(runtime.clock()):
        print(f"next attempt not before: {_instant(current.not_before)}", file=out)
    completed = current.cursor.completed_through
    print(f"synced through: {'-' if completed is None else completed}", file=out)
    if Condition.INTEGRATION_ERROR in runtime.conditions.current(scope).conditions:
        print("condition: INTEGRATION_ERROR (local data and file import keep working)", file=out)
    print("capabilities:", file=out)
    for capability, state in current.capabilities.states.items():
        print(f"  {capability.value:<16} {state.value.value:<12} {state.basis.value}", file=out)
    _reminder(key, out)
    return 0


def _summary(report: SyncReport) -> str:
    counts = report.counts
    return (
        f"windows={counts.windows} listed={counts.listed} known={counts.known} "
        f"imported={counts.imported} duplicates={counts.duplicates} rejected={counts.rejected} "
        f"no_file={counts.no_file} gone={counts.gone}"
    )


def sync(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    try:
        athlete_timezone(runtime.profiles, scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    key = _key(runtime, scope)
    if key is None:
        raise CommandError(KEY_HINT)
    try:
        report = runtime.sync.sync(scope, since=args.since)
    except ConsentRequiredError:
        raise CommandError(CONSENT_HINT) from None
    except CapabilityUnavailableError as error:
        raise CommandError(str(error)) from None
    if report.outcome is SyncOutcome.NOT_CONFIGURED:
        raise CommandError(KEY_HINT)
    status = report.status
    print(f"sync: {'-' if status is None else _state(status)}", file=out)
    if report.requested is not None:
        print(f"dates: {report.requested.start} to {report.requested.end}", file=out)
    print(_summary(report), file=out)
    if status is not None and report.outcome is SyncOutcome.PENDING:
        print(f"last successful sync: {_instant(status.last_success_at)}", file=out)
        if status.not_before is not None:
            print(f"next attempt not before: {_instant(status.not_before)}", file=out)
        print("local history and file import keep working", file=out)
    if report.counts.rejected:
        print("some payloads were rejected; see: coach issue list", file=out)
    _reminder(key, out)
    return 0 if report.outcome is SyncOutcome.SYNCED else 1
