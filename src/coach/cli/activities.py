"""``coach`` commands for consent, profile timezone, file import, activities and issues.

They run after authentication with the owner scope from ``AuthorizationService``. Output
shows codes and the athlete's own values; file content is never echoed.
"""

from __future__ import annotations

import argparse
from typing import Final, TextIO

from coach.application.consent import ConsentRequiredError
from coach.application.csv_export import export_activities
from coach.application.errors import InvalidOperationError, NotFoundError
from coach.application.ingestion import ImportResult
from coach.application.ownership import OwnerScope
from coach.cli.runtime import (
    PRIVACY_POLICY_VERSION,
    Runtime,
    TimezoneRequiredError,
    athlete_timezone,
)
from coach.domain.activity.activity import Activity
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import FieldValue
from coach.domain.common.time import parse_timezone
from coach.domain.consent.ledger import ConsentType
from coach.domain.ingestion.record import IngestionOutcome
from coach.domain.raw.source import clean_filename

CONSENT_TYPES: Final = {
    "activities": ConsentType.ACTIVITIES,
    "recovery": ConsentType.RECOVERY,
    "sleep": ConsentType.SLEEP,
    "hrv": ConsentType.HRV,
    "provider-recovery-metrics": ConsentType.PROVIDER_RECOVERY_METRICS,
    "long-term-storage": ConsentType.LONG_TERM_STORAGE,
    "ai-processing": ConsentType.AI_PROCESSING,
}
PURPOSES: Final = {
    ConsentType.ACTIVITIES: "Activity data from files and providers (privacy spec section 1).",
    ConsentType.RECOVERY: "Resting heart rate and daily recovery summaries.",
    ConsentType.SLEEP: "Sleep data.",
    ConsentType.HRV: "Heart-rate variability.",
    ConsentType.PROVIDER_RECOVERY_METRICS: "Proprietary recovery metrics (Body Battery, stress).",
    ConsentType.LONG_TERM_STORAGE: "Keeping data beyond the active coaching period.",
    ConsentType.AI_PROCESSING: "Sending personal data to the external LLM provider.",
}
TIMEZONE_HINT: Final = (
    "set your timezone first, for example: coach profile timezone America/Argentina/Buenos_Aires"
)
CONSENT_HINT: Final = "grant the ACTIVITIES consent first: coach consent grant activities"


class CommandError(Exception):
    """A user-facing error; its message never contains secrets or file content."""


