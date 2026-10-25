"""AT-UPL-001, AT-XXE-001, AT-ZIP-001 end to end: real sandbox, SQLite and blob files.

Every rejection leaves only the ingestion record and its issue; quarantine and the blob store
stay empty; hostile XML causes no file or network access.
"""

import io
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from coach.application.consent import ConsentGate
from coach.application.ids import new_id
from coach.application.ingestion import IngestionService
from coach.application.ownership import OwnerScope
from coach.application.screening import SecretScreen
from coach.cli.runtime import PRIVACY_POLICY_VERSION, Runtime, build_runtime
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.common.provenance import FieldValue
from coach.domain.consent.ledger import ConsentType
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import IngestionOutcome, IngestionRecord, RejectionReason
from coach.infrastructure.blobs import FileBlobStore
from coach.infrastructure.datadir import ensure_layout
from coach.infrastructure.events import LoggingEventSink
from coach.infrastructure.memory import InMemoryCredentialProvider
from coach.infrastructure.quarantine import FileQuarantine, UnavailableMalwareScanner
from coach.infrastructure.sqlite.raw_sources import SqliteRawSourceRepository
from coach.integrations.files.formats import LocalFileFormats
from coach.integrations.files.sandbox import ParserSandbox
from file_samples import fit_file, gpx_file, tcx_file, zip_file
from samples import ATHLETE

NOW: Final = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
SENTINEL: Final = "SENTINEL-LOCAL-FILE-CONTENT-7f3a"


class Installation:
    def __init__(self, root: Path, limits: IngestionLimits | None = None) -> None:
        self.layout = ensure_layout(root / "data")
        self.runtime: Runtime = build_runtime(
            self.layout, InMemoryCredentialProvider(), lambda: NOW
        )
        self.scope = OwnerScope(ATHLETE)
        runtime = self.runtime
        runtime.consents.grant(self.scope, ConsentType.ACTIVITIES, purpose="Analyse my activities.")
        profile = AthleteProfile.new(self.scope.athlete_id).apply(
            ProfileUpdate(timezone=FieldValue.declared("America/Argentina/Buenos_Aires", at=NOW))
        )
        runtime.profiles.add(self.scope, profile)
        self.ingestion = IngestionService(
            formats=LocalFileFormats(ParserSandbox(self.layout.quarantine)),
            quarantine=FileQuarantine(self.layout.quarantine),
            scanner=UnavailableMalwareScanner(),
            blobs=FileBlobStore(self.layout.raw),
            raw_sources=SqliteRawSourceRepository(runtime.database),
            records=runtime.ingestion_records,
            activities=runtime.activities,
            issues=runtime.issue_store,
            consents=ConsentGate(runtime.consents.ledgers, policy_version=PRIVACY_POLICY_VERSION),
            screen=SecretScreen(LoggingEventSink()),
            events=LoggingEventSink(),
            clock=lambda: NOW,
            ids=new_id,
            limits=limits,
        )

    def upload(self, data: bytes, filename: str) -> IngestionRecord:
        return self.ingestion.import_file(self.scope, io.BytesIO(data), filename=filename).record

    def count(self, table: str, where: str = "1 = 1") -> int:
        with self.runtime.database.read() as connection:
            row = connection.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()  # noqa: S608 - fixed test SQL
        return int(row[0])

    def files_under(self, directory: Path) -> list[Path]:
        return [path for path in directory.rglob("*") if path.is_file()]

    def assert_only_the_record_and_issue(self) -> None:
        assert self.count("raw_sources") == 0
        assert self.count("source_observations") == 0
        assert self.count("activities") == 0
        assert self.count("ingestion_records") == 1
        assert self.count("aggregates", "kind = 'data_quality_issue'") == 1
        assert self.files_under(self.layout.raw) == []
        assert self.files_under(self.layout.quarantine) == []


@pytest.fixture
def installation(tmp_path: Path) -> Installation:
    return Installation(tmp_path)


# --------------------------------------------------------------------------- AT-UPL-001


@pytest.mark.req("INT-FIL-002", "INT-FIL-003", "INT-FIL-004", "SEC-UPL-001", "SEC-UPL-004")
@pytest.mark.acceptance("AT-UPL-001")
@pytest.mark.parametrize(
    ("data", "filename", "limits", "reason"),
    [
        (fit_file(), "big.fit", IngestionLimits(fit_bytes=512), RejectionReason.TOO_LARGE),
        (fit_file(), "many.fit", IngestionLimits(records=100), RejectionReason.LIMIT_EXCEEDED),
        (gpx_file(), "gpx-inside.fit", None, RejectionReason.CONTENT_MISMATCH),
        (
            fit_file(),
            "slow.fit",
            IngestionLimits(parser_timeout_seconds=0.001),
            RejectionReason.PARSE_TIMEOUT,
        ),
    ],
)
def test_rejected_uploads_persist_only_their_record(
    tmp_path: Path,
    data: bytes,
    filename: str,
    limits: IngestionLimits | None,
    reason: RejectionReason,
) -> None:
    installation = Installation(tmp_path, limits)
    record = installation.upload(data, filename)
    assert (record.outcome, record.reason) == (IngestionOutcome.REJECTED, reason)
    installation.assert_only_the_record_and_issue()


