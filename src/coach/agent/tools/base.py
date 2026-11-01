"""The coach's tools: typed, narrow, bound to the session's athlete (FR-AGT-001, FR-AGT-002,
SEC-AUTHZ-002, SEC-INJ-001; tools contract).

A tool has a name, a kind, a description for the model and the JSON Schema of its input
(unknown fields rejected). No input ever names an athlete: the ``Toolbox`` runs every call in
the owner scope of the authenticated session, so a request for another athlete's data has no
parameter to express it. A call returns structured JSON:

- every measured or declared number is a quantity with its unit and evidence;
- every external text (activity names, notes, descriptions, event names) is wrapped as
  ``{"untrusted_text": ...}``, control characters removed and at most 500 characters;
- missing data stays ``null``, and what is missing is said.

Errors become the typed codes of the contract; their details carry codes and identifiers,
never secrets or free text. Each call is emitted as an observability event (tool name,
outcome and duration).
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as clock_time
from enum import Enum, StrEnum
from typing import Any, Final
from uuid import UUID

from jsonschema import Draft202012Validator

from coach.application.consent import ConsentRequiredError
from coach.application.errors import (
    ConcurrencyConflictError,
    InvalidOperationError,
    NotFoundError,
    OwnershipError,
    RateLimitedError,
    TimezoneRequiredError,
)
from coach.application.events import EventName
from coach.application.llm import ToolSpec
from coach.application.ownership import OwnerScope
from coach.application.policy_gate import ConfirmationRequiredError, PolicyDeniedError
from coach.application.ports import EventSink
from coach.domain.agent import ToolCallRecord, ToolOutcome
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef

TOOLS_VERSION: Final = "1.0.0"
UNTRUSTED_LIMIT: Final = 500
TRUNCATED: Final = " [truncated]"
MAX_DAYS: Final = 92  # the longest period a tool reads at once
FORBIDDEN_PARAMETERS: Final = frozenset(
    {
        "athleteId",
        "athlete_id",
        "ownerId",
        "url",
        "uri",
        "path",
        "file",
        "filename",
        "sql",
        "query",
        "command",
        "shell",
        "code",
        "secret",
        "token",
        "apiKey",
        "password",
    }
)
_CONTROL: Final = dict.fromkeys((*range(0, 9), 11, 12, *range(14, 32), 127))
_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class ToolKind(StrEnum):
    READ = "READ"
    WRITE_INTERNAL = "WRITE_INTERNAL"
    PROPOSE = "PROPOSE"
    EXECUTE_EXTERNAL = "EXECUTE_EXTERNAL"


class ToolError(Exception):
    """A typed error of the tools contract, raised by a tool. Details are codes."""

    def __init__(self, outcome: ToolOutcome, **details: Any) -> None:
        super().__init__(outcome.value)
        self.outcome = outcome
        self.details = details


@dataclass(frozen=True, slots=True)
class ToolContext:
    """What a call runs with: the session's owner scope and the athlete's today (``None``
    while the timezone is not set: the training day cannot be known)."""

    scope: OwnerScope
    today: date | None

    def day(self) -> date:
        if self.today is None:
            raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="TIMEZONE_REQUIRED")
        return self.today


type Handler = Callable[[ToolContext, Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    kind: ToolKind
    description: str
    input_schema: Mapping[str, Any]
    handler: Handler

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.input_schema)


@dataclass(frozen=True, slots=True)
class ToolCallOutput:
    """One call: its result block for the model, its record for the decision audit, the
    evidence its numbers rest on and its payload (for output validation)."""

    block: Mapping[str, Any]
    record: ToolCallRecord
    evidence: tuple[EvidenceRef, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- output helpers


def untrusted(text: str | None) -> dict[str, str] | None:
    """External text as data, never instructions (SEC-INJ-001)."""
    if text is None:
        return None
    cleaned = text.translate(_CONTROL).strip()
    if len(cleaned) > UNTRUSTED_LIMIT:
        cleaned = cleaned[:UNTRUSTED_LIMIT] + TRUNCATED
    return {"untrusted_text": cleaned}


def evidence(*refs: EvidenceRef) -> list[dict[str, str]]:
    found = []
    for item in refs:
        entry = {"kind": item.kind.value, "id": str(item.ref_id)}
        if item.field is not None:
            entry["field"] = item.field
        found.append(entry)
    return found


def quantity(value: float | None, unit: str, *refs: EvidenceRef, digits: int = 1) -> dict[str, Any]:
    """A number with its unit and evidence; ``None`` stays unknown. Durations, distances,
    paces, speeds and shares also get the form people read (computed here, so the coach
    never converts a unit)."""
    shown: float | None = value
    if isinstance(value, float):
        shown = round(value, digits)
        if shown == int(shown):
            shown = int(shown)
    found: dict[str, Any] = {"value": shown, "unit": unit, "evidence": evidence(*refs)}
    if value is not None:
        display = _display(float(value), unit)
        if display is not None:
            found["display"] = display
    return found


def clock(seconds: float) -> str:
    """``h:mm:ss`` or ``m:ss``."""
    whole = round(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _display(value: float, unit: str) -> str | None:
    match unit:
        case "s" if value >= 60:
            return clock(value)
        case "m" if value >= 1000:
            return f"{value / 1000:.2f}".rstrip("0").rstrip(".") + " km"
        case "s/km":
            return f"{clock(value)}/km"
        case "s/100m":
            return f"{clock(value)}/100m"
        case "m/s":
            return f"{value * 3.6:.1f} km/h"
        case "share 0-1" | "ratio":
            return f"{value * 100:.0f}%"
    return None


def code(value: Enum | None) -> str | None:
    """An enumeration as its code (a weekday by its name)."""
    if value is None:
        return None
    return value.name if isinstance(value, int) else str(value.value)


def codes(values: Sequence[Enum] | frozenset[Any] | None) -> list[str] | None:
    if values is None:
        return None
    return sorted(str(code(item)) for item in values)


def code_or_text(value: str | None) -> str | dict[str, str] | None:
    """A code as it is; any other text as untrusted text."""
    if value is None or _CODE.match(value):
        return value
    return untrusted(value)


def instant(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


# Units of numbers by the end of their field name; the first match wins.
_UNITS: Final = (
    ("pace_100m_seconds", "s/100m"),
    ("_seconds", "s"),
    ("_minutes", "min"),
    ("start_minute", "min after midnight"),
    ("_hours_per_week", "h/week"),
    ("days_per_week", "days/week"),
    ("sessions_per_week", "sessions/week"),
    ("years_experience", "years"),
    ("_percent_max_hr", "% of max HR"),
    ("_meters", "m"),
    ("_km", "km"),
    ("_m", "m"),
    ("_kg", "kg"),
    ("_cm", "cm"),
    ("watts", "W"),
    ("_rpe", "RPE 0-10"),
    ("_rir", "reps in reserve"),
    ("rpe_load", "AU (RPE x min)"),
    ("reps", "reps"),
    ("reps_min", "reps"),
    ("reps_max", "reps"),
    ("sets", "sets"),
    ("_bpm", "bpm"),
    ("_days", "days"),
)
UNITLESS: Final = "1"


def unit_of(name: str) -> str:
    return next((unit for suffix, unit in _UNITS if name.endswith(suffix)), UNITLESS)


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def to_json(
    value: object,
    ref: EvidenceRef,
    *,
    name: str = "",
    plain: frozenset[str] = frozenset(),
    trusted: bool = False,
) -> Any:
    """A stored value as tool JSON: numbers become quantities (unit from the field name,
    evidence ``ref``), codes stay codes, and text is untrusted text unless the value was
    written in code (``trusted``) or is a catalogue code named in ``plain``."""

    def again(item: object, field: str = name) -> Any:
        return to_json(item, ref, name=field, plain=plain, trusted=trusted)

    match value:
        case None | bool():
            return value
        case int() | float():
            return quantity(float(value), unit_of(name), ref)
        case Enum():
            return code(value)
        case str():
            return value if trusted or name in plain else untrusted(value)
        case datetime():
            return value.isoformat()
        case date():
            return value.isoformat()
        case clock_time():
            return value.strftime("%H:%M")
        case UUID():
            return str(value)
        case frozenset() | set():
            return sorted((again(item) for item in value), key=str)
        case tuple() | list():
            if value and all(
                isinstance(item, int | float) and not isinstance(item, bool) for item in value
            ):
                return {"values": list(value), "unit": unit_of(name), "evidence": evidence(ref)}
            return [again(item) for item in value]
        case Mapping():
            return {
                str(code(key) if isinstance(key, Enum) else key): again(item)
                for key, item in value.items()
            }
        case _ if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {
                camel(item.name): again(getattr(value, item.name), item.name)
                for item in dataclasses.fields(value)
            }
    raise TypeError(f"no tool form for {type(value).__name__}")


DATE: Final = {"type": "string", "format": "date", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
ID: Final = {"type": "string", "format": "uuid", "pattern": "^[0-9a-fA-F-]{36}$"}
RANGE_PROPERTIES: Final = {
    "from": {**DATE, "description": "First day, YYYY-MM-DD (default: see the tool)."},
    "to": {**DATE, "description": "Last day, YYYY-MM-DD (default: today)."},
}


def schema(properties: Mapping[str, Any] | None = None, *required: str) -> dict[str, Any]:
    """An object schema that rejects unknown fields."""
    return {
        "type": "object",
        "properties": dict(properties or {}),
        "required": list(required),
        "additionalProperties": False,
    }


def period(
    arguments: Mapping[str, Any], context: ToolContext, *, default_days: int
) -> tuple[date, date]:
    """The ``from``/``to`` days of an input (default: the last ``default_days`` days to
    today), at most ``MAX_DAYS`` long."""
    end = date.fromisoformat(arguments["to"]) if "to" in arguments else context.day()
    start = (
        date.fromisoformat(arguments["from"])
        if "from" in arguments
        else date.fromordinal(end.toordinal() - default_days + 1)
    )
    if start > end:
        raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="FROM_AFTER_TO")
    if (end - start).days + 1 > MAX_DAYS:
        raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="PERIOD_TOO_LONG", maxDays=MAX_DAYS)
    return start, end


# --------------------------------------------------------------------------- the toolbox


class Toolbox:
    """The tools of a session, fixed in code (SEC-AUTHZ-002): data can never add one."""

    def __init__(self, tools: Sequence[Tool], *, events: EventSink) -> None:
        names = [tool.name for tool in tools]
        if len(set(names)) != len(names):
            raise ValueError("tool names are unique")
        self._tools = {tool.name: tool for tool in tools}
        self._validators = {
            tool.name: Draft202012Validator(
                tool.input_schema, format_checker=Draft202012Validator.FORMAT_CHECKER
            )
            for tool in tools
        }
        self._events = events

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(self._tools.values())

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec() for tool in self._tools.values())

    def run(
        self, tool_use_id: str, name: str, arguments: object, context: ToolContext
    ) -> ToolCallOutput:
        started = time.perf_counter()
        tool = self._tools.get(name)
        recorded = name if tool is not None else "unknownTool"
        outcome, payload = self._call(tool, arguments, context)
        self._events.emit(
            EventName.AGENT_TOOL_CALLED,
            {
                "tool": recorded,
                "outcome": outcome.value,
                "duration_ms": str(round((time.perf_counter() - started) * 1000)),
            },
        )
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if outcome is not ToolOutcome.OK:
            block["is_error"] = True
        return ToolCallOutput(
            block=block,
            record=ToolCallRecord(recorded, outcome),
            evidence=tuple(_evidence_in(payload)),
            payload=json.loads(content),  # exactly what the model receives
        )

    def _call(
        self, tool: Tool | None, arguments: object, context: ToolContext
    ) -> tuple[ToolOutcome, dict[str, Any]]:
        try:
            if tool is None:
                raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="UNKNOWN_TOOL")
            if not isinstance(arguments, Mapping) or any(
                True for _ in self._validators[tool.name].iter_errors(arguments)
            ):
                raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="INVALID_INPUT")
            data = tool.handler(context, arguments)
        except ToolError as error:
            return error.outcome, _error(error.outcome, error.details)
        except ConsentRequiredError as error:
            missing = [item.value for item in error.missing]
            return ToolOutcome.CONSENT_REQUIRED, _error(
                ToolOutcome.CONSENT_REQUIRED, {"consentType": missing}
            )
        except PolicyDeniedError as error:
            reasons = [item.code for item in error.decision.reasons]
            return ToolOutcome.POLICY_DENIED, _error(
                ToolOutcome.POLICY_DENIED, {"reasons": reasons}
            )
        except ConfirmationRequiredError:
            return ToolOutcome.CONFIRMATION_REQUIRED, _error(ToolOutcome.CONFIRMATION_REQUIRED, {})
        except (NotFoundError, OwnershipError):
            return ToolOutcome.NOT_FOUND, _error(ToolOutcome.NOT_FOUND, {})
        except ConcurrencyConflictError:
            return ToolOutcome.CONCURRENCY_CONFLICT, _error(ToolOutcome.CONCURRENCY_CONFLICT, {})
        except RateLimitedError:
            return ToolOutcome.RATE_LIMITED, _error(ToolOutcome.RATE_LIMITED, {})
        except TimezoneRequiredError:
            return ToolOutcome.VALIDATION_ERROR, _error(
                ToolOutcome.VALIDATION_ERROR, {"reason": "TIMEZONE_REQUIRED"}
            )
        except (DomainValidationError, InvalidOperationError, ValueError):
            return ToolOutcome.VALIDATION_ERROR, _error(
                ToolOutcome.VALIDATION_ERROR, {"reason": "INVALID_REQUEST"}
            )
        return ToolOutcome.OK, {"ok": True, "data": dict(data)}


def _error(outcome: ToolOutcome, details: Mapping[str, Any]) -> dict[str, Any]:
    return {"ok": False, "error": {"code": outcome.value, **details}}


def _evidence_in(value: object) -> Iterator[EvidenceRef]:
    """The evidence references of a payload, in order, once each."""
    seen: set[tuple[str, str, str | None]] = set()
    for entry in _walk_evidence(value):
        key = (entry["kind"], entry["id"], entry.get("field"))
        if key not in seen:
            seen.add(key)
            yield EvidenceRef(EvidenceKind(entry["kind"]), UUID(entry["id"]), entry.get("field"))


def _walk_evidence(value: object) -> Iterator[Mapping[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "evidence" and isinstance(item, list):
                yield from (entry for entry in item if isinstance(entry, Mapping))
            else:
                yield from _walk_evidence(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_evidence(item)