def list_consents(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    ledger = runtime.consents.ledger(scope)
    for name, consent_type in CONSENT_TYPES.items():
        status = ledger.status(consent_type, policy_version=PRIVACY_POLICY_VERSION)
        print(f"{name:<26} {status.value}", file=out)
    return 0


def change_consent(
    runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO
) -> int:
    consent_type = CONSENT_TYPES[args.type]
    try:
        if args.action == "grant":
            runtime.consents.grant(scope, consent_type, purpose=PURPOSES[consent_type])
        else:
            runtime.consents.revoke(scope, consent_type)
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    print(f"{args.type}: {'granted' if args.action == 'grant' else 'revoked'}", file=out)
    return 0


def timezone(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    if args.name is None:
        try:
            print(athlete_timezone(runtime.profiles, scope).key, file=out)
        except TimezoneRequiredError:
            print("not set", file=out)
        return 0
    try:
        parse_timezone(args.name)
        current: AthleteProfile | None = runtime.profiles.get(scope, scope.athlete_id)
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    except NotFoundError:
        current = None
    update = ProfileUpdate(timezone=FieldValue.declared(args.name, at=runtime.clock()))
    if current is None:
        runtime.profiles.add(scope, AthleteProfile.new(scope.athlete_id).apply(update))
    else:
        runtime.profiles.update(scope, current.apply(update), expected_version=current.version)
    print(f"timezone set to {args.name}", file=out)
    return 0


def _describe(result: ImportResult) -> str:
    record = result.record
    if record.outcome in {IngestionOutcome.IMPORTED, IngestionOutcome.DUPLICATE}:
        outcomes = ", ".join(sorted(item.outcome.value for item in record.activities))
        return f"{record.outcome.value}: {len(record.activities)} activities ({outcomes})"
    reason = "" if record.reason is None else record.reason.value
    detail = "" if record.detail is None else f" {record.detail}"
    member = "" if record.failed_file_index is None else f" in member {record.failed_file_index}"
    return f"{record.outcome.value}: {reason}{detail}{member}"


def import_files(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    try:
        athlete_timezone(runtime.profiles, scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    status = 0
    for path in args.paths:
        name = clean_filename(str(path)) or "file"
        try:
            with path.open("rb") as handle:
                result = runtime.ingestion.import_file(scope, handle, filename=path.name)
        except ConsentRequiredError:
            raise CommandError(CONSENT_HINT) from None
        except OSError:
            print(f"{name}: cannot be read", file=out)
            status = 1
            continue
        print(f"{name}: {_describe(result)}", file=out)
        if result.secret_notice is not None:
            print(result.secret_notice, file=out)
        if not result.record.succeeded:
            status = 1
    return status


PROGRESS_EVERY: Final = 100


def reprocess(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    """Re-normalise imported files with the current rules (activities spec 1.1)."""

    def progress(done: int, total: int) -> None:
        if done % PROGRESS_EVERY == 0 and done < total:
            print(f"  {done} of {total} sources", file=out, flush=True)

    try:
        athlete_timezone(runtime.profiles, scope)
        report = runtime.ingestion.reprocess(scope, progress=progress)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    except ConsentRequiredError:
        raise CommandError(CONSENT_HINT) from None
    print(
        f"reprocessed {report.reprocessed} sources ({report.activities} activities rebuilt), "
        f"{report.up_to_date} already up to date",
        file=out,
    )
    for reason, count in sorted(report.skipped.items()):
        print(f"skipped {count}: {reason}", file=out)
    return 0


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = round(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _row(activity: Activity) -> str:
    distance = activity.number("distance.meters")
    subtype = activity.values.get("classification.subtype") or "-"
    kilometres = "-" if distance is None else f"{distance / 1000:.2f} km"
    return (
        f"{activity.training_day}  {activity.sport.value:<8} {subtype!s:<14} "
        f"{_duration(activity.number('timing.elapsedSeconds')):>8}  {kilometres:>10}  "
        f"{activity.status.value:<14} sources={len(activity.sources)}  {activity.activity_id}"
    )


def list_activities(
    runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO
) -> int:
    stored = [item for item in runtime.activity_store.list_all(scope) if item.is_live]
    stored.sort(key=lambda item: item.start_time_utc, reverse=True)
    if not stored:
        print("no activities", file=out)
    for item in stored[: args.limit]:
        print(_row(runtime.activities.effective(scope, item.activity_id).activity), file=out)
    return 0


def export(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    """Activities (corrections applied) in the CSV activity template (SEC-INJ-004)."""
    live = [item for item in runtime.activity_store.list_all(scope) if item.is_live]
    live.sort(key=lambda item: item.start_time_utc)
    views = [runtime.activities.effective(scope, item.activity_id).activity for item in live]
    try:
        args.output.write_bytes(export_activities(views).encode("utf-8"))
    except OSError:
        raise CommandError("cannot write the export file") from None
    print(f"{len(views)} activities exported", file=out)
    return 0


def list_issues(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    issues = runtime.activities.issues(scope)
    if not issues:
        print("no open issues", file=out)
    for issue in sorted(issues, key=lambda item: item.created_at):
        subject = issue.field or ("import" if issue.ingestion_id else "match")
        print(
            f"{issue.created_at.date()}  {issue.kind.value:<16} {issue.code:<22} {subject:<28} "
            f"{issue.issue_id}",
            file=out,
        )
    return 0


def acknowledge_issue(
    runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO
) -> int:
    try:
        runtime.activities.acknowledge(scope, args.issue_id)
    except NotFoundError:
        raise CommandError("issue not found") from None
    except InvalidOperationError as error:
        raise CommandError(str(error)) from None
    print("issue acknowledged", file=out)
    return 0
