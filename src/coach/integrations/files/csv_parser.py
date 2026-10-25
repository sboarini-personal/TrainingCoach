"""CSV parser (runs in the sandbox): the application's activity template 1.0.0 only.

Cells are data: they are never evaluated (SEC-INJ-004). Empty cells are values the athlete
did not provide (files spec 8.4).
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Final

from coach.domain.ingestion.templates import ACTIVITY_TEMPLATE, FORMULA_PREFIXES
from coach.integrations.files.parsing import (
    ParserLimits,
    RecordCounter,
    SessionData,
    limit_exceeded,
    output,
    parse_failed,
    parse_time,
    text_number,
)

PARSER: Final = "csv_activity"
VERSION: Final = ACTIVITY_TEMPLATE.version
_OFFSET: Final = re.compile(r"^([+-])([0-9]{2}):([0-5][0-9])$")
_SUMMARY: Final = {
    "elapsed_seconds": "elapsedSeconds",
    "moving_seconds": "movingSeconds",
    "distance_meters": "distanceMeters",
    "elevation_gain_meters": "ascentMeters",
    "average_hr": "avgHeartRate",
    "max_hr": "maxHeartRate",
    "rpe": "rpe",
}
_TEXTS: Final = {"name": "name", "comments": "comments"}


def _unquote(cell: str) -> str:
    """Remove the single quote the export adds before formula-like text (SEC-INJ-004)."""
    if cell.startswith("'") and cell[1:].startswith(FORMULA_PREFIXES):
        return cell[1:]
    return cell


def _offset_minutes(text: str) -> int | None:
    if not text:
        return None
    match = _OFFSET.match(text)
    if match is None:
        raise parse_failed("CSV_VALUE")
    minutes = int(match[2]) * 60 + int(match[3])
    if minutes > 14 * 60:
        raise parse_failed("CSV_VALUE")
    return -minutes if match[1] == "-" else minutes


def _row(index: int, cells: dict[str, str]) -> SessionData:
    start, _ = parse_time(cells["start_time_utc"])
    data = SessionData(
        index=index,
        start=start,
        sport=cells["sport"] or None,
        utc_offset_minutes=_offset_minutes(cells["utc_offset"]),
    )
    for column, key in _SUMMARY.items():
        data.put(key, text_number(cells[column]), f"row[{index}].{column}")
    if cells["subtype"]:
        data.labels["subtype"] = cells["subtype"]
        data.source_paths["subtype"] = f"row[{index}].subtype"
    for column, key in _TEXTS.items():
        if cells[column]:
            data.texts[key] = cells[column]
            data.source_paths[key] = f"row[{index}].{column}"
    return data


def parse_csv(path: Path, limits: ParserLimits) -> dict[str, Any]:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise parse_failed("CSV_ENCODING") from None
    names = [column.name for column in ACTIVITY_TEMPLATE.columns]
    counter = RecordCounter(limits.records)
    sessions: list[SessionData] = []
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        if next(reader, None) != names:
            raise parse_failed("CSV_TEMPLATE")
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            counter.add()
            if len(row) != len(names):
                raise parse_failed("CSV_ROW")
            if len(sessions) >= limits.activities:
                raise limit_exceeded("ACTIVITIES")
            cells = {name: _unquote(cell.strip()) for name, cell in zip(names, row, strict=True)}
            sessions.append(_row(len(sessions), cells))
    except csv.Error:
        raise parse_failed("CSV_FORMAT") from None
    return output("CSV", PARSER, VERSION, records=counter.count, sessions=sessions, limits=limits)
