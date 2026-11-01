"""The toolbox: input validation, the session's scope, untrusted text, quantities and typed
errors (tools contract sections 1 and 2; FR-AGT-001, FR-AGT-002, SEC-INJ-001). Synthetic
data only."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

import pytest

from coach.agent.tools.base import (
    TRUNCATED,
    UNTRUSTED_LIMIT,
    Tool,
    Toolbox,
    ToolContext,
    ToolError,
    ToolKind,
    quantity,
    schema,
    to_json,
    untrusted,
)
from coach.application.consent import ConsentRequiredError
from coach.application.errors import (
    ConcurrencyConflictError,
    NotFoundError,
    OwnershipError,
    TimezoneRequiredError,
)
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.domain.agent import ToolOutcome
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.consent.ledger import ConsentType
from coach.infrastructure.memory import RecordingEventSink

SCOPE = OwnerScope(UUID(int=7))
TODAY = date(2026, 10, 25)
REF = EvidenceRef(EvidenceKind.ACTIVITY, UUID(int=9), field="distance.meters")


def _toolbox(
    handler: Any, input_schema: Mapping[str, Any] | None = None
) -> tuple[Toolbox, RecordingEventSink]:
    events = RecordingEventSink()
    tool = Tool("probe", ToolKind.READ, "A probe.", input_schema or schema(), handler)
    return Toolbox([tool], events=events), events


def _run(toolbox: Toolbox, arguments: object = None, name: str = "probe") -> Any:
    return toolbox.run(
        "toolu_1", name, {} if arguments is None else arguments, ToolContext(SCOPE, TODAY)
    )


@pytest.mark.req("SEC-INJ-001")
def test_untrusted_text_is_cleaned_capped_and_marked() -> None:
    assert untrusted(None) is None
    assert untrusted(" Morning\x00 run\x07 ") == {"untrusted_text": "Morning run"}
    long = untrusted("x" * (UNTRUSTED_LIMIT + 20))
    assert long is not None
    assert long["untrusted_text"] == "x" * UNTRUSTED_LIMIT + TRUNCATED
    assert untrusted("line one\nline two") == {"untrusted_text": "line one\nline two"}


@pytest.mark.req("FR-AGT-001", "NFR-TRC-001")
def test_numbers_carry_unit_and_evidence_and_unknown_stays_null() -> None:
    assert quantity(1234.56, "m", REF) == {
        "value": 1234.6,
        "unit": "m",
        "evidence": [{"kind": "ACTIVITY", "id": str(UUID(int=9)), "field": "distance.meters"}],
    }
    assert quantity(42.0, "bpm", REF)["value"] == 42
    assert quantity(None, "bpm", REF)["value"] is None
    assert to_json(3.0, REF, name="weekly_distance_km")["unit"] == "km"
    assert to_json("my knee", REF) == {"untrusted_text": "my knee"}
    assert (
        to_json("back_squat", REF, name="exercise_id", plain=frozenset({"exercise_id"}))
        == "back_squat"
    )
    assert to_json("Easy run", REF, trusted=True) == "Easy run"
    assert to_json((120, 140, 160), REF, name="boundaries_bpm")["values"] == [120, 140, 160]


@pytest.mark.req("FR-AGT-001", "SEC-AUTHZ-003")
def test_input_is_validated_and_unknown_fields_are_rejected() -> None:
    calls: list[Mapping[str, Any]] = []

    def handler(_context: ToolContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        calls.append(arguments)
        return {"echo": dict(arguments)}

    toolbox, _events = _toolbox(handler, schema({"limit": {"type": "integer", "maximum": 5}}))
    invalid = {"ok": False, "error": {"code": "VALIDATION_ERROR", "reason": "INVALID_INPUT"}}
    for arguments in ({"athleteId": "x"}, {"limit": 9}, {"limit": "2"}, ["limit"]):
        output = _run(toolbox, arguments)
        assert output.payload == invalid
        assert output.block["is_error"] is True
    assert calls == []  # the handler never ran
    unknown = _run(toolbox, name="executeSQL")
    assert unknown.payload["error"] == {"code": "VALIDATION_ERROR", "reason": "UNKNOWN_TOOL"}
    assert unknown.record.name == "unknownTool"  # the model's text is not recorded
    assert _run(toolbox, {"limit": 3}).payload == {"ok": True, "data": {"echo": {"limit": 3}}}


@pytest.mark.req("FR-AGT-002")
def test_every_call_runs_in_the_session_scope() -> None:
    seen: list[ToolContext] = []

    def handler(context: ToolContext, _arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        seen.append(context)
        return {}

    toolbox, _events = _toolbox(handler)
    _run(toolbox)
    assert [context.scope for context in seen] == [SCOPE]


def _raising(error: Exception) -> Any:
    def handler(_context: ToolContext, _arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        raise error

    return handler


@pytest.mark.req("FR-AGT-001")
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ConsentRequiredError((ConsentType.ACTIVITIES,)),
            {"code": "CONSENT_REQUIRED", "consentType": ["ACTIVITIES"]},
        ),
        (NotFoundError(), {"code": "NOT_FOUND"}),
        (OwnershipError("someone else's"), {"code": "NOT_FOUND"}),
        (ConcurrencyConflictError("changed"), {"code": "CONCURRENCY_CONFLICT"}),
        (TimezoneRequiredError(), {"code": "VALIDATION_ERROR", "reason": "TIMEZONE_REQUIRED"}),
        (
            DomainValidationError("bad day"),
            {"code": "VALIDATION_ERROR", "reason": "INVALID_REQUEST"},
        ),
        (
            ToolError(ToolOutcome.CAPABILITY_UNAVAILABLE, capability="TRAINING_PUBLISH"),
            {"code": "CAPABILITY_UNAVAILABLE", "capability": "TRAINING_PUBLISH"},
        ),
    ],
)
def test_errors_become_the_typed_codes_without_their_messages(
    error: Exception, expected: dict[str, Any]
) -> None:
    toolbox, events = _toolbox(_raising(error))
    output = _run(toolbox)
    assert output.payload == {"ok": False, "error": expected}
    assert output.record.outcome.value == expected["code"]
    if not isinstance(error, ToolError):  # a ToolError is its code
        assert str(error) not in output.block["content"]
    ((name, fields),) = events.events
    assert name is EventName.AGENT_TOOL_CALLED
    assert (fields["tool"], fields["outcome"]) == ("probe", expected["code"])
    assert fields["duration_ms"].isdigit()


@pytest.mark.req("NFR-TRC-001", "FR-AGT-003")
def test_the_evidence_of_a_result_is_collected_once_for_the_decision_audit() -> None:
    other = EvidenceRef(EvidenceKind.GOAL, UUID(int=3))

    def handler(_context: ToolContext, _arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"a": quantity(1.0, "m", REF), "b": [quantity(2.0, "m", REF, other)]}

    toolbox, _events = _toolbox(handler)
    output = _run(toolbox)
    assert output.evidence == (
        EvidenceRef(EvidenceKind.ACTIVITY, UUID(int=9), "distance.meters"),
        other,
    )
    assert json.loads(output.block["content"]) == output.payload
    assert "is_error" not in output.block


def test_without_a_timezone_a_tool_that_needs_today_says_so() -> None:
    def handler(context: ToolContext, _arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"day": context.day().isoformat()}

    toolbox, _events = _toolbox(handler)
    output = toolbox.run("toolu_1", "probe", {}, ToolContext(SCOPE, None))
    assert output.payload["error"] == {"code": "VALIDATION_ERROR", "reason": "TIMEZONE_REQUIRED"}


def test_tool_names_are_unique() -> None:
    tool = Tool("probe", ToolKind.READ, "A probe.", schema(), _raising(NotFoundError()))
    with pytest.raises(ValueError, match="unique"):
        Toolbox([tool, tool], events=RecordingEventSink())
