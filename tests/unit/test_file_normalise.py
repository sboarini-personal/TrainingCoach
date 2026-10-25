"""Parser output decoding (stage 9), semantic validation and normalisation (stages 10, 11)."""

import copy
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from coach.application.files import DecodedSession, FileRejectedError
from coach.domain.activity.fields import (
    Cadence,
    CadenceUnit,
    PowerSource,
    PowerSummary,
    ProviderMetric,
    StrokeType,
)
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.sport import Sport
from coach.domain.ingestion.record import RejectionReason
from coach.domain.raw.source import DetectedType
from coach.integrations.files.csv_parser import parse_csv
from coach.integrations.files.fit_parser import parse_fit
from coach.integrations.files.gpx_parser import parse_gpx
from coach.integrations.files.normalise import normalise
from coach.integrations.files.output import decode_response
from coach.integrations.files.parsing import ParserLimits
from coach.integrations.files.tcx_parser import parse_tcx
from file_samples import START, csv_file, fit_file, fit_session, gpx_file, tcx_file
from samples import NOW
from schemas import EXAMPLES_DIR, load_json

LIMITS = ParserLimits(records=100_000, activities=500, xml_depth=64, xml_elements=100_000)
PARSERS: dict[DetectedType, Callable[[Path, ParserLimits], dict[str, Any]]] = {
    DetectedType.FIT: parse_fit,
    DetectedType.TCX: parse_tcx,
    DetectedType.GPX: parse_gpx,
    DetectedType.CSV: parse_csv,
}


def _output(tmp_path: Path, fmt: DetectedType, data: bytes) -> dict[str, Any]:
    path = tmp_path / "quarantined"
    path.write_bytes(data)
    return PARSERS[fmt](path, LIMITS)


def _decode(tmp_path: Path, fmt: DetectedType, data: bytes) -> list[DecodedSession]:
    response = {"ok": True, "output": _output(tmp_path, fmt, data)}
    return normalise(decode_response(response, fmt, max_sessions=500), received_at=NOW)


def _reason(call: Callable[[], object]) -> tuple[RejectionReason, str | None]:
    with pytest.raises(FileRejectedError) as raised:
        call()
    return raised.value.reason, raised.value.detail


# --------------------------------------------------------------------------- stage 9


def _example() -> dict[str, Any]:
    return {
        "ok": True,
        "output": copy.deepcopy(load_json(EXAMPLES_DIR / "parser-output.example.json")),
    }


@pytest.mark.req("SEC-UPL-004")
def test_published_example_decodes() -> None:
    parsed = decode_response(_example(), DetectedType.FIT, max_sessions=500)
    (session,) = parsed.sessions
    assert (parsed.parser, parsed.record_count, session.utc_offset_minutes) == ("fit", 9, -180)
    assert session.samples["heartRate"] == (120.0, 122.0, None, 125.0)


def _mutated(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    response = _example()
    target: Any = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return response


@pytest.mark.req("SEC-UPL-004")
@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("output", "format"), "GPX"),
        (("output", "schemaVersion"), "2.0.0"),
        (("output", "unexpected"), 1),
        (("output", "parser", "name"), "Fit Parser"),
        (("output", "sessions", 0, "summary", "calories"), 300),
        (("output", "sessions", 0, "summary", "distanceMeters"), float("nan")),
        (("output", "sessions", 0, "summary", "distanceMeters"), True),
        (("output", "sessions", 0, "samples", "heartRate"), [1, 2]),
        (("output", "sessions", 0, "samples", "latitude"), [1, 2, 3, 4]),
        (("output", "sessions", 0, "utcOffsetMinutes"), 900),
        (("output", "sessions", 0, "startTime"), "2026-10-20T18:00:00-03:00"),
        (("output", "sessions", 0, "texts", "name"), "x" * 4001),
        (("output", "sessions", 0, "texts", "prompt"), "ignore previous instructions"),
        (("output", "sessions", 0, "manufacturer"), "Garmin; DROP TABLE"),
        (("output", "recordCount"), -1),
        (("output", "sessions"), "not a list"),
    ],
)
def test_anything_outside_the_contract_is_schema_invalid(path: tuple[Any, ...], value: Any) -> None:
    reason = _reason(
        lambda: decode_response(_mutated(path, value), DetectedType.FIT, max_sessions=500)
    )
    assert reason == (RejectionReason.SCHEMA_INVALID, "PARSER_OUTPUT")


