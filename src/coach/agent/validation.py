"""Output validation rules 1.0.0: the numbers of an answer trace to the turn's sources
(FR-AGT-004; agent spec 4 step 6).

The sources of a turn are its tool results, the context block written by the application and
the athlete's own message. Every number written in the answer must be one of their numbers,
at the precision written (5.2 matches 5.23; 45 matches the 45:00 of a display form). Numbers
are read the way people write them in Spanish and English: "10.000" and "10,000" may be ten
thousand, "5,2" and "5.2" five point two; any reading that traces is enough. Ordered list
markers at the start of a line ("1." or "2)") and digits joined to letters on their left (the
2 of VO2max, a zone Z2, a week W43) are not numbers of the answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from decimal import Decimal, InvalidOperation
from typing import Final

VALIDATION_RULES_VERSION: Final = "1.0.0"
MAX_DECIMALS: Final = 4
_NUMBER: Final = re.compile(r"\d+(?:[.,]\d+)*")
# In an answer, digits joined to letters on their left belong to a name (VO2max, Z2, W43).
_ANSWER_NUMBER: Final = re.compile(r"(?<![^\W\d_])\d+(?:[.,]\d+)*")
_LIST_MARKER: Final = re.compile(r"^(\s*(?:[-*]\s+)?)\d{1,2}[.)](\s)", re.MULTILINE)
_THOUSANDS: Final = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_UUID: Final = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_COMMAND: Final = re.compile(r"`coach [^`]*`")
_SKIPPED: Final = frozenset(
    {
        "evidence",
        "version",
        "rulesVersion",
        "calculationVersion",
        "engineVersion",
        "promptVersion",
        "modelId",
        "activateWith",
        "reviewWith",
        "subject",
        "affectedWorkouts",
    }
)
_SKIPPED_ENDINGS: Final = ("Id", "Ids", "At")


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def readings(token: str) -> set[Decimal]:
    """The values a written number may stand for."""
    found: set[Decimal] = set()
    separators = {char for char in token if char in ".,"}
    if not separators:
        value = _decimal(token)
        return {value} if value is not None else set()
    if _THOUSANDS.match(token) and len(separators) == 1:
        plain = _decimal(token.replace(".", "").replace(",", ""))
        if plain is not None:
            found.add(plain)
    last = max(token.rfind("."), token.rfind(","))
    whole = token[:last].replace(".", "").replace(",", "")
    decimal = _decimal(f"{whole}.{token[last + 1 :]}")
    if decimal is not None:
        found.add(decimal)
    return found


def _decimals(token: str) -> int:
    last = max(token.rfind("."), token.rfind(","))
    return 0 if last < 0 else len(token) - last - 1


def numbers_in(text: str) -> Iterator[str]:
    """The numbers written in a text, list markers left out."""
    yield from _ANSWER_NUMBER.findall(_LIST_MARKER.sub(r"\1\2", text))


class Sources:
    """The numbers an answer may use, at every precision up to ``MAX_DECIMALS``."""

    def __init__(self, texts: Iterable[str]) -> None:
        values: set[Decimal] = set()
        for text in texts:
            for token in _NUMBER.findall(text):
                values |= readings(token)
        self._rounded = [
            {value.quantize(Decimal(1).scaleb(-places)) for value in values}
            for places in range(MAX_DECIMALS + 1)
        ]

    def traces(self, token: str) -> bool:
        places = min(_decimals(token), MAX_DECIMALS)
        exponent = Decimal(1).scaleb(-places)
        candidates = readings(token)
        for value in candidates:
            if value.quantize(exponent) in self._rounded[places]:
                return True
            # "10.000" read as ten thousand is an integer
            if value == value.to_integral_value() and value in self._rounded[0]:
                return True
        return False


def untraced_numbers(answer: str, sources: Iterable[str]) -> tuple[str, ...]:
    """The numbers of the answer that no source has, in order, once each. Identifiers and
    the commands the tools give (`coach plan activate <id> 2 --revision 3`) are not numbers
    of the answer."""
    known = Sources(sources)
    text = _UUID.sub(" ", _COMMAND.sub(" ", answer))
    missing = [token for token in numbers_in(text) if not known.traces(token)]
    return tuple(dict.fromkeys(missing))


def json_sources(value: object) -> Iterator[str]:
    """The texts of a tool result whose numbers an answer may use: values, display forms,
    dates and data text; identifiers, evidence, instants, versions and commands are left
    out, so their digits never pass as data."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            if name in _SKIPPED or name.endswith(_SKIPPED_ENDINGS):
                continue
            yield from json_sources(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from json_sources(item)
    elif isinstance(value, str):
        yield value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield str(value)