@pytest.mark.req("SEC-UPL-005", "INT-FIL-001")
@pytest.mark.acceptance("AT-UPL-001")
def test_files_are_stored_by_hash_never_by_their_name(installation: Installation) -> None:
    record = installation.upload(fit_file(), "..\\..\\Windows\\System32\\run.fit")
    assert record.outcome is IngestionOutcome.IMPORTED
    (stored,) = record.files
    blobs = installation.files_under(installation.layout.raw)
    assert stored.sha256 in {path.name for path in blobs}
    assert all("run" not in path.name and "System32" not in str(path) for path in blobs)
    assert record.original_filename == "run.fit"
    assert installation.files_under(installation.layout.quarantine) == []


# --------------------------------------------------------------------------- AT-XXE-001


def _hostile_gpx(prolog: str, name: str = "x") -> bytes:
    return gpx_file(prolog=prolog, name=name)


@pytest.mark.req("SEC-UPL-002")
@pytest.mark.acceptance("AT-XXE-001")
def test_external_entity_is_rejected_without_reading_the_file(
    tmp_path: Path, installation: Installation
) -> None:
    sentinel = tmp_path / "secret.txt"
    sentinel.write_text(SENTINEL, encoding="utf-8")
    prolog = f'<!DOCTYPE gpx [<!ENTITY xxe SYSTEM "{sentinel.as_uri()}">]>'
    for data, name in (
        (_hostile_gpx(prolog, "&xxe;"), "xxe.gpx"),
        (
            tcx_file(prolog=prolog.replace("gpx", "TrainingCenterDatabase"), notes="&xxe;"),
            "xxe.tcx",
        ),
    ):
        record = installation.upload(data, name)
        assert (record.reason, record.detail) == (RejectionReason.PARSE_FAILED, "XML_DTD_FORBIDDEN")
    database = installation.layout.database.read_bytes()
    assert SENTINEL.encode() not in database
    assert all(
        SENTINEL.encode() not in path.read_bytes()
        for path in installation.files_under(installation.layout.root)
    )


@pytest.mark.req("SEC-UPL-002")
@pytest.mark.acceptance("AT-XXE-001")
def test_billion_laughs_is_rejected(installation: Installation) -> None:
    entities = '<!ENTITY lol "lol">' + "".join(
        f'<!ENTITY lol{level} "{f"&lol{level - 1};" * 10 if level > 1 else "&lol;" * 10}">'
        for level in range(1, 10)
    )
    record = installation.upload(
        _hostile_gpx(f"<!DOCTYPE gpx [{entities}]>", "&lol9;"), "laughs.gpx"
    )
    assert (record.reason, record.detail) == (RejectionReason.PARSE_FAILED, "XML_DTD_FORBIDDEN")
    installation.assert_only_the_record_and_issue()


@pytest.mark.req("SEC-UPL-002")
@pytest.mark.acceptance("AT-XXE-001")
def test_remote_dtd_is_never_fetched(installation: Installation) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(0.5)
        port = server.getsockname()[1]
        prolog = f'<!DOCTYPE gpx SYSTEM "http://127.0.0.1:{port}/evil.dtd">'
        record = installation.upload(_hostile_gpx(prolog), "remote.gpx")
        assert record.reason is RejectionReason.PARSE_FAILED
        with pytest.raises(TimeoutError):
            server.accept()


# --------------------------------------------------------------------------- AT-ZIP-001


@pytest.mark.req("SEC-UPL-003")
@pytest.mark.acceptance("AT-ZIP-001")
@pytest.mark.parametrize(
    ("members", "reason", "detail"),
    [
        ({"bomb.fit": b"\x00" * (20 * 1024 * 1024)}, RejectionReason.LIMIT_EXCEEDED, "ZIP_RATIO"),
        ({"../escape.fit": fit_file()}, RejectionReason.CONTENT_MISMATCH, "ZIP_PATH"),
        (
            {"inner.zip": zip_file({"a.fit": fit_file()})},
            RejectionReason.CONTENT_MISMATCH,
            "ZIP_NESTED",
        ),
        (
            {f"run{index}.fit": b"x" for index in range(51)},
            RejectionReason.LIMIT_EXCEEDED,
            "ZIP_MEMBERS",
        ),
    ],
)
def test_hostile_archives_are_rejected_in_memory(
    installation: Installation, members: dict[str, bytes], reason: RejectionReason, detail: str
) -> None:
    record = installation.upload(zip_file(members), "export.zip")
    assert (record.outcome, record.reason, record.detail) == (
        IngestionOutcome.REJECTED,
        reason,
        detail,
    )
    installation.assert_only_the_record_and_issue()