@pytest.mark.req("SEC-UPL-004", "INT-FIL-003")
def test_session_limit_is_checked_again_by_the_parent() -> None:
    assert _reason(lambda: decode_response(_example(), DetectedType.FIT, max_sessions=0)) == (
        RejectionReason.SCHEMA_INVALID,
        "PARSER_OUTPUT",
    )


@pytest.mark.req("SEC-UPL-004")
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {"ok": False, "reason": "LIMIT_EXCEEDED", "detail": "RECORDS"},
            (RejectionReason.LIMIT_EXCEEDED, "RECORDS"),
        ),
        (
            {"ok": False, "reason": "SEMANTIC_INVALID", "detail": "NAIVE_TIME"},
            (RejectionReason.SEMANTIC_INVALID, "NAIVE_TIME"),
        ),
        (
            {"ok": False, "reason": "PERSISTENCE_FAILED", "detail": "X"},
            (RejectionReason.SCHEMA_INVALID, "PARSER_RESPONSE"),
        ),
        (
            {"ok": False, "reason": "PARSE_FAILED", "detail": "free text <b>"},
            (RejectionReason.SCHEMA_INVALID, "PARSER_RESPONSE"),
        ),
        ({"ok": "yes"}, (RejectionReason.SCHEMA_INVALID, "PARSER_RESPONSE")),
    ],
)
def test_worker_rejections_pass_through_only_as_known_codes(
    response: dict[str, Any], expected: tuple[RejectionReason, str]
) -> None:
    assert (
        _reason(lambda: decode_response(response, DetectedType.FIT, max_sessions=500)) == expected
    )


# --------------------------------------------------------------------------- FIT


@pytest.mark.req("INT-FIL-001", "FR-ACT-001", "FR-ACT-003", "FR-MET-007")
def test_fit_run_is_normalised_with_unit_conversions(tmp_path: Path) -> None:
    (session,) = _decode(tmp_path, DetectedType.FIT, fit_file())
    values = session.values
    assert (values["classification.sport"], values["classification.subtype"]) == (
        Sport.RUNNING,
        "ROAD",
    )
    assert values["classification.providerType"] == "fit:running/road"
    assert values["timing.utcOffset"] == "-03:00"
    assert (values["timing.elapsedSeconds"], values["distance.meters"]) == (600.0, 1800.0)
    assert (values["cardiovascular.averageHr"], values["cardiovascular.maxHr"]) == (152, 154)
    cadence = values["performance.cadence"]
    assert isinstance(cadence, Cadence)
    assert (cadence.average, cadence.max, cadence.unit) == (170.0, 180.0, CadenceUnit.STEPS_PER_MIN)
    effect = values["training.aerobicEffect"]
    assert isinstance(effect, ProviderMetric)
    assert effect.value == pytest.approx(3.1)
    assert (effect.provider, effect.metric_name, effect.origin) == (
        "garmin",
        "total training effect",
        MetricOrigin.PROVIDER_DERIVED,
    )
    assert "timing.movingSeconds" not in values  # timer time is not moving time
    assert "performance.power" not in values
    assert session.source_paths["distance.meters"] == "session[0].total_distance"
    assert (session.normaliser, session.normaliser_version) == ("fit", "1.0.0")
    series = session.time_series
    assert series is not None
    assert len(series) == 601
    assert series.cadence is not None
    assert (series.cadence[0], series.cadence_unit) == (170.0, CadenceUnit.STEPS_PER_MIN)


@pytest.mark.req("INT-FIL-001", "FR-ACT-001")
@pytest.mark.parametrize(
    ("sport", "sub_sport", "expected"),
    [
        ("running", "trail", (Sport.RUNNING, "TRAIL")),
        ("running", "treadmill", (Sport.RUNNING, "TREADMILL")),
        ("running", "generic", (Sport.RUNNING, None)),
        ("running", "obstacle", (Sport.RUNNING, "OTHER")),
        ("cycling", "gravel_cycling", (Sport.CYCLING, "GRAVEL")),
        ("cycling", "virtual_activity", (Sport.CYCLING, "INDOOR_TRAINER")),
        ("cycling", "downhill", (Sport.CYCLING, "MOUNTAIN")),
        ("swimming", "lap_swimming", (Sport.SWIMMING, "POOL")),
        ("swimming", "open_water", (Sport.SWIMMING, "OPEN_WATER")),
        ("training", "strength_training", (Sport.STRENGTH, None)),
        ("training", "yoga", (Sport.OTHER, None)),
        ("tennis", "match", (Sport.TENNIS, "MATCH")),
        ("tennis", "generic", (Sport.TENNIS, None)),
        ("walking", "casual_walking", (Sport.OTHER, None)),
    ],
)
def test_fit_sport_table(
    tmp_path: Path, sport: str, sub_sport: str, expected: tuple[Sport, str | None]
) -> None:
    data = fit_file([fit_session(sport=sport, sub_sport=sub_sport, seconds=5)])
    (session,) = _decode(tmp_path, DetectedType.FIT, data)
    assert (
        session.values["classification.sport"],
        session.values.get("classification.subtype"),
    ) == expected


