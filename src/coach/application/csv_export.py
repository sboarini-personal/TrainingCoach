"""Activity export in the CSV activity template (SEC-INJ-004, AT-CSV-001).

Text cells that a spreadsheet could read as a formula (leading ``=``, ``+``, ``-``, ``@``,
tab or carriage return) get a leading single quote; numbers are written as numbers. The
importer removes that quote again, so an export can be re-imported (files spec 8.4).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import datetime
from enum import Enum

from coach.application.contracts._json import instant
from coach.domain.activity.activity import Activity
from coach.domain.ingestion.templates import ACTIVITY_TEMPLATE, FORMULA_PREFIXES


def neutralise(text: str) -> str:
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def cell(value: object) -> str:
    """One exported cell: numbers unchanged, text neutralised, UNKNOWN empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return neutralise(str(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime):
        return instant(value)
    if isinstance(value, Enum):
        return neutralise(str(value.value))
    return neutralise(str(value))


def export_activities(activities: Iterable[Activity]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([column.name for column in ACTIVITY_TEMPLATE.columns])
    for activity in activities:
        writer.writerow(
            [cell(activity.values.get(column.path)) for column in ACTIVITY_TEMPLATE.columns]
        )
    return buffer.getvalue()
