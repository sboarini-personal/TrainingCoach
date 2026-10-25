"""Stages 2 and 3: extension allow-list and content signatures (SEC-UPL-001, SEC-UPL-005)."""

import pytest
from garmin_fit_sdk import CrcCalculator

from coach.application.files import FileRejectedError
from coach.domain.ingestion.record import RejectionReason
from coach.domain.raw.source import DetectedType
from coach.integrations.files.detection import detect, fit_crc, xml_root
from file_samples import csv_file, fit_file, gpx_file, tcx_file, zip_file

FIT = fit_file()


def _rejected(filename: str | None, data: bytes) -> FileRejectedError:
    with pytest.raises(FileRejectedError) as raised:
        detect(filename, data)
    return raised.value


@pytest.mark.req("SEC-UPL-001", "INT-FIL-001")
@pytest.mark.parametrize(
    ("filename", "data", "expected"),
    [
        ("run.fit", FIT, DetectedType.FIT),
        ("RUN.FIT", FIT, DetectedType.FIT),
        ("ride.tcx", tcx_file(), DetectedType.TCX),
        ("trail.gpx", gpx_file(), DetectedType.GPX),
        ("log.csv", csv_file(), DetectedType.CSV),
        ("log.csv", csv_file(bom=True), DetectedType.CSV),
        ("garmin.zip", zip_file({"a.fit": FIT}), DetectedType.ZIP),
    ],
)
def test_allowed_types_with_matching_content(
    filename: str, data: bytes, expected: DetectedType
) -> None:
    assert detect(filename, data) is expected


@pytest.mark.req("SEC-UPL-001", "SEC-UPL-005")
@pytest.mark.parametrize(
    "filename", ["run.exe", "run.fit.exe", "run", None, "", "notes.txt", "run.FIT.js", "..\\..\\"]
)
def test_other_extensions_are_refused(filename: str | None) -> None:
    assert _rejected(filename, FIT).reason is RejectionReason.EXTENSION_NOT_ALLOWED


@pytest.mark.req("SEC-UPL-005")
def test_path_tricks_in_the_name_only_count_their_last_component() -> None:
    assert detect("..\\..\\Windows\\run.fit", FIT) is DetectedType.FIT
    assert detect("/etc/passwd/../run.fit", FIT) is DetectedType.FIT


@pytest.mark.req("SEC-UPL-001")
@pytest.mark.acceptance("AT-UPL-001")
@pytest.mark.parametrize(
    ("filename", "data", "detail"),
    [
        ("run.fit", gpx_file(), "FIT_HEADER"),
        ("run.fit", b"\x0e\x20" + b"\x00" * 6 + b".XYZ" + b"\x00" * 6, "FIT_HEADER"),
        ("run.fit", FIT[:-10], "FIT_SIZE"),
        ("run.gpx", tcx_file(), "XML_ROOT"),
        ("run.tcx", gpx_file(), "XML_ROOT"),
        ("run.tcx", FIT, "XML_ENCODING"),
        ("run.gpx", b"plain text, not xml", "XML_ROOT"),
        ("run.csv", b"date,km\r\n2026-10-20,10\r\n", "CSV_TEMPLATE"),
        ("run.csv", csv_file() + b"\x00", "CSV_NUL"),
        ("run.zip", FIT, "ZIP_MAGIC"),
    ],
)
def test_content_must_match_the_extension(filename: str, data: bytes, detail: str) -> None:
    rejection = _rejected(filename, data)
    assert (rejection.reason, rejection.detail) == (RejectionReason.CONTENT_MISMATCH, detail)


@pytest.mark.req("SEC-UPL-001")
def test_fit_header_crc_is_checked_and_matches_the_sdk() -> None:
    assert fit_crc(FIT[:12]) == CrcCalculator.calculate_crc(FIT[:12], 0, 12)
    assert int.from_bytes(FIT[12:14], "little") == fit_crc(FIT[:12])
    broken = bytearray(FIT)
    broken[12] ^= 0xFF
    assert _rejected("run.fit", bytes(broken)).detail == "FIT_HEADER_CRC"
    unchecked = bytearray(FIT)
    unchecked[12:14] = b"\x00\x00"  # FIT allows 0: header CRC not computed
    assert detect("run.fit", bytes(unchecked)) is DetectedType.FIT


@pytest.mark.req("SEC-UPL-001", "SEC-UPL-002")
def test_xml_root_is_found_without_parsing_the_prolog() -> None:
    doctype = '<!DOCTYPE gpx [<!ENTITY a "x>y]"><!ENTITY b SYSTEM "file:///nowhere">]>'
    assert xml_root(gpx_file(prolog=doctype)) == "gpx"
    assert xml_root(b'\xef\xbb\xbf<?xml version="1.0"?><!-- <tcx> --><?pi x?><g:gpx/>') == "gpx"
    with pytest.raises(FileRejectedError):
        xml_root(b'<?xml version="1.0" encoding="ISO-8859-1"?><gpx/>')
    with pytest.raises(FileRejectedError):
        xml_root(b"<!DOCTYPE gpx [ never closed")
