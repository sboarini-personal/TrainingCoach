"""Extension allow-list and content signatures (SEC-UPL-001; stages 2 and 3).

Nothing here parses the file: FIT headers are checked byte by byte, XML root elements are
found with a scanner that reads only the start of the file, and CSV headers are compared with
the supported templates (spec/security/uploads.spec.md section 1).
"""

from __future__ import annotations

import codecs
import re
from pathlib import PurePosixPath
from typing import Final

from coach.application.files import FileRejectedError
from coach.domain.ingestion.record import RejectionReason
from coach.domain.ingestion.templates import TEMPLATES
from coach.domain.raw.source import DetectedType, clean_filename

EXTENSIONS: Final = {
    ".fit": DetectedType.FIT,
    ".tcx": DetectedType.TCX,
    ".gpx": DetectedType.GPX,
    ".csv": DetectedType.CSV,
    ".zip": DetectedType.ZIP,
}
XML_ROOTS: Final = {DetectedType.TCX: "TrainingCenterDatabase", DetectedType.GPX: "gpx"}
ZIP_MAGIC: Final = b"PK\x03\x04"
FIT_MARKER: Final = b".FIT"
SNIFF_BYTES: Final = 64 * 1024
UTF8_BOM: Final = b"\xef\xbb\xbf"

_FIT_CRC_TABLE: Final = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)  # fmt: skip
_XML_DECLARATION: Final = re.compile(r"<\?xml\s[^>]*?\?>", re.DOTALL)
_ENCODING: Final = re.compile(r"""encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")
_NAME: Final = re.compile(r"[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?")


def _mismatch(detail: str) -> FileRejectedError:
    return FileRejectedError(RejectionReason.CONTENT_MISMATCH, detail)


def extension_type(filename: str | None) -> tuple[str, DetectedType]:
    """Stage 2: the lower-case extension of the sanitised name and its type."""
    name = clean_filename(filename)
    suffix = "" if name is None else PurePosixPath(name).suffix.lower()
    detected = EXTENSIONS.get(suffix)
    if detected is None:
        raise FileRejectedError(RejectionReason.EXTENSION_NOT_ALLOWED)
    return suffix, detected


def fit_crc(data: bytes) -> int:
    """FIT CRC-16 (FIT protocol, CRC section)."""
    crc = 0
    for byte in data:
        for nibble in (byte & 0x0F, byte >> 4):
            low = _FIT_CRC_TABLE[crc & 0x0F]
            crc = ((crc >> 4) & 0x0FFF) ^ low ^ _FIT_CRC_TABLE[nibble]
    return crc


def check_fit(data: bytes) -> None:
    if len(data) < 12 or data[0] not in (12, 14) or data[8:12] != FIT_MARKER:
        raise _mismatch("FIT_HEADER")
    header_size = data[0]
    data_size = int.from_bytes(data[4:8], "little")
    if len(data) < header_size + data_size + 2:
        raise _mismatch("FIT_SIZE")
    if header_size == 14:
        header_crc = int.from_bytes(data[12:14], "little")
        if header_crc != 0 and header_crc != fit_crc(data[:12]):
            raise _mismatch("FIT_HEADER_CRC")


def _skip_until(text: str, start: int, terminator: str) -> int:
    end = text.find(terminator, start)
    if end < 0:
        raise _mismatch("XML_ROOT")
    return end + len(terminator)


def _skip_doctype(text: str, start: int) -> int:
    """End of a document type declaration, internal subset and quoted strings included."""
    position, depth, quote = start, 0, ""
    while position < len(text):
        char = text[position]
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == ">" and depth <= 0:
            return position + 1
        position += 1
    raise _mismatch("XML_ROOT")


def xml_root(data: bytes) -> str:
    """Local name of the root element, from the start of the file only (no parsing)."""
    head = data[:SNIFF_BYTES]
    if head.startswith(UTF8_BOM):
        head = head[len(UTF8_BOM) :]
    try:
        text = codecs.getincrementaldecoder("utf-8")("strict").decode(head, final=False)
    except UnicodeDecodeError:
        raise _mismatch("XML_ENCODING") from None
    declaration = _XML_DECLARATION.match(text.lstrip())
    if declaration is not None:
        encoding = _ENCODING.search(declaration.group(0))
        if encoding is not None and encoding.group(1).lower() not in {"utf-8", "utf8"}:
            raise _mismatch("XML_ENCODING")
    position = 0
    while True:
        position = text.find("<", position)
        if position < 0:
            raise _mismatch("XML_ROOT")
        if text.startswith("<?", position):
            position = _skip_until(text, position, "?>")
        elif text.startswith("<!--", position):
            position = _skip_until(text, position, "-->")
        elif text.startswith("<!", position):
            position = _skip_doctype(text, position)
        else:
            name = _NAME.match(text, position + 1)
            if name is None:
                raise _mismatch("XML_ROOT")
            return name.group(0).rsplit(":", 1)[-1]


def check_csv(data: bytes) -> None:
    if b"\x00" in data:
        raise _mismatch("CSV_NUL")
    body = data[len(UTF8_BOM) :] if data.startswith(UTF8_BOM) else data
    first_line = body.split(b"\n", 1)[0].rstrip(b"\r")
    try:
        header = first_line.decode("utf-8")
    except UnicodeDecodeError:
        raise _mismatch("CSV_ENCODING") from None
    if header not in {template.header for template in TEMPLATES}:
        raise _mismatch("CSV_TEMPLATE")


def detect(filename: str | None, data: bytes) -> DetectedType:
    """Stages 2 and 3: the extension must be allowed and the content must match it."""
    _, detected = extension_type(filename)
    check_content(detected, data)
    return detected


def check_content(detected: DetectedType, data: bytes) -> None:
    if detected is DetectedType.FIT:
        check_fit(data)
    elif detected in XML_ROOTS:
        if xml_root(data) != XML_ROOTS[detected]:
            raise _mismatch("XML_ROOT")
    elif detected is DetectedType.CSV:
        check_csv(data)
    elif detected is DetectedType.ZIP:
        if not data.startswith(ZIP_MAGIC):
            raise _mismatch("ZIP_MAGIC")
    else:  # pragma: no cover - EXTENSIONS lists every supported type
        raise _mismatch("UNSUPPORTED")
