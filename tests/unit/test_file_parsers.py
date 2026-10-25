"""Sandboxed parsers, run in-process: FIT, TCX, GPX and CSV to the parser-output contract."""

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from coach.integrations.files.csv_parser import parse_csv
from coach.integrations.files.fit_parser import parse_fit
from coach.integrations.files.gpx_parser import haversine, parse_gpx
from coach.integrations.files.parsing import ParserLimits, ParserReason, ParserRejection
from coach.integrations.files.tcx_parser import parse_tcx
from coach.integrations.files.worker import handle
from file_samples import START, csv_file, fit_file, fit_session, gpx_file, tcx_file
from schemas import validation_errors

LIMITS = ParserLimits(records=100_000, activities=500, xml_depth=64, xml_elements=100_000)
type Parser = Callable[[Path, ParserLimits], dict[str, Any]]


def _parse(
    tmp_path: Path, parser: Parser, data: bytes, limits: ParserLimits = LIMITS
) -> dict[str, Any]:
    path = tmp_path / "quarantined"
    path.write_bytes(data)
    output = parser(path, limits)
    assert validation_errors("parser-output", output) == []
    return output


def _rejection(
    tmp_path: Path, parser: Parser, data: bytes, limits: ParserLimits = LIMITS
) -> ParserRejection:
    path = tmp_path / "quarantined"
    path.write_bytes(data)
    with pytest.raises(ParserRejection) as raised:
        parser(path, limits)
    return raised.value


# --------------------------------------------------------------------------- FIT


@pytest.mark.req("INT-FIL-001")
def test_fit_session_summary_offset_and_samples(tmp_path: Path) -> None:
    output = _parse(tmp_path, parse_fit, fit_file())
    assert (output["format"], output["fileType"], output["recordCount"]) == ("FIT", "activity", 604)
    (session,) = output["sessions"]
    assert (session["sport"], session["subSport"], session["manufacturer"]) == (
        "running",
        "road",
        "garmin",
    )
    assert (session["startTime"], session["utcOffsetMinutes"]) == ("2026-10-20T21:00:00Z", -180)
    summary = session["summary"]
    assert summary["elapsedSeconds"] == 600.0
    assert summary["distanceMeters"] == 1800.0
    assert summary["avgHeartRate"] == 152.0
    assert summary["avgCadence"] == 85.0
    assert summary["trainingEffectAerobic"] == pytest.approx(3.1)
    assert session["sourcePaths"]["distanceMeters"] == "session[0].total_distance"
    samples = session["samples"]
    assert len(samples["offsetSeconds"]) == 601
    assert samples["distanceMeters"][:3] == [0.0, 3.0, 6.0]
    assert "altitudeMeters" not in samples


@pytest.mark.req("INT-FIL-001")
def test_fit_multisport_sessions_get_their_own_records(tmp_path: Path) -> None:
    ride = fit_session(
        start=START + timedelta(minutes=15), seconds=300, sport="cycling", sub_sport="road"
    )
    output = _parse(tmp_path, parse_fit, fit_file([fit_session(), ride]))
    first, second = output["sessions"]
    assert (first["index"], second["index"], second["sport"]) == (0, 1, "cycling")
    assert len(first["samples"]["offsetSeconds"]) == 601
    assert len(second["samples"]["offsetSeconds"]) == 301
    assert second["samples"]["offsetSeconds"][0] == 0.0


@pytest.mark.req("INT-FIL-001", "FR-ACT-003")
def test_fit_offset_is_unknown_without_a_local_timestamp(tmp_path: Path) -> None:
    (session,) = _parse(tmp_path, parse_fit, fit_file(utc_offset_hours=None))["sessions"]
    assert session["utcOffsetMinutes"] is None


