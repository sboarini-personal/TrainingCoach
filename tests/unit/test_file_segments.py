"""Laps, pool lengths and power sensors from files (files spec 8.6, normalisation rules 1.1.0).

Synthetic FIT files from the official SDK encoder; TCX as text. No real athlete data.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from coach.application.files import DecodedSession, FileRejectedError
from coach.domain.activity.fields import CadenceUnit, PowerSensor, StrokeType
from coach.domain.activity.segments import LapIntensity, LapTrigger, LengthType
from coach.domain.common.sport import Sport
from coach.domain.ingestion.record import RejectionReason
from coach.domain.raw.source import DetectedType
from coach.integrations.files.fit_parser import parse_fit
from coach.integrations.files.normalise import normalise
from coach.integrations.files.output import decode_response
from coach.integrations.files.parsing import ParserLimits
from coach.integrations.files.tcx_parser import parse_tcx
from file_samples import (
    START,
    fit_device,
    fit_file,
    fit_lap,
    fit_length,
    fit_session,
    tcx_file,
)
from samples import NOW

LIMITS = ParserLimits(records=100_000, activities=500, xml_depth=64, xml_elements=100_000)
PARSERS: dict[DetectedType, Callable[[Path, ParserLimits], dict[str, Any]]] = {
    DetectedType.FIT: parse_fit,
    DetectedType.TCX: parse_tcx,
}


def _output(tmp_path: Path, fmt: DetectedType, data: bytes) -> dict[str, Any]:
    path = tmp_path / "quarantined"
    path.write_bytes(data)
    return PARSERS[fmt](path, LIMITS)


def _decode(tmp_path: Path, fmt: DetectedType, data: bytes) -> list[DecodedSession]:
    response = {"ok": True, "output": _output(tmp_path, fmt, data)}
    return normalise(decode_response(response, fmt, max_sessions=500), received_at=NOW)


def _with(
    session: tuple[list[dict[str, Any]], dict[str, Any]], *extra: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, summary = session
    return [*records, *extra], summary


def _intervals() -> list[dict[str, Any]]:
    """Warm-up, two 120 s reps with a rest between, cool-down: 600 s in all."""
    at = START
    laps = []
    for seconds, distance, intensity, trigger in (
        (180, 540, "warmup", "manual"),
        (120, 480, "active", "distance"),
        (60, 120, "rest", "time"),
        (120, 470, "active", "distance"),
        (120, 190, "cooldown", "session_end"),
    ):
        laps.append(fit_lap(at, seconds, distance, intensity=intensity, trigger=trigger))
        at += timedelta(seconds=seconds)
    return laps


# --------------------------------------------------------------------------- laps


@pytest.mark.req("INT-FIL-001", "FR-RUN-001")
def test_fit_laps_become_the_laps_stream(tmp_path: Path) -> None:
    data = fit_file([_with(fit_session(), *_intervals())])
    (session,) = _decode(tmp_path, DetectedType.FIT, data)
    laps = session.laps
    assert laps is not None
    assert [lap.start_offset_seconds for lap in laps.laps] == [0.0, 180.0, 300.0, 360.0, 480.0]
    assert [lap.intensity for lap in laps.laps] == [
        LapIntensity.WARMUP,
        LapIntensity.ACTIVE,
        LapIntensity.REST,
        LapIntensity.ACTIVE,
        LapIntensity.COOLDOWN,
    ]
    assert [lap.trigger for lap in laps.laps][:3] == [
        LapTrigger.MANUAL,
        LapTrigger.DISTANCE,
        LapTrigger.TIME,
    ]
    second = laps.laps[1]
    assert (second.elapsed_seconds, second.timer_seconds, second.distance_meters) == (
        120.0,
        120.0,
        480.0,
    )
    assert (second.average_hr, second.average_cadence) == (150.0, 170.0)  # strides x 2
    assert laps.cadence_unit is CadenceUnit.STEPS_PER_MIN
    assert session.normaliser_version == "1.1.0"


@pytest.mark.req("INT-FIL-001")
def test_laps_outside_the_session_or_without_time_are_left_out(tmp_path: Path) -> None:
    after = START + timedelta(seconds=700)  # the session lasts 600 s
    data = fit_file(
        [
            _with(
                fit_session(),
                fit_lap(START, 600, 1800),
                fit_lap(START + timedelta(seconds=600), 0, 0),  # a double press
                fit_lap(after, 60, 100),
            )
        ]
    )
    (session,) = _decode(tmp_path, DetectedType.FIT, data)
    assert session.laps is not None
    assert len(session.laps) == 1


@pytest.mark.req("INT-FIL-001", "FR-ACT-009")
def test_an_inconsistent_lap_list_is_dropped_but_the_session_imported(tmp_path: Path) -> None:
    output = _output(tmp_path, DetectedType.FIT, fit_file([_with(fit_session(), *_intervals())]))
    output["sessions"][0]["laps"][1]["elapsedSeconds"] = 90_000.0  # more than a day
    response = {"ok": True, "output": output}
    (session,) = normalise(
        decode_response(response, DetectedType.FIT, max_sessions=500), received_at=NOW
    )
    assert session.laps is None
    assert session.values["distance.meters"] == 1800.0


@pytest.mark.req("INT-FIL-001")
def test_tcx_laps_run_to_the_next_lap_start(tmp_path: Path) -> None:
    data = tcx_file(
        laps=[
            {
                "start": "2026-10-20T21:00:00Z",
                "seconds": 290,
                "distance": 900,
                "avg": 140,
                "max": 150,
                "speed": 3.5,
                "intensity": "Active",
                "trigger": "Distance",
            },
            {
                "start": "2026-10-20T21:05:00Z",
                "seconds": 60,
                "distance": 100,
                "avg": 120,
                "max": 130,
                "speed": 2.0,
                "intensity": "Resting",
                "trigger": "HeartRate",
            },
        ]
    )
    (session,) = _decode(tmp_path, DetectedType.TCX, data)
    assert session.laps is not None
    first, second = session.laps.laps
    assert (first.elapsed_seconds, first.timer_seconds) == (300.0, 290.0)
    assert second.elapsed_seconds is None  # the last lap: unknown
    assert (first.intensity, second.intensity) == (LapIntensity.ACTIVE, LapIntensity.REST)
    assert (first.trigger, second.trigger) == (LapTrigger.DISTANCE, LapTrigger.OTHER)
    assert (first.average_hr, second.max_hr) == (140.0, 130.0)


# --------------------------------------------------------------------------- lengths


def _pool_swim() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, summary = fit_session(
        sport="swimming",
        sub_sport="lap_swimming",
        seconds=100,
        distance_per_second=1.0,
        extra={"pool_length": 25.0, "num_active_lengths": 3, "swim_stroke": "freestyle"},
    )
    lengths = [
        fit_length(START, 24.5, 16),
        fit_length(START + timedelta(seconds=25), 25, 17),  # FIT times are whole seconds
        fit_length(START + timedelta(seconds=50), 20, None, length_type="idle", stroke=None),
        fit_length(START + timedelta(seconds=70), 26, 18, stroke="backstroke"),
    ]
    return [*records, *lengths], summary


@pytest.mark.req("INT-FIL-001", "FR-SWM-002")
def test_pool_lengths_become_the_lengths_stream(tmp_path: Path) -> None:
    (session,) = _decode(tmp_path, DetectedType.FIT, fit_file([_pool_swim()]))
    assert session.values["environment.poolLengthMeters"] == 25.0
    lengths = session.lengths
    assert lengths is not None
    assert [length.type for length in lengths.lengths] == [
        LengthType.ACTIVE,
        LengthType.ACTIVE,
        LengthType.IDLE,
        LengthType.ACTIVE,
    ]
    assert [length.strokes for length in lengths.lengths] == [16, 17, None, 18]
    assert [length.stroke_type for length in lengths.lengths] == [
        StrokeType.FREESTYLE,
        StrokeType.FREESTYLE,
        None,
        StrokeType.BACKSTROKE,
    ]
    assert lengths.lengths[1].start_offset_seconds == 25.0


@pytest.mark.req("INT-FIL-001", "FR-SWM-001")
def test_lengths_belong_to_swims_only(tmp_path: Path) -> None:
    data = fit_file([_with(fit_session(), fit_length(START, 30, 20))])
    (session,) = _decode(tmp_path, DetectedType.FIT, data)
    assert session.lengths is None


# --------------------------------------------------------------------------- power sensor


def _ride(*devices: dict[str, Any], power: bool = True) -> bytes:
    extra = {"avg_power": 210, "max_power": 480} if power else {}
    session = fit_session(sport="cycling", sub_sport="road", distance_per_second=8.0, extra=extra)
    return fit_file([_with(session, *devices)])


@pytest.mark.req("INT-FIL-001", "FR-CYC-002")
@pytest.mark.parametrize(
    ("devices", "expected"),
    [
        ((fit_device(11),), PowerSensor.POWER_METER),
        ((fit_device(17),), PowerSensor.SMART_TRAINER),
        ((fit_device(7, source="bluetooth_low_energy"),), PowerSensor.SMART_TRAINER),
        ((fit_device(17), fit_device(11)), PowerSensor.POWER_METER),  # a dedicated meter wins
        ((fit_device(120),), None),  # a heart-rate strap measures no power
        ((), None),
    ],
)
def test_the_power_sensor_named_by_the_file(
    tmp_path: Path, devices: tuple[dict[str, Any], ...], expected: PowerSensor | None
) -> None:
    (session,) = _decode(tmp_path, DetectedType.FIT, _ride(*devices))
    assert session.values.get("performance.powerSensor") is expected


@pytest.mark.req("FR-CYC-002", "INT-FIL-001")
def test_a_sensor_is_kept_as_its_device_type_only(tmp_path: Path) -> None:
    output = _output(tmp_path, DetectedType.FIT, _ride(fit_device(11)))
    assert output["sessions"][0]["sensors"] == ["bike_power"]
    assert "3141592653" not in json.dumps(output)  # the serial number is never copied
    (session,) = _decode(tmp_path, DetectedType.FIT, _ride(fit_device(11), power=False))
    assert "performance.powerSensor" not in session.values  # no power, nothing to attribute


# --------------------------------------------------------------------------- decoder


@pytest.mark.req("SEC-UPL-004")
@pytest.mark.parametrize(
    "corrupt",
    [
        lambda session: session["laps"][0].update(position=1.0),
        lambda session: session["laps"][0].pop("startOffsetSeconds"),
        lambda session: session.update(sensors=["Bike Power"]),
        lambda session: session.update(sensors=["x"] * 33),
        lambda session: session.pop("lengths"),
    ],
)
def test_the_decoder_refuses_malformed_segments(
    tmp_path: Path, corrupt: Callable[[dict[str, Any]], object]
) -> None:
    output = _output(tmp_path, DetectedType.FIT, fit_file([_with(fit_session(), *_intervals())]))
    corrupt(output["sessions"][0])
    with pytest.raises(FileRejectedError) as raised:
        decode_response({"ok": True, "output": output}, DetectedType.FIT, max_sessions=500)
    assert raised.value.reason is RejectionReason.SCHEMA_INVALID


@pytest.mark.req("INT-FIL-001")
def test_every_session_names_its_sport_and_streams(tmp_path: Path) -> None:
    (session,) = _decode(tmp_path, DetectedType.FIT, fit_file([_pool_swim()]))
    assert session.values["classification.sport"] is Sport.SWIMMING
    assert session.laps is None  # this swim has lengths but no lap messages
