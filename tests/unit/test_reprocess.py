"""Re-normalisation of imported files (activities spec 1.1, ADR-029).

Files are imported with what normalisation rules 1.0.0 produced (P8: no laps, lengths or
power sensor), then re-normalised with the current rules.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from activity_world import SCOPE
from coach.application.consent import ConsentRequiredError
from coach.application.events import EventName
from coach.application.files import ArchiveMember, DecodedSession, FileRejectedError
from coach.application.identity import Principal
from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import PowerSensor, StreamKind
from coach.domain.consent.ledger import ConsentType
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import RejectionReason
from coach.domain.raw.source import DetectedType
from coach.integrations.files.formats import LocalFileFormats
from file_samples import START, fit_device, fit_file, fit_lap, fit_length, fit_session
from ingestion_world import IngestionWorld, InProcessRunner
from samples import USER

PRINCIPAL = Principal(USER)


class Formats:
    """The file adapter as rules 1.0.0 (P8) behaved until ``upgrade``."""

    def __init__(self) -> None:
        self.inner = LocalFileFormats(InProcessRunner())
        self.legacy = True
        self.legacy_start_shift = timedelta()
        self.legacy_sessions: int | None = None
        self.reject = False

    def upgrade(self) -> None:
        self.legacy = False

    def detect(self, filename: str | None, data: bytes) -> DetectedType:
        return self.inner.detect(filename, data)

    def check_content(self, detected_type: DetectedType, data: bytes) -> None:
        self.inner.check_content(detected_type, data)

    def members(self, data: bytes, limits: IngestionLimits) -> Sequence[ArchiveMember]:
        return self.inner.members(data, limits)

    def member_type(self, member: ArchiveMember) -> DetectedType:
        return self.inner.member_type(member)

    def normaliser_versions(self) -> Mapping[str, str]:
        current = self.inner.normaliser_versions()
        return dict.fromkeys(current, "1.0.0") if self.legacy else current

    def decode(
        self,
        path: Path,
        detected_type: DetectedType,
        limits: IngestionLimits,
        *,
        received_at: datetime,
    ) -> Sequence[DecodedSession]:
        if self.reject and not self.legacy:
            raise FileRejectedError(RejectionReason.PARSE_FAILED, "FIT_DECODE")
        sessions = self.inner.decode(path, detected_type, limits, received_at=received_at)
        if not self.legacy:
            return sessions
        legacy = []
        for session in list(sessions)[: self.legacy_sessions]:
            values = {
                path: value
                for path, value in session.values.items()
                if path != "performance.powerSensor"
            }
            start = values["timing.startTimeUtc"]
            assert isinstance(start, datetime)
            values["timing.startTimeUtc"] = start + self.legacy_start_shift
            legacy.append(
                replace(session, values=values, normaliser_version="1.0.0", laps=None, lengths=None)
            )
        return legacy


def ride_with_laps() -> bytes:
    records, summary = fit_session(
        sport="cycling", sub_sport="road", distance_per_second=8.0, extra={"avg_power": 220}
    )
    laps = [
        fit_lap(START, 300, 2400, power=230),
        fit_lap(START + timedelta(seconds=300), 300, 2400, power=210),
    ]
    return fit_file([([*records, *laps, fit_device(11)], summary)])


def pool_swim() -> bytes:
    records, summary = fit_session(
        start=START + timedelta(days=1),
        sport="swimming",
        sub_sport="lap_swimming",
        seconds=60,
        distance_per_second=1.0,
        extra={"pool_length": 25.0},
    )
    later = START + timedelta(days=1)
    lengths = [fit_length(later, 30, 16), fit_length(later + timedelta(seconds=30), 30, 17)]
    return fit_file([([*records, *lengths], summary)])


def kinds(activity: Activity) -> set[StreamKind]:
    return {stream.kind for stream in activity.streams}


def imported(world: IngestionWorld, data: bytes, name: str) -> UUID:
    result = world.upload(data, filename=name)
    assert result.record.succeeded, result.record
    (item,) = result.record.activities
    return item.activity_id


@pytest.fixture
def setup(tmp_path: Path) -> tuple[IngestionWorld, Formats]:
    formats = Formats()
    return IngestionWorld(tmp_path, formats=formats), formats


@pytest.mark.req("FR-ACT-002", "INT-FIL-001", "FR-CYC-002", "FR-SWM-002")
def test_history_gains_laps_lengths_and_power_sensor(
    setup: tuple[IngestionWorld, Formats],
) -> None:
    world, formats = setup
    ride = imported(world, ride_with_laps(), "ride.fit")
    swim = imported(world, pool_swim(), "swim.fit")
    world.service.correct(SCOPE, PRINCIPAL, ride, "distance.meters", 4700.0, "Measured route")
    before = world.activities.get(SCOPE, ride)
    assert kinds(before) == {StreamKind.TIME_SERIES}
    assert world.ingestion.reprocess(SCOPE).up_to_date == 2  # nothing older than 1.0.0 yet

    formats.upgrade()
    seen: list[tuple[int, int]] = []
    report = world.ingestion.reprocess(
        SCOPE, progress=lambda done, total: seen.append((done, total))
    )
    assert seen == [(1, 2), (2, 2)]
    assert (report.reprocessed, report.activities, report.up_to_date, report.skipped) == (
        2,
        2,
        0,
        {},
    )
    after = world.activities.get(SCOPE, ride)
    assert (after.activity_id, after.sources, after.status) == (
        before.activity_id,
        before.sources,
        before.status,
    )
    assert kinds(after) == {StreamKind.TIME_SERIES, StreamKind.LAPS}
    view = world.service.effective(SCOPE, ride).activity
    assert view.values["performance.powerSensor"] is PowerSensor.POWER_METER
    assert view.values["distance.meters"] == 4700.0  # the correction still applies
    assert StreamKind.LENGTHS in kinds(world.activities.get(SCOPE, swim))
    key = after.sources[0].key
    assert [item.normaliser_version for item in world.observations.revisions(SCOPE, key)] == [
        "1.0.0",
        "1.1.0",
    ]
    assert (EventName.ACTIVITY_REPROCESSED, {"reprocessed": "2", "skipped": "0"}) in (
        world.events.events
    )
    again = world.ingestion.reprocess(SCOPE)  # idempotent
    assert (again.reprocessed, again.up_to_date) == (0, 2)


@pytest.mark.req("FR-ACT-002")
def test_a_session_that_is_no_longer_the_same_is_left_alone(
    setup: tuple[IngestionWorld, Formats],
) -> None:
    world, formats = setup
    formats.legacy_start_shift = timedelta(minutes=5)  # as if 1.0.0 had read another start
    ride = imported(world, ride_with_laps(), "ride.fit")
    formats.upgrade()
    report = world.ingestion.reprocess(SCOPE)
    assert (report.reprocessed, report.skipped) == (0, {"IDENTITY_CHANGED": 1})
    assert kinds(world.activities.get(SCOPE, ride)) == {StreamKind.TIME_SERIES}
    assert (
        len(world.observations.revisions(SCOPE, world.activities.get(SCOPE, ride).sources[0].key))
        == 1
    )


@pytest.mark.req("FR-ACT-002")
def test_a_file_with_more_sessions_now_is_left_alone(
    setup: tuple[IngestionWorld, Formats],
) -> None:
    world, formats = setup
    formats.legacy_sessions = 1
    second = fit_session(start=START + timedelta(hours=2))
    imported(world, fit_file([fit_session(), second]), "two.fit")
    formats.upgrade()
    assert world.ingestion.reprocess(SCOPE).skipped == {"SESSIONS_CHANGED": 1}


@pytest.mark.req("FR-ACT-002", "INT-FIL-004")
def test_a_file_that_no_longer_parses_is_left_alone(
    setup: tuple[IngestionWorld, Formats],
) -> None:
    world, formats = setup
    ride = imported(world, ride_with_laps(), "ride.fit")
    formats.upgrade()
    formats.reject = True
    assert world.ingestion.reprocess(SCOPE).skipped == {"REJECTED": 1}
    assert kinds(world.activities.get(SCOPE, ride)) == {StreamKind.TIME_SERIES}
    assert world.quarantined() == []  # the re-read copy is discarded


@pytest.mark.req("PRIV-CON-002")
def test_re_normalisation_needs_the_activities_consent(
    setup: tuple[IngestionWorld, Formats],
) -> None:
    world, _ = setup
    world.consents.revoke(SCOPE, ConsentType.ACTIVITIES)
    with pytest.raises(ConsentRequiredError):
        world.ingestion.reprocess(SCOPE)