@pytest.mark.req("SEC-UPL-001", "SEC-UPL-004", "INT-FIL-003")
@pytest.mark.acceptance("AT-UPL-001")
def test_fit_crc_and_record_limits(tmp_path: Path) -> None:
    corrupted = bytearray(fit_file())
    corrupted[200] ^= 0xFF
    rejection = _rejection(tmp_path, parse_fit, bytes(corrupted))
    assert (rejection.reason, rejection.detail) == (ParserReason.PARSE_FAILED, "CRC_ERROR")
    small = ParserLimits(records=100, activities=500, xml_depth=64, xml_elements=1000)
    rejection = _rejection(tmp_path, parse_fit, fit_file(), small)
    assert (rejection.reason, rejection.detail) == (ParserReason.LIMIT_EXCEEDED, "RECORDS")


@pytest.mark.req("INT-FIL-003")
def test_activity_limit_applies_to_sessions(tmp_path: Path) -> None:
    sessions = [fit_session(start=START + timedelta(hours=hour), seconds=10) for hour in range(3)]
    two = ParserLimits(records=100_000, activities=2, xml_depth=64, xml_elements=1000)
    rejection = _rejection(tmp_path, parse_fit, fit_file(sessions), two)
    assert (rejection.reason, rejection.detail) == (ParserReason.LIMIT_EXCEEDED, "ACTIVITIES")


# --------------------------------------------------------------------------- TCX


@pytest.mark.req("INT-FIL-001")
def test_tcx_laps_are_combined_by_the_documented_rules(tmp_path: Path) -> None:
    output = _parse(tmp_path, parse_tcx, tcx_file(notes="Easy run"))
    (session,) = output["sessions"]
    summary = session["summary"]
    assert session["sport"] == "Running"
    assert summary["elapsedSeconds"] == 600.0  # last start 21:05 + 300 s - first start 21:00
    assert summary["distanceMeters"] == 1900.0
    assert summary["avgHeartRate"] == 150.0  # (140 * 300 + 160 * 300) / 600
    assert summary["maxHeartRate"] == 170.0
    assert summary["maxSpeedMps"] == 4.0
    assert summary["avgSpeedMps"] == pytest.approx(1900 / 600)
    assert session["texts"] == {"description": "Easy run"}
    assert session["utcOffsetMinutes"] is None
    assert output["recordCount"] == 6
    assert session["samples"]["offsetSeconds"][3] == 300.0


@pytest.mark.req("INT-FIL-001")
def test_tcx_heart_rate_is_unknown_unless_every_lap_reports_it(tmp_path: Path) -> None:
    data = tcx_file().replace(b"<AverageHeartRateBpm><Value>160</Value></AverageHeartRateBpm>", b"")
    (session,) = _parse(tmp_path, parse_tcx, data)["sessions"]
    assert "avgHeartRate" not in session["summary"]


@pytest.mark.req("INT-FIL-001", "FR-ACT-003")
def test_tcx_offsets_and_naive_times(tmp_path: Path) -> None:
    laps = [
        {
            "start": "2026-10-20T18:00:00-03:00",
            "seconds": 60,
            "distance": 10,
            "avg": 1,
            "max": 2,
            "speed": 1,
        }
    ]
    (session,) = _parse(tmp_path, parse_tcx, tcx_file(laps=laps))["sessions"]
    assert (session["startTime"], session["utcOffsetMinutes"]) == ("2026-10-20T21:00:00Z", -180)
    naive = tcx_file().replace(
        b'StartTime="2026-10-20T21:00:00Z"', b'StartTime="2026-10-20T21:00:00"'
    )
    rejection = _rejection(tmp_path, parse_tcx, naive)
    assert (rejection.reason, rejection.detail) == (ParserReason.SEMANTIC_INVALID, "NAIVE_TIME")


@pytest.mark.req("INT-FIL-001", "SEC-UPL-001")
def test_tcx_requires_the_v2_namespace(tmp_path: Path) -> None:
    data = tcx_file().replace(b"TrainingCenterDatabase/v2", b"TrainingCenterDatabase/v9")
    assert _rejection(tmp_path, parse_tcx, data).detail == "UNSUPPORTED_NAMESPACE"


