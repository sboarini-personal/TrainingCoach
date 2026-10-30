"""File ingestion pipeline (INT-FIL-001 to INT-FIL-004; spec/integration/files.spec.md).

Runs the stages in order: read with a cap, extension and content, size per format,
quarantine, malware scan, hash and duplicate check, sandboxed parsing, schema and semantic
validation, normalisation (all sessions before anything is stored), then persistence through
the RAW store and the activity service, which deduplicates. Every attempt ends with one
immutable ingestion record; a rejection or failure also opens an IMPORT_FAILED issue.

Provider payloads take the same path (files spec 9.1): the endpoint states their type, they
are not malware-scanned, a JSON record is decoded by the provider's record normaliser, and a
downloaded archive is stored as RAW besides its members.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Final
from uuid import UUID, uuid4

from coach.application.activities import ActivityService, RenormalisationRefusedError
from coach.application.consent import ConsentGate
from coach.application.contracts import (
    LAPS_CODEC,
    LENGTHS_CODEC,
    SETS_CODEC,
    TIME_SERIES_CODEC,
)
from coach.application.errors import ApplicationError, NotFoundError
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
from coach.application.providers import (
    JSON_MEDIA_TYPE,
    ZIP_MEDIA_TYPE,
    ProviderRecords,
    RawPayload,
)
from coach.application.raw_storage import RawStorage
from coach.application.screening import SecretScreen
from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import FieldKind, StreamKind, StreamRef, field_spec
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.activity.observation import (
    Observation,
    SourceKind,
    same_session,
    source_kind_of,
    version_key,
)
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
from coach.domain.raw.source import (
    PROVIDER_OF,
    DetectedType,
    MalwareScan,
    RawSource,
    SourceOrigin,
    provider_origin,
)

CHUNK: Final = 1024 * 1024
CATEGORY: Final = "ACTIVITY_FILE"
SCREEN_SOURCE: Final = "file_import"
_PAYLOAD_TYPES: Final = {JSON_MEDIA_TYPE: DetectedType.JSON, ZIP_MEDIA_TYPE: DetectedType.ZIP}


class IdempotencyConflictError(ApplicationError):
    """The idempotency key was already used for different bytes."""

    def __init__(self) -> None:
        super().__init__("idempotency key already used for a different upload")


@dataclass(frozen=True, slots=True)
class ImportResult:
    record: IngestionRecord
    secret_notice: str | None = None


@dataclass(frozen=True, slots=True)
class ReprocessReport:
    """Counts of a re-normalisation run (activities spec 1.1); raw sources, not files."""

    reprocessed: int
    up_to_date: int
    activities: int
    skipped: Mapping[str, int]


class _Skip(Exception):  # noqa: N818 - control flow: a raw source left as it is
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _outdated(observation: Observation, current: Mapping[str, str]) -> bool:
    version = current.get(observation.normaliser)
    return version is not None and version_key(observation.normaliser_version) < version_key(
        version
    )


@dataclass(slots=True)
class _File:
    index: int
    detected_type: DetectedType
    data: bytes
    sha256: str
    path: Path | None
    sessions: list[DecodedSession] = field(default_factory=list)


@dataclass(slots=True)
class _Attempt:
    """What is known about an import so far; becomes its ingestion record."""

    ingestion_id: UUID
    athlete_id: UUID
    received_at: datetime
    filename: str | None
    mime_type: str | None
    idempotency_key: str | None
    origin: SourceOrigin = SourceOrigin.FILE_UPLOAD
    provider_object_id: str | None = None
    category: str = CATEGORY
    byte_size: int = 0
    sha256: str | None = None
    detected_type: DetectedType = DetectedType.UNKNOWN
    malware_scan: MalwareScan | None = None
    notice: str | None = None

    @property
    def provider(self) -> str | None:
        return PROVIDER_OF.get(self.origin)


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
        provider_records: Mapping[str, ProviderRecords] | None = None,
    ) -> None:
        self._formats = formats
        self._quarantine = quarantine
        self._scanner = scanner
        self._blobs = blobs
        self._raw = RawStorage(blobs, raw_sources, clock=clock, ids=ids)
        self._raw_sources = raw_sources
        self._records = records
        self._activities = activities
        self._issues = issues
        self._consents = consents
        self._screen = screen
        self._events = events
        self._clock = clock
        self._ids = ids
        self.limits = limits or IngestionLimits()
        self._provider_records = dict(provider_records or {})

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

    def import_provider_payload(self, scope: OwnerScope, payload: RawPayload) -> ImportResult:
        """Import a provider record or download (files spec 9.1); problems end in the record."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        detected = _PAYLOAD_TYPES.get(payload.media_type)
        if detected is DetectedType.JSON and payload.provider not in self._provider_records:
            raise LookupError(f"no record normaliser for provider {payload.provider!r}")
        if payload.provider_object_id is None:
            raise DomainValidationError("a provider payload names its object")
        attempt = _Attempt(
            self._ids(),
            scope.athlete_id,
            self._clock(),
            None,
            payload.media_type,
            None,
            origin=provider_origin(payload.provider),
            provider_object_id=payload.provider_object_id,
            category=payload.category,
            byte_size=len(payload.data),
            sha256=hashlib.sha256(payload.data).hexdigest(),
            detected_type=detected or DetectedType.UNKNOWN,
            malware_scan=MalwareScan.NOT_APPLICABLE,
        )
        self._events.emit(EventName.INGESTION_RECEIVED, {})
        # Every provider response is kept unmodified before it is examined (INT-GAR-009), so a
        # rejected payload can be reprocessed once the rules learn its shape.
        self._raw.store(
            scope,
            payload.data,
            origin=attempt.origin,
            category=payload.category,
            detected_type=self._stored_type(detected, payload.data),
            malware_scan=MalwareScan.NOT_APPLICABLE,
            provider=payload.provider,
            provider_object_id=payload.provider_object_id,
            request_window=payload.window,
        )
        quarantined: list[Path] = []
        try:
            if detected is None:
                raise FileRejectedError(RejectionReason.CONTENT_MISMATCH, "MEDIA_TYPE")
            return self._pipeline(scope, attempt, payload.data, quarantined)
        except FileRejectedError as rejection:
            return self._reject(scope, attempt, rejection)
        finally:
            for path in quarantined:
                self._quarantine.discard(path)

    def reject_provider_payload(
        self,
        scope: OwnerScope,
        *,
        provider: str,
        provider_object_id: str,
        media_type: str,
        rejection: FileRejectedError,
    ) -> ImportResult:
        """Record a provider payload that never arrived whole (for example, over the size cap)."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        attempt = _Attempt(
            self._ids(),
            scope.athlete_id,
            self._clock(),
            None,
            media_type,
            None,
            origin=provider_origin(provider),
            provider_object_id=provider_object_id,
            detected_type=_PAYLOAD_TYPES.get(media_type, DetectedType.UNKNOWN),
            malware_scan=MalwareScan.NOT_APPLICABLE,
        )
        return self._reject(scope, attempt, rejection)

    # ------------------------------------------------------------------ re-normalisation

    def reprocess(
        self, scope: OwnerScope, *, progress: Callable[[int, int], None] | None = None
    ) -> ReprocessReport:
        """Re-normalise the RAW sources whose observations an older normaliser produced
        (activities spec 1.1). RAW never changes; a source that cannot be redone is skipped.
        ``progress`` receives (sources done, sources to do) after each one."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        current = self._formats.normaliser_versions()
        by_source: dict[UUID, list[Observation]] = {}
        for observation in self._activities.observations(scope):
            by_source.setdefault(observation.raw_source_id, []).append(observation)
        reprocessed = 0
        rebuilt: set[UUID] = set()
        skipped: Counter[str] = Counter()
        outdated = [
            raw_source_id
            for raw_source_id in sorted(by_source, key=str)
            if any(_outdated(observation, current) for observation in by_source[raw_source_id])
        ]
        for done, raw_source_id in enumerate(outdated, start=1):
            stored = sorted(by_source[raw_source_id], key=lambda item: item.part)
            try:
                activities = self._reprocess_source(scope, raw_source_id, stored)
                rebuilt.update(activity.activity_id for activity in activities)
                reprocessed += 1
            except _Skip as skip:
                skipped[skip.reason] += 1
            if progress is not None:
                progress(done, len(outdated))
        up_to_date = len(by_source) - len(outdated)
        self._events.emit(
            EventName.ACTIVITY_REPROCESSED,
            {"reprocessed": str(reprocessed), "skipped": str(sum(skipped.values()))},
        )
        return ReprocessReport(reprocessed, up_to_date, len(rebuilt), dict(skipped))

    def _reprocess_source(
        self, scope: OwnerScope, raw_source_id: UUID, stored: Sequence[Observation]
    ) -> list[Activity]:
        try:
            source = self._raw_sources.get(scope, raw_source_id)
            data = self._raw.read(scope, raw_source_id)
        except (NotFoundError, RuntimeError):  # missing, or no longer matching its hash
            raise _Skip("RAW_UNAVAILABLE") from None
        path = self._quarantine.put(data)
        try:
            sessions = self._formats.decode(
                path, source.detected_type, self.limits, received_at=source.received_at
            )
        except FileRejectedError:
            raise _Skip("REJECTED") from None
        finally:
            self._quarantine.discard(path)
        if sorted(session.part for session in sessions) != [item.part for item in stored]:
            raise _Skip("SESSIONS_CHANGED")
        screened = [self._screen_texts(session)[0] for session in sessions]
        origin = _Source.of(source)
        by_part = {item.part: item for item in stored}
        try:
            trial = [self._observation(scope, origin, session, streams=()) for session in screened]
        except DomainValidationError:
            raise _Skip("REJECTED") from None
        if any(not same_session(new, by_part[new.part]) for new in trial):
            raise _Skip("IDENTITY_CHANGED")  # checked before any stream blob is written
        observations = [
            self._observation(scope, origin, session, streams=self._streams(scope, session))
            for session in screened
        ]
        try:
            return self._activities.renormalise(scope, observations)
        except RenormalisationRefusedError as refused:
            raise _Skip(refused.reason) from None

    def _stored_type(self, detected: DetectedType | None, data: bytes) -> DetectedType:
        if detected is DetectedType.ZIP:
            try:
                self._formats.check_content(DetectedType.ZIP, data)
            except FileRejectedError:
                return DetectedType.UNKNOWN
        return detected or DetectedType.UNKNOWN

    # ------------------------------------------------------------------ stages

    def _pipeline(
        self, scope: OwnerScope, attempt: _Attempt, data: bytes, quarantined: list[Path]
    ) -> ImportResult:
        uploaded = attempt.origin is SourceOrigin.FILE_UPLOAD
        if uploaded:
            detected = self._formats.detect(attempt.filename, data)
        else:
            detected = attempt.detected_type
            if detected is not DetectedType.JSON:
                self._formats.check_content(detected, data)
        if len(data) > self.limits.max_bytes(detected):
            raise FileRejectedError(RejectionReason.TOO_LARGE, "FORMAT_LIMIT")
        attempt.detected_type = detected
        path = None
        if detected is not DetectedType.JSON:
            path = self._quarantine.put(data)
            quarantined.append(path)
        if uploaded and path is not None:
            attempt.malware_scan = self._scanner.scan(path)
        sha256 = attempt.sha256 or ""
        earlier = self._records.find_imported(scope, sha256)
        if earlier is not None:
            return self._duplicate(scope, attempt, earlier)
        files = self._files(attempt, detected, data, sha256, path, quarantined)
        total = 0
        for item in files:
            try:
                item.sessions = list(self._decode(attempt, item))
                self._check_observations(scope, attempt, item)
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
        attempt: _Attempt,
        detected: DetectedType,
        data: bytes,
        sha256: str,
        path: Path | None,
        quarantined: list[Path],
    ) -> list[_File]:
        if detected is not DetectedType.ZIP:
            return [_File(0, detected, data, sha256, path)]
        files = []
        for member in self._formats.members(data, self.limits):
            try:
                member_type = self._formats.member_type(member)
                if (
                    member_type is DetectedType.CSV
                    and attempt.origin is not SourceOrigin.FILE_UPLOAD
                ):
                    # Only the athlete's own files are athlete entries (files spec 9.1).
                    raise FileRejectedError(RejectionReason.CONTENT_MISMATCH, "PROVIDER_CSV")
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

    def _decode(self, attempt: _Attempt, item: _File) -> Sequence[DecodedSession]:
        if item.detected_type is DetectedType.JSON:
            records = self._provider_records[attempt.provider or ""]
            return records.decode(
                item.data,
                provider_object_id=attempt.provider_object_id or "",
                received_at=attempt.received_at,
            )
        if item.path is None:  # pragma: no cover - every file but a JSON record is quarantined
            raise FileRejectedError(RejectionReason.PARSE_FAILED)
        return self._formats.decode(
            item.path, item.detected_type, self.limits, received_at=attempt.received_at
        )

    def _check_observations(self, scope: OwnerScope, attempt: _Attempt, item: _File) -> None:
        """Stage 11: every session must make a valid observation before anything is stored."""
        trial = _Source(
            raw_source_id=uuid4(),
            origin=attempt.origin,
            detected_type=item.detected_type,
            provider=attempt.provider,
            provider_object_id=attempt.provider_object_id,
            sha256=item.sha256,
            received_at=self._clock(),
        )
        for session in item.sessions:
            try:
                self._observation(scope, trial, session, streams=())
            except DomainValidationError:
                raise FileRejectedError(
                    RejectionReason.NORMALIZATION_FAILED, "OBSERVATION"
                ) from None

    def _screened(self, session: DecodedSession, attempt: _Attempt) -> DecodedSession:
        screened, notice = self._screen_texts(session)
        attempt.notice = attempt.notice or notice
        return screened

    def _screen_texts(self, session: DecodedSession) -> tuple[DecodedSession, str | None]:
        """Text taken from files is screened for pasted secrets (SEC-SEC-005)."""
        values = dict(session.values)
        notice = None
        for path, value in session.values.items():
            if isinstance(value, str) and field_spec(path).kind is FieldKind.TEXT:
                screened = self._screen.screen(value, source=SCREEN_SOURCE)
                values[path] = screened.text
                notice = notice or screened.notice
        return replace(session, values=values), notice

    # ------------------------------------------------------------------ persistence

    def _observation(
        self,
        scope: OwnerScope,
        source: _Source,
        session: DecodedSession,
        *,
        streams: tuple[StreamRef, ...],
    ) -> Observation:
        """The observation of a stored source; its origin is the source's, not the attempt's."""
        kind = source_kind_of(source.origin, source.detected_type)
        record = kind is SourceKind.PROVIDER_RECORD
        return Observation(
            raw_source_id=source.raw_source_id,
            part=session.part,
            athlete_id=scope.athlete_id,
            origin=source.origin,
            kind=kind,
            raw_sha256=source.sha256,
            received_at=source.received_at,
            normaliser=session.normaliser,
            normaliser_version=session.normaliser_version,
            values=session.values,
            provider=source.provider if record else None,
            provider_activity_id=source.provider_object_id if record else None,
            source_paths=session.source_paths,
            streams=streams,
            anomalies=session.anomalies,
        )

    def _streams(self, scope: OwnerScope, session: DecodedSession) -> tuple[StreamRef, ...]:
        """Stream blobs of a session: time series, laps, lengths and strength sets (files
        spec 8.5 to 8.7)."""
        documents: list[tuple[StreamKind, dict[str, object]]] = []
        if session.time_series is not None:
            documents.append((StreamKind.TIME_SERIES, TIME_SERIES_CODEC.dump(session.time_series)))
        if session.laps is not None:
            documents.append((StreamKind.LAPS, LAPS_CODEC.dump(session.laps)))
        if session.lengths is not None:
            documents.append((StreamKind.LENGTHS, LENGTHS_CODEC.dump(session.lengths)))
        if session.sets is not None:
            documents.append((StreamKind.SETS, SETS_CODEC.dump(session.sets)))
        return tuple(
            StreamRef(kind, self._blobs.put(scope, _canonical(document)))
            for kind, document in documents
        )

    def _store(
        self, scope: OwnerScope, attempt: _Attempt, item: _File, *, single: bool
    ) -> RawSource:
        # A provider record keeps its endpoint category; files are activity files.
        record = item.detected_type is DetectedType.JSON
        return self._raw.store(
            scope,
            item.data,
            origin=attempt.origin,
            category=attempt.category if record else CATEGORY,
            detected_type=item.detected_type,
            malware_scan=attempt.malware_scan or MalwareScan.NOT_SCANNED,
            provider=attempt.provider,
            provider_object_id=attempt.provider_object_id,
            original_filename=attempt.filename if single else None,
        ).source

    def _persist(
        self, scope: OwnerScope, attempt: _Attempt, files: Sequence[_File]
    ) -> ImportResult:
        stored_files: list[StoredFile] = []
        imported: list[ImportedActivity] = []
        try:
            for item in files:
                stored = self._store(scope, attempt, item, single=len(files) == 1)
                for session in item.sessions:
                    observation = self._observation(
                        scope, _Source.of(stored), session, streams=self._streams(scope, session)
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
            origin=attempt.origin,
            provider=attempt.provider,
            provider_object_id=attempt.provider_object_id,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Source:
    """What an observation needs to know about its RawSource (stored, or about to be)."""

    raw_source_id: UUID
    origin: SourceOrigin
    detected_type: DetectedType
    provider: str | None
    provider_object_id: str | None
    sha256: str
    received_at: datetime

    @classmethod
    def of(cls, source: RawSource) -> _Source:
        return cls(
            raw_source_id=source.raw_source_id,
            origin=source.origin,
            detected_type=source.detected_type,
            provider=source.provider,
            provider_object_id=source.provider_object_id,
            sha256=source.sha256,
            received_at=source.received_at,
        )


def _canonical(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
