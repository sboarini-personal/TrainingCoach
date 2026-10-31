"""The strength sets CSV template 1.0.0 (files spec 8.4; strength spec 5): import and export.

One row is one set of the activity named on the command line. The header must equal, exactly
(a UTF-8 byte-order mark is ignored)::

    exercise,set,reps,load_kg,rpe,rir,target_reps,target_load_kg,target_rpe

The rows are the athlete's own declarations, as `coach strength log` takes them: untrusted
data, never instructions. They are parsed in-process with the standard library's ``csv``
module under fixed limits (64 KiB, 200 rows, UTF-8, no NUL), every cell read as a typed value
(a whole number, a number with a dot as decimal separator, or the exercise's text, resolved
later exactly against the catalogue and the athlete's aliases); nothing is evaluated. An
empty cell means the value is not provided. Formula-like text is protected on export with a
leading single quote, which the importer removes, as in the activity template (SEC-INJ-004);
numbers are written as numbers.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from coach.domain.ingestion.templates import FORMULA_PREFIXES

TEMPLATE_VERSION: Final = "1.0.0"
HEADER: Final = (
    "exercise",
    "set",
    "reps",
    "load_kg",
    "rpe",
    "rir",
    "target_reps",
    "target_load_kg",
    "target_rpe",
)
MAX_BYTES: Final = 64 * 1024
MAX_ROWS: Final = 200
MAX_EXERCISE: Final = 100
# ASCII digits only (str.isdigit also accepts superscripts), bounded, so int() and float()
# never fail; the domain then checks the ranges.
_WHOLE: Final = re.compile(r"[0-9]{1,6}")
_NUMBER: Final = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6})?")


class TemplateError(ValueError):
    """The file does not follow the template; the message names the line, never its text."""


@dataclass(frozen=True, slots=True)
class SetRow:
    line: int
    exercise: str  # resolved exactly by the caller (catalogue and the athlete's aliases)
    set_number: int
    reps: int | None = None
    load_kg: float | None = None
    rpe: float | None = None
    rir: int | None = None
    target_reps: int | None = None
    target_load_kg: float | None = None
    target_rpe: float | None = None


def _unquoted(cell: str) -> str:
    """Remove the one quote an export put before formula-like text."""
    if cell.startswith("'") and cell[1:].startswith(FORMULA_PREFIXES):
        return cell[1:]
    return cell


def _whole(cell: str, name: str, line: int) -> int | None:
    if not cell:
        return None
    if _WHOLE.fullmatch(cell) is None:
        raise TemplateError(f"line {line}: {name} is a whole number")
    return int(cell)


def _number(cell: str, name: str, line: int) -> float | None:
    if not cell:
        return None
    if _NUMBER.fullmatch(cell) is None:
        raise TemplateError(f"line {line}: {name} is a number with a dot as decimal separator")
    return float(cell)


def parse(data: bytes) -> list[SetRow]:
    """The rows of a strength sets file, or ``TemplateError``."""
    if len(data) > MAX_BYTES:
        raise TemplateError(f"the file is larger than {MAX_BYTES // 1024} KiB")
    if b"\x00" in data:
        raise TemplateError("the file contains NUL bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise TemplateError("the file is not UTF-8") from None
    text = text.removeprefix("\ufeff")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise TemplateError("the file is empty") from None
    except csv.Error:
        raise TemplateError("line 1: not a CSV row") from None
    if tuple(header) != HEADER:
        raise TemplateError("line 1: the header is not the strength sets template")
    rows: list[SetRow] = []
    try:
        for cells in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in cells):
                continue
            if len(rows) >= MAX_ROWS:
                raise TemplateError(f"more than {MAX_ROWS} rows")
            if len(cells) != len(HEADER):
                raise TemplateError(f"line {line}: a row has {len(HEADER)} cells")
            values = [_unquoted(cell.strip()) for cell in cells]
            exercise = values[0]
            if not exercise or len(exercise) > MAX_EXERCISE:
                raise TemplateError(
                    f"line {line}: the exercise is required, at most 100 characters"
                )
            number = _whole(values[1], "set", line)
            if number is None:
                raise TemplateError(f"line {line}: the set number is required")
            rows.append(
                SetRow(
                    line=line,
                    exercise=exercise,
                    set_number=number,
                    reps=_whole(values[2], "reps", line),
                    load_kg=_number(values[3], "load_kg", line),
                    rpe=_number(values[4], "rpe", line),
                    rir=_whole(values[5], "rir", line),
                    target_reps=_whole(values[6], "target_reps", line),
                    target_load_kg=_number(values[7], "target_load_kg", line),
                    target_rpe=_number(values[8], "target_rpe", line),
                )
            )
    except csv.Error:
        raise TemplateError(f"line {reader.line_num}: not a CSV row") from None
    if not rows:
        raise TemplateError("the file has no sets")
    return rows


def _protected(text: str) -> str:
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _cell(value: float | int | None) -> str:
    """A number as a number: no exponent, at most 6 decimals (the values are bounded)."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def export(rows: Iterable[SetRow]) -> str:
    """The rows in the template, formula-like text protected, numbers unchanged."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    for row in rows:
        numbers = (
            row.set_number,
            row.reps,
            row.load_kg,
            row.rpe,
            row.rir,
            row.target_reps,
            row.target_load_kg,
            row.target_rpe,
        )
        writer.writerow((_protected(row.exercise), *(_cell(value) for value in numbers)))
    return out.getvalue()
