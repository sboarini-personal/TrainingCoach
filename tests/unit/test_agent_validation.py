"""Output validation rules 1.0.0: an answer's numbers trace to the turn's sources
(FR-AGT-004; agent spec 4 step 6). Synthetic data only."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

import pytest

from coach.agent.tools.base import clock, quantity
from coach.agent.validation import json_sources, numbers_in, readings, untraced_numbers
from coach.domain.common.evidence import EvidenceKind, EvidenceRef

REF = EvidenceRef(EvidenceKind.ACTIVITY, UUID(int=1))
RESULT = json.dumps(
    {
        "distance": quantity(10234.0, "m", REF),
        "duration": quantity(2700.0, "s", REF),
        "pace": quantity(263.8, "s/km", REF),
        "rpe": quantity(7.0, "RPE 0-10", REF),
        "share": quantity(0.853, "share 0-1", REF, digits=2),
        "averageHr": quantity(151.0, "bpm", REF),
    }
)


@pytest.mark.req("FR-AGT-004")
@pytest.mark.parametrize(
    "answer",
    [
        "Corriste 10.23 km en 45:00, a 4:24/km, con FC media de 151 bpm.",
        "Fueron 10,23 km y un RPE de 7.",  # Spanish decimal comma
        "Distancia: 10234 m; el 85% del tiempo en zona.",
        "1. Mantené el ritmo.\n2. Dormí bien.\n- 3) Hidratación.",  # list markers
        "Recorriste 10 km.",  # 10.234 written at the precision chosen
        "No tengo datos de sueño.",
    ],
)
def test_numbers_from_the_tool_results_trace(answer: str) -> None:
    assert untraced_numbers(answer, [RESULT]) == ()


@pytest.mark.req("FR-AGT-004")
def test_invented_numbers_are_named_once_each() -> None:
    answer = "Tu VO2max es 55 y tu umbral 172 bpm; con 55 de VO2max podés correr 42 km."
    assert untraced_numbers(answer, [RESULT]) == ("55", "172", "42")
    assert untraced_numbers(answer, [RESULT, "estimé 55, 172 y 42"]) == ()


def test_written_numbers_are_read_both_ways() -> None:
    assert readings("10.000") == {10000, 10}
    assert readings("5,2") == {Decimal("5.2")}
    assert list(numbers_in("1. Hola 4:30 y 2026-10-25")) == ["4", "30", "2026", "10", "25"]
    assert clock(2700) == "45:00"
    assert clock(3725) == "1:02:05"


@pytest.mark.req("FR-AGT-004")
def test_identifiers_instants_and_commands_are_not_sources() -> None:
    payload = {
        "activityId": "01a19859-0000-7000-8000-000000000072",
        "importedAt": "2026-10-25T10:59:00+00:00",
        "evidence": [{"kind": "ACTIVITY", "id": "01a19859-0000-7000-8000-000000000088"}],
        "activateWith": "coach plan activate 01a19859-0000-7000-8000-000000000001 2 --revision 3",
        "distance": quantity(5000.0, "m", REF),
    }
    sources = list(json_sources(payload))
    assert untraced_numbers("Corriste 5 km.", sources) == ()
    assert untraced_numbers("Tu FC en reposo es 59 y tu VO2 72.", sources) == ("59", "72")
    command = (
        "Activalo con `coach plan activate 01a19859-0000-7000-8000-000000000001 2 --revision 3`."
    )
    assert untraced_numbers(command, sources) == ()
