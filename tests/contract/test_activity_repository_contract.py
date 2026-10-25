"""Contracts for activity, observation and override repositories (ADR-004, ADR-011, ADR-016).

Each test runs against the in-memory reference adapter and the SQLite adapter.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from coach.application.errors import ConcurrencyConflictError, NotFoundError, OwnershipError
from coach.application.ownership import OwnerScope
from coach.application.ports import (
    ActivityChange,
    ActivityRepository,
    ObservationRepository,
    OverrideRepository,
)
from coach.domain.activity.activity import ActivityStatus, LinkKind
from coach.domain.activity.observation import Observation, SourceKey, SourceKind
from coach.domain.activity.overrides import EntityType
from coach.infrastructure.memory import (
    InMemoryActivityRepository,
    InMemoryObservationRepository,
    InMemoryOverrideRepository,
)
from coach.infrastructure.sqlite.activities import (
    SqliteActivityRepository,
    SqliteObservationRepository,
    SqliteOverrideRepository,
)
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.raw_sources import SqliteRawSourceRepository
from samples import (
    ACTIVITY,
    ATHLETE,
    FIT_SOURCE,
    GARMIN_SOURCE,
    MANUAL_SOURCE,
    OTHER_ATHLETE,
    SHA_A,
    SHA_B,
    SHA_C,
    SHA_D,
    START,
    sample_activity,
    sample_observation,
    sample_override,
    sample_raw_source,
)

SCOPE_A = OwnerScope(ATHLETE)
SCOPE_B = OwnerScope(OTHER_ATHLETE)
SECOND = UUID("0193b2a0-0000-7000-8000-000000000102")
THIRD = UUID("0193b2a0-0000-7000-8000-000000000103")
FOREIGN_SOURCE = UUID("0193b2a0-0000-7000-8000-000000000299")


@dataclass
class Stores:
    activities: ActivityRepository
    observations: ObservationRepository
    overrides: OverrideRepository


def _in_memory(_: Path) -> Stores:
    return Stores(
        InMemoryActivityRepository(), InMemoryObservationRepository(), InMemoryOverrideRepository()
    )


def _sqlite(directory: Path) -> Stores:
    database = SqliteDatabase.open(directory / "coach.sqlite3")
    raw = SqliteRawSourceRepository(database)
    for raw_id, sha, athlete in (
        (FIT_SOURCE, SHA_A, ATHLETE),
        (GARMIN_SOURCE, SHA_B, ATHLETE),
        (MANUAL_SOURCE, SHA_C, ATHLETE),
        (FOREIGN_SOURCE, SHA_D, OTHER_ATHLETE),
    ):
        raw.add(OwnerScope(athlete), sample_raw_source(athlete, sha, raw_id))
    return Stores(
        SqliteActivityRepository(database),
        SqliteObservationRepository(database),
        SqliteOverrideRepository(database),
    )


@pytest.fixture(params=[_in_memory, _sqlite], ids=["in-memory", "sqlite"])
def stores(request: pytest.FixtureRequest, tmp_path: Path) -> Stores:
    factory: Callable[[Path], Stores] = request.param
    return factory(tmp_path)


def _garmin() -> Observation:
    return sample_observation(SourceKind.PROVIDER_RECORD)


# --------------------------------------------------------------------------- activities


@pytest.mark.req("FR-ACT-001", "NFR-REL-003")
def test_activities_are_saved_with_versions(stores: Stores) -> None:
    (stored,) = stores.activities.save(SCOPE_A, [ActivityChange(sample_activity())])
    assert stored.version == 1
    assert stores.activities.get(SCOPE_A, ACTIVITY) == stored
    assert stores.activities.list_all(SCOPE_A) == [stored]
    renamed = replace(stored, values={**stored.values, "metadata.name": "Tempo"})
    (updated,) = stores.activities.save(SCOPE_A, [ActivityChange(renamed, 1)])
    assert updated.version == 2
    assert stores.activities.get(SCOPE_A, ACTIVITY).values["metadata.name"] == "Tempo"
    with pytest.raises(ConcurrencyConflictError):
        stores.activities.save(SCOPE_A, [ActivityChange(renamed, 1)])
    with pytest.raises(ConcurrencyConflictError):
        stores.activities.save(SCOPE_A, [ActivityChange(sample_activity())])


@pytest.mark.req("SEC-IDOR-001", "SEC-AUTHZ-001")
def test_activities_are_owner_scoped(stores: Stores) -> None:
    stores.activities.save(SCOPE_A, [ActivityChange(sample_activity())])
    with pytest.raises(NotFoundError):
        stores.activities.get(SCOPE_B, ACTIVITY)
    assert stores.activities.list_all(SCOPE_B) == []
    assert stores.activities.find_by_source(SCOPE_B, SourceKey(FIT_SOURCE)) is None
    assert stores.activities.starting_between(SCOPE_B, START, START) == []
    with pytest.raises(NotFoundError):
        stores.activities.save(SCOPE_B, [ActivityChange(sample_activity(), 1)])
    with pytest.raises(OwnershipError):
        stores.activities.save(SCOPE_B, [ActivityChange(sample_activity(activity_id=SECOND))])


@pytest.mark.req("FR-ACT-007", "NFR-REL-001")
def test_a_source_belongs_to_one_live_activity(stores: Stores) -> None:
    stores.activities.save(SCOPE_A, [ActivityChange(sample_activity())])
    found = stores.activities.find_by_source(SCOPE_A, SourceKey(FIT_SOURCE))
    assert found is not None
    assert found.activity_id == ACTIVITY
    assert stores.activities.find_by_source(SCOPE_A, SourceKey(FIT_SOURCE, 1)) is None
    duplicate = sample_activity(activity_id=SECOND)
    with pytest.raises(ConcurrencyConflictError):
        stores.activities.save(SCOPE_A, [ActivityChange(duplicate)])
    assert [item.activity_id for item in stores.activities.list_all(SCOPE_A)] == [ACTIVITY]


@pytest.mark.req("FR-ACT-007")
def test_merge_moves_sources_atomically(stores: Stores) -> None:
    target, pending = stores.activities.save(
        SCOPE_A,
        [
            ActivityChange(sample_activity(_garmin())),
            ActivityChange(
                sample_activity(activity_id=SECOND, status=ActivityStatus.PENDING_REVIEW)
            ),
        ],
    )
    moved = replace(pending.sources[0], link_kind=LinkKind.ENRICHMENT)
    merged = replace(target, sources=(*target.sources, moved))
    tombstone = replace(pending, status=ActivityStatus.MERGED, merged_into=target.activity_id)
    with pytest.raises(ConcurrencyConflictError):
        stores.activities.save(SCOPE_A, [ActivityChange(merged, 1)])
    stale = replace(tombstone, version=1)
    with pytest.raises(ConcurrencyConflictError):
        stores.activities.save(SCOPE_A, [ActivityChange(merged, 1), ActivityChange(stale, 2)])
    assert stores.activities.get(SCOPE_A, ACTIVITY).version == 1
    assert stores.activities.find_by_source(SCOPE_A, SourceKey(FIT_SOURCE)) == pending
    # The order of the changes inside one save does not matter.
    stores.activities.save(SCOPE_A, [ActivityChange(merged, 1), ActivityChange(tombstone, 1)])
    found = stores.activities.find_by_source(SCOPE_A, SourceKey(FIT_SOURCE))
    assert found is not None
    assert found.activity_id == ACTIVITY
    assert stores.activities.get(SCOPE_A, SECOND).status is ActivityStatus.MERGED


@pytest.mark.req("FR-ACT-007")
def test_provider_records_resolve_to_their_live_activity(stores: Stores) -> None:
    stores.activities.save(SCOPE_A, [ActivityChange(sample_activity(_garmin()))])
    found = stores.activities.find_by_provider_record(SCOPE_A, "garmin", "synthetic-1001")
    assert found is not None
    assert found.activity_id == ACTIVITY
    assert stores.activities.find_by_provider_record(SCOPE_A, "garmin", "other") is None
    assert stores.activities.find_by_provider_record(SCOPE_B, "garmin", "synthetic-1001") is None


@pytest.mark.req("FR-ACT-007")
def test_candidates_are_live_activities_in_the_window(stores: Stores) -> None:
    early = sample_activity()
    late = sample_activity(
        sample_observation(
            SourceKind.PROVIDER_RECORD,
            updates={"timing.startTimeUtc": START + timedelta(minutes=9)},
        ),
        activity_id=SECOND,
        status=ActivityStatus.PENDING_REVIEW,
    )
    stores.activities.save(SCOPE_A, [ActivityChange(late), ActivityChange(early)])
    window = stores.activities.starting_between(SCOPE_A, START, START + timedelta(minutes=9))
    assert [item.activity_id for item in window] == [ACTIVITY, SECOND]
    assert (
        stores.activities.starting_between(
            SCOPE_A, START + timedelta(seconds=1), START + timedelta(minutes=8)
        )
        == []
    )
    stores.activities.save(
        SCOPE_A,
        [ActivityChange(replace(late, status=ActivityStatus.MERGED, merged_into=ACTIVITY), 1)],
    )
    window = stores.activities.starting_between(SCOPE_A, START, START + timedelta(minutes=9))
    assert [item.activity_id for item in window] == [ACTIVITY]


# --------------------------------------------------------------------------- observations


@pytest.mark.req("FR-ACT-002", "NFR-REL-001")
def test_observations_are_immutable_and_idempotent(stores: Stores) -> None:
    observation = sample_observation()
    assert stores.observations.add(SCOPE_A, observation) == (observation, True)
    assert stores.observations.add(SCOPE_A, observation) == (observation, False)
    changed = sample_observation(updates={"distance.meters": 1.0})
    with pytest.raises(ConcurrencyConflictError):
        stores.observations.add(SCOPE_A, changed)
    assert stores.observations.get(SCOPE_A, observation.key) == observation


@pytest.mark.req("SEC-IDOR-001")
def test_observations_are_owner_scoped(stores: Stores) -> None:
    theirs = sample_observation(
        raw_source_id=FOREIGN_SOURCE, sha256=SHA_D, athlete_id=OTHER_ATHLETE
    )
    stores.observations.add(SCOPE_B, theirs)
    with pytest.raises(NotFoundError):
        stores.observations.get(SCOPE_A, theirs.key)
    with pytest.raises(OwnershipError):
        stores.observations.add(SCOPE_A, theirs)
    with pytest.raises(NotFoundError):
        stores.observations.get(SCOPE_A, SourceKey(FIT_SOURCE))


# --------------------------------------------------------------------------- overrides


@pytest.mark.req("FR-ACT-006")
def test_overrides_extend_one_chain_per_field(stores: Stores) -> None:
    first = stores.overrides.add(SCOPE_A, sample_override())
    with pytest.raises(ConcurrencyConflictError):
        stores.overrides.add(SCOPE_A, sample_override(override_id=UUID(int=2)))
    second = stores.overrides.add(
        SCOPE_A,
        sample_override(
            override_id=UUID(int=2),
            corrected_value=10020.0,
            supersedes_override_id=first.override_id,
        ),
    )
    with pytest.raises(ConcurrencyConflictError):
        stores.overrides.add(
            SCOPE_A,
            sample_override(override_id=UUID(int=3), supersedes_override_id=first.override_id),
        )
    gain = stores.overrides.add(
        SCOPE_A,
        sample_override(
            override_id=UUID(int=4),
            field="elevation.gainMeters",
            original_value=85.0,
            corrected_value=90.0,
        ),
    )
    assert stores.overrides.for_entity(SCOPE_A, EntityType.ACTIVITY, ACTIVITY) == [
        first,
        second,
        gain,
    ]
    assert stores.overrides.get(SCOPE_A, second.override_id) == second
    with pytest.raises(ConcurrencyConflictError):
        stores.overrides.add(
            SCOPE_A,
            sample_override(override_id=UUID(int=2), supersedes_override_id=gain.override_id),
        )


@pytest.mark.req("SEC-IDOR-001", "FR-ACT-006")
def test_overrides_are_owner_scoped(stores: Stores) -> None:
    theirs = stores.overrides.add(SCOPE_B, sample_override(athlete_id=OTHER_ATHLETE))
    with pytest.raises(NotFoundError):
        stores.overrides.get(SCOPE_A, theirs.override_id)
    assert stores.overrides.for_entity(SCOPE_A, EntityType.ACTIVITY, ACTIVITY) == []
    with pytest.raises(OwnershipError):
        stores.overrides.add(
            SCOPE_A, sample_override(override_id=UUID(int=8), athlete_id=OTHER_ATHLETE)
        )


# --------------------------------------------------------------------------- SQLite integrity


@pytest.mark.req("FR-ACT-002", "FR-ACT-006")
@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("UPDATE source_observations SET document = json('{}')", "immutable"),
        ("UPDATE data_overrides SET field = 'elevation.lossMeters'", "append-only"),
    ],
)
def test_database_rejects_rewriting_observations_and_overrides(
    tmp_path: Path, sql: str, message: str
) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    stores = _sqlite_stores(database)
    stores.observations.add(SCOPE_A, sample_observation())
    stores.overrides.add(SCOPE_A, sample_override())
    with pytest.raises(sqlite3.IntegrityError, match=message), database.write() as connection:
        connection.execute(sql)


@pytest.mark.req("FR-ACT-006")
def test_database_allows_one_chain_per_field(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    _sqlite_stores(database).overrides.add(SCOPE_A, sample_override())
    insert = (
        "INSERT INTO data_overrides (override_id, athlete_id, entity_type, entity_id, field, "
        "supersedes_override_id, schema_version, document) VALUES (?, ?, 'ACTIVITY', ?, "
        "'distance.meters', ?, '1.0.0', '{}')"
    )
    first = str(sample_override().override_id)
    with database.write() as connection:
        connection.execute(insert, (str(UUID(int=2)), str(ATHLETE), str(ACTIVITY), first))
    for override_id, supersedes in ((UUID(int=3), None), (UUID(int=4), first)):
        with pytest.raises(sqlite3.IntegrityError), database.write() as connection:
            connection.execute(insert, (str(override_id), str(ATHLETE), str(ACTIVITY), supersedes))


@pytest.mark.req("FR-ACT-002")
def test_observations_need_their_raw_source(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    with pytest.raises(NotFoundError):
        SqliteObservationRepository(database).add(SCOPE_A, sample_observation())


def _sqlite_stores(database: SqliteDatabase) -> Stores:
    SqliteRawSourceRepository(database).add(SCOPE_A, sample_raw_source())
    return Stores(
        SqliteActivityRepository(database),
        SqliteObservationRepository(database),
        SqliteOverrideRepository(database),
    )
