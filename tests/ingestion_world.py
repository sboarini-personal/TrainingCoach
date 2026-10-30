"""Ingestion service wiring for tests: in-memory stores, file quarantine and blob store.

``InProcessRunner`` runs the sandbox worker's request handler in this process (fast unit
tests); integration and security tests use the real process sandbox.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from activity_world import SCOPE, World
from coach.application.files import FileFormats
from coach.application.ingestion import ImportResult, IngestionService
from coach.application.providers import ProviderRecords
from coach.application.screening import SecretScreen
from coach.domain.ingestion.limits import IngestionLimits
from coach.infrastructure.blobs import FileBlobStore
from coach.infrastructure.memory import InMemoryIngestionRecordRepository
from coach.infrastructure.quarantine import FileQuarantine, UnavailableMalwareScanner
from coach.integrations.files import worker
from coach.integrations.files.formats import LocalFileFormats, ParserRunner


class InProcessRunner:
    def run(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float,
        memory_bytes: int,
        max_output_bytes: int,
    ) -> dict[str, Any]:
        response = worker.handle(json.dumps(dict(request)).encode())
        # The real sandbox returns JSON text; keep the same round trip.
        decoded: dict[str, Any] = json.loads(json.dumps(response))
        return decoded


class IngestionWorld(World):
    def __init__(
        self,
        root: Path,
        *,
        runner: ParserRunner | None = None,
        formats: FileFormats | None = None,
        limits: IngestionLimits | None = None,
        consent: bool = True,
        provider_records: Mapping[str, ProviderRecords] | None = None,
    ) -> None:
        super().__init__(consent=consent)
        self.records = InMemoryIngestionRecordRepository()
        self.quarantine_dir = root / "quarantine"
        self.raw_dir = root / "raw"
        self.blobs = FileBlobStore(self.raw_dir)
        self.scanner = UnavailableMalwareScanner()
        self.ingestion = IngestionService(
            formats=formats or LocalFileFormats(runner or InProcessRunner()),
            quarantine=FileQuarantine(self.quarantine_dir),
            scanner=self.scanner,
            blobs=self.blobs,
            raw_sources=self.raw,
            records=self.records,
            activities=self.service,
            issues=self.issues,
            consents=self.consents_gate,
            screen=SecretScreen(self.events),
            events=self.events,
            clock=self.clock,
            ids=self.ids,
            limits=limits,
            provider_records=provider_records,
        )

    def upload(self, data: bytes, filename: str = "run.fit", **options: Any) -> ImportResult:
        return self.ingestion.import_file(SCOPE, io.BytesIO(data), filename=filename, **options)

    def stored_blobs(self) -> list[Path]:
        return sorted(path for path in self.raw_dir.rglob("*") if path.is_file())

    def quarantined(self) -> list[Path]:
        if not self.quarantine_dir.exists():
            return []
        return sorted(self.quarantine_dir.iterdir())
