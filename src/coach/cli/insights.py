"""``coach insights`` commands (P11; insights spec).

Detection runs pattern rules 1.0.0 over the athlete's data; this module only formats what it
found. Observations are written in code; interpretation, hypothesis and recommendation come
with the AI coach (P13).
"""

from __future__ import annotations

import argparse
from typing import TextIO

from coach.application.ownership import OwnerScope
from coach.cli.activities import TIMEZONE_HINT, CommandError
from coach.cli.runtime import Runtime, TimezoneRequiredError
from coach.domain.insights import Insight


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    insights = commands.add_parser("insights", help="patterns found in your data").add_subparsers(
        dest="action", required=True
    )
    insights.add_parser("detect", help="look for patterns up to today")
    listing = insights.add_parser("list", help="insights found before")
    listing.add_argument("--days", type=int, default=28)


def _print(insight: Insight, out: TextIO) -> None:
    period = insight.period
    print(
        f"{insight.kind.value}  {period.start} to {period.end}  "
        f"confidence {insight.confidence.value}",
        file=out,
    )
    print(f"  {insight.observation}", file=out)
    association = insight.association
    if association is not None:
        variables = " and ".join(association.variables)
        print(
            f"  association of {variables}: {association.sample_size} pairs in "
            f"{association.window_days} days (not a cause)",
            file=out,
        )
    if insight.uncertainty_reasons:
        reasons = ", ".join(sorted(item.value for item in insight.uncertainty_reasons))
        print(f"  uncertainty: {reasons}", file=out)


def detect(runtime: Runtime, scope: OwnerScope, out: TextIO) -> int:
    try:
        report = runtime.insights.detect(scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    print(f"insights to {report.day}: {len(report.insights)} found ({report.new} new)", file=out)
    for insight in report.insights:
        _print(insight, out)
    if report.not_evaluated:
        skipped = ", ".join(f"{kind.value} ({why.value})" for kind, why in report.not_evaluated)
        print(f"not evaluated: {skipped}", file=out)
    print("interpretation and recommendations arrive with the AI coach", file=out)
    return 0


def list_insights(
    runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO
) -> int:
    if not 1 <= args.days <= 366:
        raise CommandError("days must be 1 to 366")
    try:
        today = runtime.recovery.today(scope)
    except TimezoneRequiredError:
        raise CommandError(TIMEZONE_HINT) from None
    found = runtime.insights.recent(scope, until=today, days=args.days)
    for insight in found:
        _print(insight, out)
    if not found:
        print("no insights in these days", file=out)
    return 0