# --------------------------------------------------------------------------- GPX


@pytest.mark.req("INT-FIL-001")
def test_gpx_distance_heart_rate_and_samples(tmp_path: Path) -> None:
    output = _parse(tmp_path, parse_gpx, gpx_file())
    (session,) = output["sessions"]
    leg = haversine(-34.6, -58.4, -34.6, -58.3989)
    assert session["summary"]["distanceMeters"] == pytest.approx(
        leg + haversine(-34.6, -58.3989, -34.6, -58.3978)
    )
    assert session["summary"]["elapsedSeconds"] == 60.0
    assert session["summary"]["avgHeartRate"] == 145.0  # 140 and 150 held 30 s each
    assert session["summary"]["maxHeartRate"] == 160.0
    assert (session["sport"], session["texts"]) == ("running", {"name": "Evening run"})
    assert "latitude" not in str(session["samples"])
    assert session["samples"]["distanceMeters"][1] == pytest.approx(leg)


@pytest.mark.req("INT-FIL-001")
def test_haversine_matches_a_known_distance() -> None:
    # One degree of longitude on the equator: 2 * pi * R / 360.
    assert haversine(0, 0, 0, 1) == pytest.approx(111_195.08, abs=0.1)


@pytest.mark.req("INT-FIL-001")
def test_gpx_gap_between_segments_adds_no_distance(tmp_path: Path) -> None:
    first = [
        (-34.6, -58.4, "2026-10-20T21:00:00Z", 140),
        (-34.6, -58.3989, "2026-10-20T21:00:30Z", 140),
    ]
    second = [
        (-34.0, -58.0, "2026-10-20T21:10:00Z", 150),
        (-34.0, -57.9989, "2026-10-20T21:10:30Z", 150),
    ]
    (session,) = _parse(tmp_path, parse_gpx, gpx_file(segments=[first, second]))["sessions"]
    expected = haversine(-34.6, -58.4, -34.6, -58.3989) + haversine(-34.0, -58.0, -34.0, -57.9989)
    assert session["summary"]["distanceMeters"] == pytest.approx(expected)
    assert session["summary"]["avgHeartRate"] == 145.0


@pytest.mark.req("INT-FIL-001")
@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (gpx_file().replace(b"<time>2026-10-20T21:00:30Z</time>", b""), "MISSING_TIME"),
        (gpx_file().replace(b'lat="-34.6"', b'lat="-134.6"', 1), "INVALID_COORDINATE"),
        (gpx_file().replace(b"GPX/1/1", b"GPX/2/0"), "UNSUPPORTED_NAMESPACE"),
        (gpx_file(points=[]), "EMPTY_TRACK"),
    ],
)
def test_gpx_invalid_tracks(tmp_path: Path, data: bytes, detail: str) -> None:
    assert _rejection(tmp_path, parse_gpx, data).detail == detail


@pytest.mark.req("SEC-UPL-002", "INT-FIL-003")
def test_xml_depth_and_element_limits(tmp_path: Path) -> None:
    deep = (
        b'<gpx xmlns="http://www.topografix.com/GPX/1/1">' + b"<a>" * 70 + b"</a>" * 70 + b"</gpx>"
    )
    assert _rejection(tmp_path, parse_gpx, deep).detail == "XML_DEPTH"
    few = ParserLimits(records=1000, activities=10, xml_depth=64, xml_elements=10)
    assert _rejection(tmp_path, parse_gpx, gpx_file(), few).detail == "XML_ELEMENTS"
    malformed = b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><unclosed></gpx>'
    assert _rejection(tmp_path, parse_gpx, malformed).detail == "XML_MALFORMED"


# --------------------------------------------------------------------------- CSV


