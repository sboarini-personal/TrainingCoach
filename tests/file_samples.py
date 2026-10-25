"""Synthetic activity files for ingestion tests. No real athlete data.

FIT files are produced with the official Garmin FIT SDK encoder; TCX, GPX and CSV are text.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from garmin_fit_sdk import Encoder, Profile

from coach.domain.ingestion.templates import ACTIVITY_TEMPLATE

START: Final = datetime(2026, 10, 20, 21, 0, tzinfo=UTC)
FIT_EPOCH: Final = datetime(1989, 12, 31, tzinfo=UTC)
NUMS: Final = Profile["mesg_num"]


def fit_session(
    *,
    start: datetime = START,
    seconds: int = 600,
    sport: str = "running",
    sub_sport: str = "road",
    distance_per_second: float = 3.0,
    heart_rate: int = 150,
    extra: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Records and the session message of one synthetic session."""
    records = [
        {
            "mesg_num": NUMS["RECORD"],
            "timestamp": start + timedelta(seconds=second),
            "distance": second * distance_per_second,
            "heart_rate": heart_rate + second % 5,
            "enhanced_speed": distance_per_second,
            "cadence": 85,
        }
        for second in range(seconds + 1)
    ]
    session = {
        "mesg_num": NUMS["SESSION"],
        "timestamp": start + timedelta(seconds=seconds),
        "start_time": start,
        "sport": sport,
        "sub_sport": sub_sport,
        "total_elapsed_time": float(seconds),
        "total_timer_time": float(seconds),
        "total_distance": seconds * distance_per_second,
        "avg_heart_rate": heart_rate + 2,
        "max_heart_rate": heart_rate + 4,
        "enhanced_avg_speed": distance_per_second,
        "avg_cadence": 85,
        "max_cadence": 90,
        "total_ascent": 40,
        "total_training_effect": 3.1,
    }
    session.update(extra or {})
    return records, session


def fit_file(
    sessions: Iterable[tuple[list[dict[str, Any]], dict[str, Any]]] | None = None,
    *,
    file_type: str = "activity",
    manufacturer: str = "garmin",
    utc_offset_hours: float | None = -3,
) -> bytes:
    """A FIT activity file with the given sessions (default: one 10-minute road run)."""
    chosen = list(sessions) if sessions is not None else [fit_session()]
    encoder = Encoder()
    encoder.write_mesg(
        {
            "mesg_num": NUMS["FILE_ID"],
            "type": file_type,
            "manufacturer": manufacturer,
            "product": 1,
            "time_created": chosen[0][1]["start_time"],
        }
    )
    for records, session in chosen:
        for record in records:
            encoder.write_mesg(record)
        encoder.write_mesg(session)
    end = chosen[-1][1]["timestamp"]
    activity: dict[str, Any] = {
        "mesg_num": NUMS["ACTIVITY"],
        "timestamp": end,
        "num_sessions": len(chosen),
    }
    if utc_offset_hours is not None:
        local = end + timedelta(hours=utc_offset_hours)
        activity["local_timestamp"] = int((local - FIT_EPOCH).total_seconds())
    encoder.write_mesg(activity)
    return encoder.close()


def _later(start: str, seconds: int) -> str:
    """``start`` plus ``seconds``, keeping its offset notation."""
    moment = datetime.fromisoformat(start) + timedelta(seconds=seconds)
    text = moment.isoformat()
    return text.replace("+00:00", "Z") if start.endswith("Z") else text


