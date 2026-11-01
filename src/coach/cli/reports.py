"""``coach report`` commands: the data part of the reports (P11; reporting spec).

Numbers come from the report service with their evidence; this module only formats them, or
prints the report-data document with ``--json``. ``--narrative`` adds the AI coach's
narrative, written from the data part only (P13.5; FR-AGT-004).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import TextIO
from uuid import UUID

from coach.agent.narratives import Narrator
from coach.application.consent import ConsentRequiredError
from coach.application.contracts.reports import report_to_contract
from coach.application.errors import NotFoundError, TimezoneRequiredError
from coach.application.llm import LLMUnavailableError
from coach.application.ownership import OwnerScope
from coach.cli.activities import CONSENT_HINT, TIMEZONE_HINT, CommandError
from coach.cli.analytics import amount, week_argument
from coach.cli.chat import ai_unavailable
from coach.cli.runtime import Runtime
from coach.domain.common.errors import DomainValidationError
from coach.domain.reports import ReportData, ReportItem


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    report = commands.add_parser("report", help="the data part of your reports").add_subparsers(
        dest="action", required=True
    )
    activity = report.add_parser("activity", help="post-activity report")
    activity.add_argument("activity_id")
    week = report.add_parser("week", help="weekly report (ISO week, this week by default)")
    week.add_argument("--week", help="ISO week, for example 2026-W44")
    month = report.add_parser("month", help="monthly report (this month by default)")
    month.add_argument("--month", help="YYYY-MM")
    for parser in (activity, week, month):
        parser.add_argument("--json", action="store_true", help="the report-data document")
        parser.add_argument(
            "--narrative",
            action="store_true",
            help="add the AI coach's narrative (needs the ai-processing consent)",
        )


def _value(item: ReportItem) -> str:
    value = item.value
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value
    if item.unit is None:
        return f"{value:g}"
    return amount(value, item.unit)


def _print(report: ReportData, title: str, out: TextIO) -> None:
    period = report.period
    print(f"{title} ({period.start} to {period.end})", file=out)
    for section in report.sections:
        if section.unavailable is not None:
            print(f"{section.name}: not available ({section.unavailable.value})", file=out)
            continue
        print(section.name, file=out)
        for item in section.items:
            print(f"  {item.label}: {_value(item)}", file=out)
    reasons = ", ".join(sorted(item.value for item in report.uncertainty_reasons))
    print(f"Confidence: {report.confidence.value}" + (f" ({reasons})" if reasons else ""), file=out)
    missing = ", ".join(
        f"{item.section} {item.item} (unknown)"
        if item.reason is None
        else f"{item.section} ({item.reason.value})"
        for item in report.missing_data
    )
    print(f"Missing data: {missing or 'none'}", file=out)


type NarratorFactory = Callable[[], Narrator]


def _emit(
    report: ReportData,
    title: str,
    args: argparse.Namespace,
    out: TextIO,
    *,
    scope: OwnerScope,
    narrator: NarratorFactory | None,
) -> int:
    if args.json:
        print(json.dumps(report_to_contract(report), ensure_ascii=False, indent=2), file=out)
        return 0
    _print(report, title, out)
    if not getattr(args, "narrative", False) or narrator is None:
        return 0
    try:
        narrative = narrator().report(scope, report)
    except (ConsentRequiredError, LLMUnavailableError) as error:
        print(f"Narrative: not available ({ai_unavailable(error)})", file=out)
        return 1
    print(f"Narrative (AI coach, from the data above): {narrative.outcome.value}", file=out)
    print(narrative.text, file=out)
    return 0


def activity(
    runtime: Runtime,
    scope: OwnerScope,
    args: argparse.Namespace,
    out: TextIO,
    *,
    narrator: NarratorFactory | None = None,
) -> int:
    try:
        report = runtime.reports.post_activity(scope, UUID(args.activity_id))
    except ValueError:
        raise CommandError("invalid activity ID") from None
    except NotFoundError:
        raise CommandError("activity not found") from None
    except ConsentRequiredError:
        raise CommandError(CONSENT_HINT) from None
    title = f"post-activity report {report.subject}"
    return _emit(report, title, args, out, scope=scope, narrator=narrator)


def week(
    runtime: Runtime,
    scope: OwnerScope,
    args: argparse.Namespace,
    out: TextIO,
    *,
    narrator: NarratorFactory | None = None,
) -> int:
    key = week_argument(args.week, runtime, scope)
    try:
        report = runtime.reports.weekly(scope, key)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    return _emit(report, f"weekly report {key}", args, out, scope=scope, narrator=narrator)


def month(
    runtime: Runtime,
    scope: OwnerScope,
    args: argparse.Namespace,
    out: TextIO,
    *,
    narrator: NarratorFactory | None = None,
) -> int:
    try:
        key = args.month or runtime.recovery.today(scope).strftime("%Y-%m")
        report = runtime.reports.monthly(scope, key)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    return _emit(report, f"monthly report {key}", args, out, scope=scope, narrator=narrator)