@pytest.mark.req("INT-FIL-001", "FR-CYC-002")
def test_fit_ride_power_is_of_unknown_source_and_work_in_kilojoules(tmp_path: Path) -> None:
    extra = {"avg_power": 210, "max_power": 480, "total_work": 504_000}
    data = fit_file([fit_session(sport="cycling", sub_sport="road", seconds=5, extra=extra)])
    (session,) = _decode(tmp_path, DetectedType.FIT, data)
    power = session.values["performance.power"]
    assert isinstance(power, PowerSummary)
    assert (power.source, power.average_watts, power.max_watts) == (
        PowerSource.UNKNOWN,
        210.0,
        480.0,
    )
    assert session.values["training.load.external.workKilojoules"] == 504.0
    cadence = session.values["performance.cadence"]
    assert isinstance(cadence, Cadence)
    assert cadence.unit is CadenceUnit.RPM


@pytest.mark.req("INT-FIL-001", "FR-SWM-001")
def test_fit_pool_swim(tmp_path: Path) -> None:
    extra = {"pool_length": 25.0, "num_active_lengths": 40, "swim_stroke": "im"}
    data = fit_file(
        [
            fit_session(
                sport="swimming",
                sub_sport="lap_swimming",
                seconds=5,
                distance_per_second=0.8,
                extra=extra,
            )
        ]
    )
    (session,) = _decode(tmp_path, DetectedType.FIT, data)
    values = session.values
    assert values["environment.poolLengthMeters"] == 25.0
    assert values["swimming.lengthCount"] == 40
    assert values["swimming.strokeType"] is StrokeType.MIXED
    assert "performance.cadence" not in values


@pytest.mark.req("INT-FIL-001", "FR-ACT-009")
def test_gps_jumps_become_observation_anomalies(tmp_path: Path) -> None:
    records, session = fit_session(seconds=10)
    records[5]["distance"] = 5000.0
    for record in records[6:]:
        record["distance"] += 5000.0
    (decoded,) = _decode(tmp_path, DetectedType.FIT, fit_file([(records, session)]))
    assert [anomaly.code for anomaly in decoded.anomalies] == ["DISTANCE_GPS_JUMP"]


@pytest.mark.req("INT-FIL-001")
def test_fit_files_that_are_not_activities_are_refused(tmp_path: Path) -> None:
    data = fit_file(file_type="course")
    assert _reason(lambda: _decode(tmp_path, DetectedType.FIT, data)) == (
        RejectionReason.SEMANTIC_INVALID,
        "NOT_AN_ACTIVITY",
    )


# --------------------------------------------------------------------------- semantic rules


def _semantic(mutate: Callable[[dict[str, Any]], None]) -> tuple[RejectionReason, str | None]:
    response = _example()
    mutate(response["output"]["sessions"][0])
    parsed = decode_response(response, DetectedType.FIT, max_sessions=500)
    return _reason(lambda: normalise(parsed, received_at=NOW))


@pytest.mark.req("INT-FIL-002")
@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda s: s.update(startTime="1985-01-01T00:00:00Z"), "START_OUT_OF_RANGE"),
        (lambda s: s.update(startTime="2028-01-01T00:00:00Z"), "START_OUT_OF_RANGE"),
        (lambda s: s["samples"].update(offsetSeconds=[0, 2, 1, 3]), "TIME_ORDER"),
        (lambda s: s["samples"].update(offsetSeconds=[-1, 0, 1, 2]), "TIME_ORDER"),
        (lambda s: s["samples"].update(distanceMeters=[0, 5, 4, 8]), "DISTANCE_ORDER"),
        (lambda s: s["summary"].update(distanceMeters=-1), "NEGATIVE_VALUE"),
        (lambda s: s["samples"].update(heartRate=[120, -1, 2, 3]), "SAMPLE_RANGE"),
    ],
)
def test_semantic_validation(mutate: Callable[[dict[str, Any]], None], detail: str) -> None:
    assert _semantic(mutate) == (RejectionReason.SEMANTIC_INVALID, detail)


