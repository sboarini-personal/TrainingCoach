"""File ingestion pipeline (INT-FIL-001 to INT-FIL-004; spec/integration/files.spec.md).

Runs the stages in order: read with a cap, extension and content, size per format,
quarantine, malware scan, hash and duplicate check, sandboxed parsing, schema and semantic
validation, normalisation (all sessions before anything is stored), then persistence through
the RAW store and the activity service, which deduplicates. Every attempt ends with one
immutable ingestion record; a rejection or failure also opens an IMPORT_FAILED issue.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Final
from uuid import UUID, uuid4

from coach.application.activities import ActivityService
from coach.application.consent import ConsentGate
from coach.application.contracts import TIME_SERIES_CODEC
from coach.application.errors import ApplicationError
from coach.application.events import EventName
from coach.application.files import (
    DecodedSession,
    FileFormats,
    FileRejectedError,
    MalwareScanner,
    Quarantine,
)
from coach.application.ownership import OwnerScope
from coach.application.ports import (
    BlobStore,
    Clock,
    EventSink,
    IdFactory,
    IngestionRecordRepository,
    OwnedRepository,
    RawSourceRepository,
)
from coach.application.raw_storage import RawStorage
from coach.application.screening import SecretScreen
from coach.domain.activity.fields import FieldKind, StreamKind, StreamRef, field_spec
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.activity.observation import Observation, source_kind_of
from coach.domain.common.errors import DomainValidationError
from coach.domain.consent.ledger import ConsentType
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import (
    ActivityOutcome,
    ImportedActivity,
    IngestionOutcome,
    IngestionRecord,
    RejectionReason,
    StoredFile,
    check_idempotency_key,
)
from coach.domain.raw.source import DetectedType, MalwareScan, SourceOrigin

CHUNK: Final = 1024 * 1024
CATEGORY: Final = "ACTIVITY_FILE"
SCREEN_SOURCE: Final = "file_import"


class IdempotencyConflictError(ApplicationError):
    """The idempotency key was already used for different bytes."""

    def __init__(self) -> None:
        super().__init__("idempotency key already used for a different upload")


@dataclass(frozen=True, slots=True)
class ImportResult:
    record: IngestionRecord
    secret_notice: str | None = None


@dataclass(slots=True)
class _File:
    index: int
    detected_type: DetectedType
    data: bytes
    sha256: str
    path: Path
    sessions: list[DecodedSession] = field(default_factory=list)


@dataclass(slots=True)
class _Attempt:
    """What is known about an upload so far; becomes its ingestion record."""

    ingestion_id: UUID
    athlete_id: UUID
    received_at: datetime
    filename: str | None
    mime_type: str | None
    idempotency_key: str | None
    byte_size: int = 0
    sha256: str | None = None
    detected_type: DetectedType = DetectedType.UNKNOWN
    malware_scan: MalwareScan | None = None
    notice: str | None = None


def read_capped(stream: BinaryIO, limit: int) -> tuple[bytes, bool]:
    """Up to ``limit`` bytes; ``False`` when the stream holds more (stage 1)."""
    buffer = bytearray()
    while len(buffer) <= limit:
        chunk = stream.read(min(CHUNK, limit + 1 - len(buffer)))
        if not chunk:
            return bytes(buffer), True
        buffer.extend(chunk)
    return bytes(buffer), False


class IngestionService:
    def __init__(
        self,
        *,
        formats: FileFormats,
        quarantine: Quarantine,
        scanner: MalwareScanner,
        blobs: BlobStore,
        raw_sources: RawSourceRepository,
        records: IngestionRecordRepository,
        activities: ActivityService,
        issues: OwnedRepository[DataQualityIssue],
        consents: ConsentGate,
        screen: SecretScreen,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
        limits: IngestionLimits | None = None,
    ) -> None:
        self._formats = formats
        self._quarantine = quarantine
        self._scanner = scanner
        self._blobs = blobs
        self._raw = RawStorage(blobs, raw_sources, clock=clock, ids=ids)
        self._records = records
        self._activities = activities
        self._issues = issues
        self._consents = consents
        self._screen = screen
        self._events = events
        self._clock = clock
        self._ids = ids
        self.limits = limits or IngestionLimits()

    def import_file(
        self,
        scope: OwnerScope,
        stream: BinaryIO,
        *,
        filename: str | None,
        declared_mime_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> ImportResult:
        """Import one upload; never raises for file problems, which end in the record."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        key = check_idempotency_key(idempotency_key)
        attempt = _Attempt(
            self._ids(), scope.athlete_id, self._clock(), filename, declared_mime_type, key
        )
        data, complete = read_capped(stream, self.limits.max_upload_bytes)
        attempt.byte_size = len(data)
        attempt.sha256 = hashlib.sha256(data).hexdigest() if complete else None
        if key is not None:
            earlier = self._records.find_by_idempotency_key(scope, key)
            if earlier is not None:
                if earlier.sha256 != attempt.sha256:
                    raise IdempotencyConflictError
                return ImportResult(earlier)
        self._events.emit(EventName.INGESTION_RECEIVED, {})
        quarantined: list[Path] = []
        try:
            if not complete:
                raise FileRejectedError(RejectionReason.TOO_LARGE, "STREAM_LIMIT")
            return self._pipeline(scope, attempt, data, quarantined)
        except FileRejectedError as rejection:
            return self._reject(scope, attempt, rejection)
        finally:
            for path in quarantined:
                self._quarantine.discard(path)

    # ------------------------------------------------------------------ stages

    def _pipeline(
        self, scope: OwnerScope, attempt: _Attempt, data: bytes, quarantined: list[Path]
    ) -> ImportResult:
        detected = self._formats.detect(attempt.filename, data)
        if len(data) > self.limits.max_bytes(detected):
            raise FileRejectedError(RejectionReason.TOO_LARGE, "FORMAT_LIMIT")
        attempt.detected_type = detected
        path = self._quarantine.put(data)
        quarantined.append(path)
        attempt.malware_scan = self._scanner.scan(path)
        sha256 = attempt.sha256 or ""
        earlier = self._records.find_imported(scope, sha256)
        if earlier is not None:
            return self._duplicate(scope, attempt, earlier)
        files = self._files(detected, data, sha256, path, quarantined)
        total = 0
        for item in files:
            try:
                item.sessions = list(
                    self._formats.decode(
                        item.path, item.detected_type, self.limits, received_at=attempt.received_at
                    )
                )
                self._check_observations(scope, item)
            except FileRejectedError as rejection:
                if detected is DetectedType.ZIP:
                    raise rejection.in_member(item.index) from None
                raise
            total += len(item.sessions)
            self._events.emit(
                EventName.INGESTION_PARSED,
                {"type": item.detected_type.value, "sessions": str(len(item.sessions))},
            )
        if total > self.limits.activities:
            raise FileRejectedError(RejectionReason.LIMIT_EXCEEDED, "ACTIVITIES")
        for item in files:
            item.sessions = [self._screened(session, attempt) for session in item.sessions]
        return self._persist(scope, attempt, files)

    def _files(
        self,
        detected: DetectedType,
        data: bytes,
        sha256: str,
        path: Path,
        quarantined: list[Path],
    ) -> list[_File]:
        if detected is not DetectedType.ZIP:
            return [_File(0, detected, data, sha256, path)]
        files = []
        for member in self._formats.members(data, self.limits):
            try:
                member_type = self._formats.member_type(member)
            except FileRejectedError as rejection:
                raise rejection.in_member(member.index) from None
            member_path = self._quarantine.put(member.data)
            quarantined.append(member_path)
            files.append(
                _File(
                    member.index,
                    member_type,
                    member.data,
                    hashlib.sha256(member.data).hexdigest(),
                    member_path,
                )
            )
        return files

    def _check_observations(self, scope: OwnerScope, item: _File) -> None:
        """Stage 11: every session must make a valid observation before anything is stored."""
        for session in item.sessions:
            try:
                self._observation(scope, item, session, uuid4(), self._clock(), streams=())
            except DomainValidationError:
                raise FileRejectedError(
                    RejectionReason.NORMALIZATION_FAILED, "OBSERVATION"
                ) from None

    def _screened(self, session: DecodedSession, attempt: _Attempt) -> DecodedSession:
        """Text taken from files is screened for pasted secrets (SEC-SEC-005)."""
        values = dict(session.values)
        for path, value in session.values.items():
            if isinstance(value, str) and field_spec(path).kind is FieldKind.TEXT:
                screened = self._screen.screen(value, source=SCREEN_SOURCE)
                values[path] = screened.text
                attempt.notice = attempt.notice or screened.notice
        return replace(session, values=values)

    # ------------------------------------------------------------------ persistence

    def _observation(
        self,
        scope: OwnerScope,
        item: _File,
        session: DecodedSession,
        raw_source_id: UUID,
        received_at: datetime,
        *,
        streams: tuple[StreamRef, ...],
    ) -> Observation:
        return Observation(
            raw_source_id=raw_source_id,
            part=session.part,
            athlete_id=scope.athlete_id,
            origin=SourceOrigin.FILE_UPLOAD,
            kind=source_kind_of(SourceOrigin.FILE_UPLOAD, item.detected_type),
            raw_sha256=item.sha256,
            received_at=received_at,
            normaliser=session.normaliser,
            normaliser_version=session.normaliser_version,
            values=session.values,
            source_paths=session.source_paths,
            streams=streams,
            anomalies=session.anomalies,
        )

    def _persist(
        self, scope: OwnerScope, attempt: _Attempt, files: Sequence[_File]
    ) -> ImportResult:
        stored_files: list[StoredFile] = []
        imported: list[ImportedActivity] = []
        try:
            for item in files:
                stored = self._raw.store(
                    scope,
                    item.data,
                    origin=SourceOrigin.FILE_UPLOAD,
                    category=CATEGORY,
                    detected_type=item.detected_type,
                    malware_scan=attempt.malware_scan or MalwareScan.NOT_SCANNED,
                    original_filename=attempt.filename if len(files) == 1 else None,
                ).source
                for session in item.sessions:
                    streams: tuple[StreamRef, ...] = ()
                    if session.time_series is not None:
                        document = TIME_SERIES_CODEC.dump(session.time_series)
                        digest = self._blobs.put(scope, _canonical(document))
                        streams = (StreamRef(StreamKind.TIME_SERIES, digest),)
                    observation = self._observation(
                        scope,
                        item,
                        session,
                        stored.raw_source_id,
                        stored.received_at,
                        streams=streams,
                    )
                    result = self._activities.ingest(scope, observation)
                    imported.append(
                        ImportedActivity(
                            result.activity.activity_id,
                            stored.raw_source_id,
                            session.part,
                            ActivityOutcome(result.outcome.value),
                        )
                    )
                stored_files.append(
                    StoredFile(
                        item.index,
                        item.detected_type,
                        len(item.data),
                        item.sha256,
                        stored.raw_source_id,
                        len(item.sessions),
                    )
                )
        except Exception:
            failure = FileRejectedError(RejectionReason.PERSISTENCE_FAILED)
            return self._reject(scope, attempt, failure, failed=True, activities=imported)
        record = self._record(
            attempt,
            IngestionOutcome.IMPORTED,
            files=tuple(stored_files),
            activities=tuple(imported),
        )
        return ImportResult(self._records.add(scope, record), attempt.notice)

    # ------------------------------------------------------------------ outcomes

    def _duplicate(
        self, scope: OwnerScope, attempt: _Attempt, earlier: IngestionRecord
    ) -> ImportResult:
        self._events.emit(EventName.INGESTION_DUPLICATE, {"tier": "1"})
        record = self._record(
            attempt,
            IngestionOutcome.DUPLICATE,
            files=earlier.files,
            activities=tuple(
                replace(item, outcome=ActivityOutcome.DUPLICATE) for item in earlier.activities
            ),
            duplicate_of=earlier.ingestion_id,
        )
        return ImportResult(self._records.add(scope, record))

    def _reject(
        self,
        scope: OwnerScope,
        attempt: _Attempt,
        rejection: FileRejectedError,
        *,
        failed: bool = False,
        activities: Sequence[ImportedActivity] = (),
    ) -> ImportResult:
        outcome = IngestionOutcome.FAILED if failed else IngestionOutcome.REJECTED
        record = self._records.add(
            scope,
            self._record(
                attempt,
                outcome,
                reason=rejection.reason,
                detail=rejection.detail,
                failed_file_index=rejection.member,
                activities=tuple(activities) if failed else (),
            ),
        )
        name = EventName.INGESTION_FAILED if failed else EventName.INGESTION_REJECTED
        fields = {"reason": rejection.reason.value}
        if rejection.detail is not None:
            fields["detail"] = rejection.detail
        self._events.emit(name, fields)
        issue = DataQualityIssue.for_import_failure(
            issue_id=self._ids(),
            athlete_id=scope.athlete_id,
            ingestion_id=record.ingestion_id,
            reason=rejection.reason.value,
            detail=rejection.detail,
            created_at=record.completed_at,
        )
        self._issues.add(scope, issue)
        self._events.emit(EventName.QUALITY_ISSUE_RAISED, {"code": rejection.reason.value})
        return ImportResult(record)

    def _record(
        self,
        attempt: _Attempt,
        outcome: IngestionOutcome,
        *,
        reason: RejectionReason | None = None,
        detail: str | None = None,
        failed_file_index: int | None = None,
        files: tuple[StoredFile, ...] = (),
        activities: tuple[ImportedActivity, ...] = (),
        duplicate_of: UUID | None = None,
    ) -> IngestionRecord:
        return IngestionRecord(
            ingestion_id=attempt.ingestion_id,
            athlete_id=attempt.athlete_id,
            received_at=attempt.received_at,
            completed_at=self._clock(),
            outcome=outcome,
            detected_type=attempt.detected_type,
            byte_size=attempt.byte_size,
            sha256=attempt.sha256,
            malware_scan=attempt.malware_scan,
            original_filename=attempt.filename,
            declared_mime_type=attempt.mime_type,
            reason=reason,
            detail=detail,
            failed_file_index=failed_file_index,
            files=files,
            activities=activities,
            duplicate_of=duplicate_of,
            idempotency_key=attempt.idempotency_key,
        )


def _canonical(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
