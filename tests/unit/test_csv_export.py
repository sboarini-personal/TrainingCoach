"""AT-CSV-001: exported CSV neutralises formula-like text; numbers stay numbers (SEC-INJ-004)."""

import csv
import io
from pathlib import Path

import pytest

from coach.application.csv_export import cell, export_activities, neutralise
from coach.domain.activity.observation import SourceKind
from coach.domain.ingestion.templates import ACTIVITY_TEMPLATE
from coach.domain.raw.source import DetectedType
from coach.integrations.files.csv_parser import parse_csv
from coach.integrations.files.normalise import normalise
from coach.integrations.files.output import decode_response
from coach.integrations.files.parsing import ParserLimits
from samples import NOW, sample_activity, sample_observation, tennis_values

LIMITS = ParserLimits(records=1000, activities=500, xml_depth=64, xml_elements=1000)


@pytest.mark.req("SEC-INJ-004")
@pytest.mark.acceptance("AT-CSV-001")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("=SUM(A1:A9)", "'=SUM(A1:A9)"),
        ("+cmd|' /C calc'!A0", "'+cmd|' /C calc'!A0"),
        ("-2+3", "'-2+3"),
        ("@SUM(1)", "'@SUM(1)"),
        ("\tleading tab", "'\tleading tab"),
        ("\rleading return", "'\rleading return"),
        ("Morning run", "Morning run"),
        ("a=b", "a=b"),
    ],
)
def test_formula_like_text_is_quoted(text: str, expected: str) -> None:
    assert neutralise(text) == expected
    assert cell(text) == expected


@pytest.mark.req("SEC-INJ-004")
@pytest.mark.acceptance("AT-CSV-001")
def test_numbers_are_written_unchanged() -> None:
    assert (cell(-5.2), cell(10050.5), cell(148), cell(None)) == ("-5.2", "10050.5", "148", "")


@pytest.mark.req("SEC-INJ-004", "INT-FIL-001")
@pytest.mark.acceptance("AT-CSV-001")
def test_export_uses_the_template_and_can_be_imported_again(tmp_path: Path) -> None:
    entry = sample_observation(
        SourceKind.ATHLETE_ENTRY,
        values=tennis_values(
            **{
                "timing.utcOffset": "-03:00",
                "subjective.rpe": 7.0,
                "metadata.name": '=HYPERLINK("http://example.invalid")',
                "subjective.comments": "@coach rest tomorrow",
            }
        ),
    )
    exported = export_activities([sample_activity(), sample_activity(entry)])
    rows = list(csv.reader(io.StringIO(exported)))
    assert rows[0] == [column.name for column in ACTIVITY_TEMPLATE.columns]
    tennis = dict(zip(rows[0], rows[2], strict=True))
    assert tennis["name"] == '\'=HYPERLINK("http://example.invalid")'
    assert tennis["comments"] == "'@coach rest tomorrow"
    assert tennis["utc_offset"] == "'-03:00"
    assert (tennis["elapsed_seconds"], tennis["rpe"], tennis["sport"]) == (
        "5400.0",
        "7.0",
        "TENNIS",
    )
    path = tmp_path / "export.csv"
    path.write_bytes(exported.encode("utf-8"))
    parsed = decode_response(
        {"ok": True, "output": parse_csv(path, LIMITS)}, DetectedType.CSV, max_sessions=10
    )
    run, again = normalise(parsed, received_at=NOW)
    assert again.values["metadata.name"] == '=HYPERLINK("http://example.invalid")'
    assert again.values["subjective.comments"] == "@coach rest tomorrow"
    assert (again.values["timing.utcOffset"], again.values["subjective.rpe"]) == ("-03:00", 7.0)
    assert run.values["distance.meters"] == 10050.5