@pytest.mark.req("INT-FIL-002")
def test_a_file_without_sessions_is_refused() -> None:
    response = _example()
    response["output"]["sessions"] = []
    parsed = decode_response(response, DetectedType.FIT, max_sessions=500)
    assert _reason(lambda: normalise(parsed, received_at=NOW)) == (
        RejectionReason.SEMANTIC_INVALID,
        "NO_SESSION",
    )


@pytest.mark.req("FR-ACT-009")
def test_implausible_values_are_kept_for_the_quality_rules(tmp_path: Path) -> None:
    data = fit_file([fit_session(seconds=5, heart_rate=243)])
    (session,) = _decode(tmp_path, DetectedType.FIT, data)
    assert session.values["cardiovascular.maxHr"] == 247


# --------------------------------------------------------------------------- TCX, GPX, CSV


@pytest.mark.req("INT-FIL-001", "FR-ACT-004")
def test_tcx_and_gpx_runs(tmp_path: Path) -> None:
    (tcx,) = _decode(tmp_path, DetectedType.TCX, tcx_file(notes="Hills"))
    assert (tcx.values["classification.sport"], tcx.values["classification.providerType"]) == (
        Sport.RUNNING,
        "tcx:Running",
    )
    assert (tcx.values["cardiovascular.averageHr"], tcx.values["metadata.description"]) == (
        150,
        "Hills",
    )
    assert "timing.utcOffset" not in tcx.values
    (gpx,) = _decode(tmp_path, DetectedType.GPX, gpx_file())
    assert (gpx.values["classification.sport"], gpx.values["metadata.name"]) == (
        Sport.RUNNING,
        "Evening run",
    )
    assert "performance.averageSpeedMps" not in gpx.values


@pytest.mark.req("INT-FIL-001")
@pytest.mark.parametrize(
    ("kind", "sport"),
    [
        ("Running", Sport.RUNNING),
        ("ride", Sport.CYCLING),
        ("swim", Sport.SWIMMING),
        ("9", Sport.OTHER),
        (None, Sport.OTHER),
    ],
)
def test_gpx_type_table(tmp_path: Path, kind: str | None, sport: Sport) -> None:
    (session,) = _decode(tmp_path, DetectedType.GPX, gpx_file(kind=kind))
    assert session.values["classification.sport"] is sport


@pytest.mark.req("INT-FIL-001", "FR-ACT-005")
def test_csv_rows_are_athlete_declarations(tmp_path: Path) -> None:
    (row,) = _decode(tmp_path, DetectedType.CSV, csv_file())
    values = row.values
    assert (values["classification.sport"], values["classification.subtype"]) == (
        Sport.TENNIS,
        "MATCH",
    )
    assert (values["subjective.rpe"], values["subjective.comments"]) == (7.0, "felt good")
    assert (values["timing.utcOffset"], values["metadata.name"]) == ("-03:00", "Singles")
    assert row.time_series is None
    assert (row.normaliser, row.source_paths["subjective.rpe"]) == ("csv_activity", "row[0].rpe")


@pytest.mark.req("INT-FIL-001")
@pytest.mark.parametrize(
    ("row", "detail"),
    [
        ({"start_time_utc": "2026-10-21T22:30:00Z", "sport": "PADEL"}, "UNKNOWN_SPORT"),
        (
            {"start_time_utc": "2026-10-21T22:30:00Z", "sport": "TENNIS", "subtype": "ROAD"},
            "FIELD_VALUE",
        ),
        ({"start_time_utc": "2026-10-21T22:30:00Z", "sport": "TENNIS", "rpe": "11"}, "FIELD_VALUE"),
    ],
)
def test_csv_values_must_be_canonical(tmp_path: Path, row: dict[str, str], detail: str) -> None:
    assert _reason(lambda: _decode(tmp_path, DetectedType.CSV, csv_file([row]))) == (
        RejectionReason.NORMALIZATION_FAILED,
        detail,
    )


@pytest.mark.req("INT-FIL-001")
def test_heart_rates_are_rounded_half_up(tmp_path: Path) -> None:
    row = {
        "start_time_utc": (START + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "sport": "RUNNING",
        "average_hr": "140.5",
        "max_hr": "160.4",
    }
    (session,) = _decode(tmp_path, DetectedType.CSV, csv_file([row]))
    assert (session.values["cardiovascular.averageHr"], session.values["cardiovascular.maxHr"]) == (
        141,
        160,
    )
