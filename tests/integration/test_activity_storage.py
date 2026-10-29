"""AT-RAW-001 end to end on SQLite and the file blob store: corrections never change raw data."""

import itertools
from pathlib import Path
from uuid import UUID

import pytest

from coach.application.activities import ActivityService, ActivityStores, IngestOutcome
from coach.application.consent import ConsentGate, ConsentService
from coach.application.contracts import (
    ACTIVITY_CODEC,
    CONSENT_LEDGER_CODEC,
    DATA_QUALITY_ISSUE_CODEC,
)
from coach.application.derived_metrics import DerivedMetricService
from coach.application.errors import NotFoundError
from coach.application.identity import Principal
from coach.application.ownership import OwnerScope
from coach.application.raw_storage import RawStorage
from coach.application.screening import SecretScreen
from coach.domain.activity.observation import Observation, SourceKey, SourceKind
from coach.domain.consent.ledger import ConsentType
from coach.domain.raw.source import DetectedType, MalwareScan, RawSource, SourceOrigin
from coach.infrastructure.blobs import FileBlobStore
from coach.infrastructure.events import LoggingEventSink
from coach.infrastructure.sqlite.activities import (
    SqliteActivityRepository,
    SqliteObservationRepository,
    SqliteOverrideRepository,
)
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from coach.infrastructure.sqlite.audit import SqliteAuditLog
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.derived_values import SqliteDerivedValueRepository
from coach.infrastructure.sqlite.raw_sources import SqliteRawSourceRepository
from coach.metrics.catalog import CATALOG
from coach.metrics.derived import DerivedStatus, MetricSubject
from samples import ATHLETE, BUENOS_AIRES, NOW, USER, sample_observation

SCOPE = OwnerScope(ATHLETE)
FIT_BYTES = b"synthetic FIT bytes, not a real file"


class Installation:
    def __init__(self, directory: Path) -> None:
        self.database = SqliteDatabase.open(directory / "coach.sqlite3")
        counter = itertools.count(1)
        ids = lambda: UUID(int=next(counter))  # noqa: E731 - deterministic test ids
        clock = lambda: NOW  # noqa: E731
        self.raw = SqliteRawSourceRepository(self.database)
        self.storage = RawStorage(FileBlobStore(directory / "raw"), self.raw, clock=clock, ids=ids)
        self.activities = SqliteActivityRepository(self.database)
        self.observations = SqliteObservationRepository(self.database)
        self.derived = SqliteDerivedValueRepository(self.database)
        audit = SqliteAuditLog(self.database)
        ledgers = SqliteAggregateRepository(self.database, CONSENT_LEDGER_CODEC)
        events = LoggingEventSink()
        ConsentService(
            ledgers, policy_version="1.0", audit=audit, events=events, clock=clock, ids=ids
        ).grant(SCOPE, ConsentType.ACTIVITIES, purpose="Analyse my activities.")
        self.service = ActivityService(
            ActivityStores(
                self.activities,
                self.observations,
                SqliteOverrideRepository(self.database),
                SqliteAggregateRepository(self.database, DATA_QUALITY_ISSUE_CODEC),
                self.raw,
            ),
            consents=ConsentGate(ledgers, policy_version="1.0"),
            metrics=DerivedMetricService(CATALOG, self.derived, clock=clock, ids=ids),
            timezone_of=lambda _: BUENOS_AIRES,
            audit=audit,
            events=events,
            clock=clock,
            ids=ids,
            screen=SecretScreen(events),
            batch=self.database.batch,
        )


def _observation_for(raw: RawSource) -> Observation:
    return sample_observation(
        SourceKind.FIT_FILE,
        raw_source_id=raw.raw_source_id,
        sha256=raw.sha256,
        received_at=raw.received_at,
    )


@pytest.mark.req("FR-ACT-002", "FR-ACT-006", "PRIV-ACC-002", "INT-GAR-009", "FR-MET-004")
@pytest.mark.acceptance("AT-RAW-001")
def test_corrections_never_change_raw_or_normalized_data(tmp_path: Path) -> None:
    installation = Installation(tmp_path)
    stored = installation.storage.store(
        SCOPE,
        FIT_BYTES,
        origin=SourceOrigin.FILE_UPLOAD,
        category="ACTIVITY_FILE",
        detected_type=DetectedType.FIT,
        malware_scan=MalwareScan.NOT_SCANNED,
        original_filename="run.fit",
    )
    result = installation.service.ingest(SCOPE, _observation_for(stored.source))
    assert result.outcome is IngestOutcome.CREATED
    activity_id = result.activity.activity_id
    normalized_before = ACTIVITY_CODEC.dump(installation.activities.get(SCOPE, activity_id))
    observation_before = installation.observations.get(
        SCOPE, SourceKey(stored.source.raw_source_id)
    )

    principal = Principal(USER)
    override = installation.service.correct(
        SCOPE, principal, activity_id, "distance.meters", 10000.0, "Measured course"
    ).override
    installation.service.correct(SCOPE, principal, activity_id, "subjective.rpe", 6.0, "Forgot")

    assert installation.storage.read(SCOPE, stored.source.raw_source_id) == FIT_BYTES
    assert installation.raw.get(SCOPE, stored.source.raw_source_id) == stored.source
    assert ACTIVITY_CODEC.dump(installation.activities.get(SCOPE, activity_id)) == normalized_before
    assert (
        installation.observations.get(SCOPE, SourceKey(stored.source.raw_source_id))
        == observation_before
    )

    reopened = Installation(tmp_path)
    view = reopened.service.effective(SCOPE, activity_id)
    assert view.activity.number("distance.meters") == 10000.0
    assert view.normalized.number("distance.meters") == 10050.5
    recorded = view.overrides[0]
    assert recorded == override
    assert (recorded.original_value, recorded.corrected_value, recorded.reason) == (
        10050.5,
        10000.0,
        "Measured course",
    )
    assert (recorded.corrected_at, recorded.corrected_by) == (NOW, USER)

    history = reopened.derived.history(SCOPE, "any.srpe_load", MetricSubject.activity(activity_id))
    assert [(value.status, value.is_current) for value in history] == [
        (DerivedStatus.MISSING_INPUT, False),
        (DerivedStatus.COMPUTED, True),
    ]
    assert history[-1].value == pytest.approx(6.0 * 3720 / 60)
    assert view.activity.number("training.load.internal.sessionRpeLoad") == history[-1].value


@pytest.mark.req("NFR-REL-001", "FR-ACT-007")
def test_an_ingest_that_fails_part_way_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = Installation(tmp_path)
    stored = installation.storage.store(
        SCOPE,
        FIT_BYTES,
        origin=SourceOrigin.FILE_UPLOAD,
        category="ACTIVITY_FILE",
        detected_type=DetectedType.FIT,
        malware_scan=MalwareScan.NOT_SCANNED,
    )

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("metrics failed after the activity was saved")

    monkeypatch.setattr(DerivedMetricService, "compute", broken)
    with pytest.raises(RuntimeError):
        installation.service.ingest(SCOPE, _observation_for(stored.source))
    assert installation.activities.list_all(SCOPE) == []
    with pytest.raises(NotFoundError):
        installation.observations.get(SCOPE, SourceKey(stored.source.raw_source_id))
    monkeypatch.undo()
    result = installation.service.ingest(SCOPE, _observation_for(stored.source))
    assert result.outcome is IngestOutcome.CREATED  # a retry starts from a clean state