@pytest.mark.req("INT-FIL-001", "SEC-INJ-004")
def test_csv_rows_become_sessions_and_cells_stay_data(tmp_path: Path) -> None:
    rows = [
        {
            "start_time_utc": "2026-10-21T22:30:00Z",
            "utc_offset": "-03:00",
            "sport": "TENNIS",
            "subtype": "MATCH",
            "elapsed_seconds": "5400",
            "average_hr": "131",
            "rpe": "7",
            "name": '=HYPERLINK("http://x")',
        },
        {"start_time_utc": "2026-10-22T10:00:00Z", "sport": "STRENGTH", "elapsed_seconds": "3000"},
    ]
    output = _parse(tmp_path, parse_csv, csv_file(rows, bom=True))
    first, second = output["sessions"]
    assert (first["sport"], first["labels"], first["utcOffsetMinutes"]) == (
        "TENNIS",
        {"subtype": "MATCH"},
        -180,
    )
    assert first["summary"] == {"elapsedSeconds": 5400.0, "avgHeartRate": 131.0, "rpe": 7.0}
    assert first["texts"]["name"] == '=HYPERLINK("http://x")'
    assert second["summary"] == {"elapsedSeconds": 3000.0}
    assert output["recordCount"] == 2


@pytest.mark.req("INT-FIL-001")
@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (b"start_time_utc,sport\r\n", "CSV_TEMPLATE"),
        (
            csv_file(
                [
                    {
                        "start_time_utc": "2026-10-21T22:30:00Z",
                        "sport": "RUNNING",
                        "elapsed_seconds": "ten",
                    }
                ]
            ),
            "INVALID_NUMBER",
        ),
        (
            csv_file(
                [{"start_time_utc": "2026-10-21T22:30:00Z", "sport": "RUNNING", "utc_offset": "-3"}]
            ),
            "CSV_VALUE",
        ),
        (csv_file([{"start_time_utc": "2026-10-21T22:30:00", "sport": "RUNNING"}]), "NAIVE_TIME"),
        (csv_file() + b"1,2,3\r\n", "CSV_ROW"),
        (b"\xff\xfe" + csv_file(), "CSV_ENCODING"),
    ],
)
def test_csv_invalid_content(tmp_path: Path, data: bytes, detail: str) -> None:
    assert _rejection(tmp_path, parse_csv, data).detail == detail


@pytest.mark.req("INT-FIL-003")
def test_csv_activity_limit(tmp_path: Path) -> None:
    rows = [
        {"start_time_utc": f"2026-10-{day:02d}T10:00:00Z", "sport": "TENNIS"} for day in range(1, 4)
    ]
    two = ParserLimits(records=100, activities=2, xml_depth=64, xml_elements=1000)
    assert _rejection(tmp_path, parse_csv, csv_file(rows), two).detail == "ACTIVITIES"


@pytest.mark.req("SEC-UPL-004", "INT-FIL-003")
@pytest.mark.parametrize(
    "limits",
    [
        {"records": 0, "activities": 1, "xmlDepth": 1, "xmlElements": 1, "memoryBytes": 1},
        {"records": -5, "activities": 1, "xmlDepth": 1, "xmlElements": 1, "memoryBytes": 1},
        {"records": True, "activities": 1, "xmlDepth": 1, "xmlElements": 1, "memoryBytes": 1},
        {"records": 1.5, "activities": 1, "xmlDepth": 1, "xmlElements": 1, "memoryBytes": 1},
        {"records": 1, "activities": 1, "xmlDepth": 1, "xmlElements": 1},
    ],
)
def test_worker_refuses_requests_without_valid_limits(
    tmp_path: Path, limits: dict[str, object]
) -> None:
    path = tmp_path / "quarantined"
    path.write_bytes(fit_file())
    request = {"protocol": 1, "format": "FIT", "path": str(path), "limits": limits}
    response = handle(json.dumps(request).encode())
    assert response == {"ok": False, "reason": "PARSE_FAILED", "detail": "BAD_REQUEST"}