def tcx_file(
    *,
    sport: str = "Running",
    laps: Iterable[Mapping[str, Any]] | None = None,
    start: str = "2026-10-20T21:00:00Z",
    notes: str | None = None,
    prolog: str = "",
) -> bytes:
    chosen: list[Mapping[str, Any]] = (
        list(laps)
        if laps is not None
        else [
            {"start": start, "seconds": 300, "distance": 900, "avg": 140, "max": 150, "speed": 3.5},
            {
                "start": "2026-10-20T21:05:00Z",
                "seconds": 300,
                "distance": 1000,
                "avg": 160,
                "max": 170,
                "speed": 4.0,
            },
        ]
    )
    lap_xml = []
    covered = 0.0  # track point distances are cumulative over the activity
    for lap in chosen:
        points = "".join(
            f"<Trackpoint><Time>{_later(lap['start'], second)}</Time>"
            f"<DistanceMeters>{covered + second * 3}</DistanceMeters>"
            f"<HeartRateBpm><Value>{lap['avg']}</Value></HeartRateBpm></Trackpoint>"
            for second in range(0, 3)
        )
        covered += float(lap["distance"])
        lap_xml.append(
            f'<Lap StartTime="{lap["start"]}"><TotalTimeSeconds>{lap["seconds"]}</TotalTimeSeconds>'
            f"<DistanceMeters>{lap['distance']}</DistanceMeters><MaximumSpeed>{lap['speed']}</MaximumSpeed>"
            f"<AverageHeartRateBpm><Value>{lap['avg']}</Value></AverageHeartRateBpm>"
            f"<MaximumHeartRateBpm><Value>{lap['max']}</Value></MaximumHeartRateBpm>"
            f"<Track>{points}</Track></Lap>"
        )
    notes_xml = "" if notes is None else f"<Notes>{notes}</Notes>"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>{prolog}'
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">'
        f'<Activities><Activity Sport="{sport}"><Id>{chosen[0]["start"]}</Id>'
        f"{''.join(lap_xml)}{notes_xml}</Activity></Activities></TrainingCenterDatabase>"
    ).encode()


def gpx_file(
    *,
    kind: str | None = "running",
    name: str | None = "Evening run",
    points: Iterable[tuple[float, float, str, int | None]] | None = None,
    prolog: str = "",
    segments: Iterable[Iterable[tuple[float, float, str, int | None]]] | None = None,
) -> bytes:
    """GPX 1.1 track; points are ``(lat, lon, time, heart rate)``."""
    default = [
        (-34.6, -58.4, "2026-10-20T21:00:00Z", 140),
        (-34.6, -58.3989, "2026-10-20T21:00:30Z", 150),
        (-34.6, -58.3978, "2026-10-20T21:01:00Z", 160),
    ]
    single = default if points is None else list(points)
    chosen_segments = list(segments) if segments is not None else [single]
    segment_xml = []
    for segment in chosen_segments:
        point_xml = []
        for latitude, longitude, time, heart_rate in segment:
            extension = (
                ""
                if heart_rate is None
                else "<extensions><gpxtpx:TrackPointExtension>"
                f"<gpxtpx:hr>{heart_rate}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>"
            )
            point_xml.append(
                f'<trkpt lat="{latitude}" lon="{longitude}"><ele>25</ele><time>{time}</time>'
                f"{extension}</trkpt>"
            )
        segment_xml.append(f"<trkseg>{''.join(point_xml)}</trkseg>")
    name_xml = "" if name is None else f"<name>{name}</name>"
    kind_xml = "" if kind is None else f"<type>{kind}</type>"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>{prolog}'
        '<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
        f"<trk>{name_xml}{kind_xml}{''.join(segment_xml)}</trk></gpx>"
    ).encode()


def csv_file(rows: Iterable[Mapping[str, str]] | None = None, *, bom: bool = False) -> bytes:
    names = [column.name for column in ACTIVITY_TEMPLATE.columns]
    chosen = (
        list(rows)
        if rows is not None
        else [
            {
                "start_time_utc": "2026-10-21T22:30:00Z",
                "utc_offset": "-03:00",
                "sport": "TENNIS",
                "subtype": "MATCH",
                "elapsed_seconds": "5400",
                "rpe": "7",
                "name": "Singles",
                "comments": "felt good",
            }
        ]
    )
    lines = [",".join(names)]
    for row in chosen:
        lines.append(",".join(row.get(name, "") for name in names))
    text = "\r\n".join(lines) + "\r\n"
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode()


def zip_file(members: Mapping[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()
