from dataclasses import replace
from datetime import date, datetime
from typing import Any

import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import DateRange
from coach.domain.raw.source import (
    DetectedType,
    MalwareScan,
    RawSource,
    SourceOrigin,
    clean_filename,
)
from samples import sample_raw_source


def _with(**changes: Any) -> RawSource:
    return replace(sample_raw_source(), **changes)


@pytest.mark.req("FR-ACT-002", "SEC-UPL-006")
@pytest.mark.parametrize(
    "changes",
    [
        {"origin": SourceOrigin.GARMIN_PERSONAL_API, "malware_scan": MalwareScan.NOT_APPLICABLE},
        {"origin": SourceOrigin.GARMIN_PERSONAL_API, "provider": "garmin"},
        {"provider": "garmin"},
        {"malware_scan": MalwareScan.NOT_APPLICABLE},
        {"origin": SourceOrigin.MANUAL_ENTRY, "malware_scan": MalwareScan.CLEAN},
    ],
)
def test_origin_rules(changes: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        _with(**changes)


@pytest.mark.req("FR-ACT-002")
def test_provider_payload_names_garmin() -> None:
    raw = _with(
        origin=SourceOrigin.GARMIN_PERSONAL_API,
        provider="garmin",
        provider_object_id="synthetic-1001",
        malware_scan=MalwareScan.NOT_APPLICABLE,
        original_filename=None,
    )
    assert raw.provider == "garmin"


@pytest.mark.req("FR-ACT-002")
@pytest.mark.parametrize(
    "changes",
    [
        {"sha256": "A" * 64},
        {"sha256": "a" * 63},
        {"sha256": "../" + "a" * 61},
        {"byte_size": -1},
        {"category": "activity file"},
        {"received_at": datetime(2026, 10, 24, 18, 0)},  # noqa: DTZ001
    ],
)
def test_invalid_raw_sources_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        _with(**changes)


@pytest.mark.req("SEC-UPL-005")
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("morning_run.fit", "morning_run.fit"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\Downloads\\run.fit", "run.fit"),
        ("run\x00\x07.fit", "run.fit"),
        ("   ", None),
        ("../", None),
        (None, None),
    ],
)
def test_original_filename_is_display_only_and_sanitised(
    given: str | None, expected: str | None
) -> None:
    assert clean_filename(given) == expected
    assert _with(original_filename=given).original_filename == expected


@pytest.mark.req("SEC-UPL-005")
def test_long_filenames_are_truncated() -> None:
    name = clean_filename("x" * 300 + ".fit")
    assert name is not None
    assert len(name) == 255


@pytest.mark.req("INT-GAR-009")
def test_provider_payloads_record_their_request_window() -> None:
    window = DateRange(date(2026, 10, 1), date(2026, 10, 7))
    page = _with(
        origin=SourceOrigin.GARMIN_PERSONAL_API,
        provider="garmin",
        malware_scan=MalwareScan.NOT_APPLICABLE,
        detected_type=DetectedType.JSON,
        category="GARMIN_ACTIVITY_LIST",
        request_window=window,
    )
    assert page.request_window == window
    with pytest.raises(DomainValidationError, match="window"):
        _with(request_window=window)
