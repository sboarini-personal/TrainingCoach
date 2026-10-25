"""Store raw bytes with immutable metadata, idempotently (FR-ACT-002, NFR-REL-001).

The blob is written first; it is content-addressed, so a failure before the metadata is
recorded leaves at most an unreferenced blob and never a record without bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from coach.application.ownership import OwnerScope
from coach.application.ports import BlobStore, Clock, IdFactory, RawSourceRepository
from coach.domain.raw.source import DetectedType, MalwareScan, RawSource, SourceOrigin


@dataclass(frozen=True, slots=True)
class StoredRaw:
    source: RawSource
    created: bool


class RawStorage:
    def __init__(
        self, blobs: BlobStore, sources: RawSourceRepository, *, clock: Clock, ids: IdFactory
    ) -> None:
        self._blobs = blobs
        self._sources = sources
        self._clock = clock
        self._ids = ids

    def store(
        self,
        scope: OwnerScope,
        data: bytes,
        *,
        origin: SourceOrigin,
        category: str,
        detected_type: DetectedType,
        malware_scan: MalwareScan,
        provider: str | None = None,
        provider_object_id: str | None = None,
        original_filename: str | None = None,
    ) -> StoredRaw:
        digest = self._blobs.put(scope, data)
        candidate = RawSource(
            raw_source_id=self._ids(),
            athlete_id=scope.athlete_id,
            origin=origin,
            provider=provider,
            provider_object_id=provider_object_id,
            category=category,
            received_at=self._clock(),
            sha256=digest,
            byte_size=len(data),
            detected_type=detected_type,
            original_filename=original_filename,
            malware_scan=malware_scan,
        )
        source, created = self._sources.add(scope, candidate)
        return StoredRaw(source, created)

    def read(self, scope: OwnerScope, raw_source_id: UUID) -> bytes:
        return self._blobs.get(scope, self._sources.get(scope, raw_source_id).sha256)
