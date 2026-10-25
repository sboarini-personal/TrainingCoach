"""Every aggregate kind persists as its contract document (ADR-022, NFR-REL-004)."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from coach.application.contracts import (
    AVAILABILITY_CODEC,
    COACHING_STATE_CODEC,
    CONSENT_LEDGER_CODEC,
    CREDENTIAL_RECORD_CODEC,
    GOAL_CODEC,
    PROFILE_CODEC,
    SPORT_EVENT_CODEC,
    DocumentCodec,
)
from coach.application.ownership import OwnerScope
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from coach.infrastructure.sqlite.database import SqliteDatabase
from samples import (
    ATHLETE,
    full_profile,
    sample_availability,
    sample_coaching_state,
    sample_consent_ledger,
    sample_credential_record,
    sample_event,
    sample_goal,
)
from schemas import validation_errors

SCOPE = OwnerScope(ATHLETE)
CASES: list[tuple[DocumentCodec[Any], Any]] = [
    (PROFILE_CODEC, full_profile()),
    (AVAILABILITY_CODEC, sample_availability()),
    (GOAL_CODEC, sample_goal()),
    (SPORT_EVENT_CODEC, sample_event()),
    (CONSENT_LEDGER_CODEC, sample_consent_ledger()),
    (COACHING_STATE_CODEC, sample_coaching_state()),
    (CREDENTIAL_RECORD_CODEC, sample_credential_record()),
]


@pytest.fixture
def database(tmp_path: Path) -> SqliteDatabase:
    return SqliteDatabase.open(tmp_path / "coach.sqlite3")


@pytest.mark.req("NFR-REL-004", "FR-PRF-001", "PRIV-CON-001", "FR-ONB-006")
@pytest.mark.parametrize(("codec", "entity"), CASES, ids=lambda case: getattr(case, "kind", ""))
def test_aggregate_round_trips_through_sqlite(
    database: SqliteDatabase, codec: DocumentCodec[Any], entity: Any
) -> None:
    repository = SqliteAggregateRepository(database, codec)
    stored = repository.add(SCOPE, entity)
    assert repository.get(SCOPE, entity.entity_id) == stored == entity.with_version(1)
    updated = repository.update(SCOPE, stored, expected_version=1)
    assert repository.get(SCOPE, entity.entity_id).version == updated.version == 2


@pytest.mark.req("NFR-REL-004")
@pytest.mark.parametrize(("codec", "entity"), CASES, ids=lambda case: getattr(case, "kind", ""))
def test_stored_document_is_the_published_contract(
    database: SqliteDatabase, codec: DocumentCodec[Any], entity: Any
) -> None:
    SqliteAggregateRepository(database, codec).add(SCOPE, entity)
    with database.read() as connection:
        row = connection.execute(
            "SELECT schema_version, document FROM aggregates WHERE kind = ?", (codec.kind,)
        ).fetchone()
    document = json.loads(row["document"])
    assert row["schema_version"] == document["schemaVersion"]
    assert validation_errors(codec.schema, document) == []


def test_kinds_sharing_an_id_are_kept_apart(database: SqliteDatabase) -> None:
    profiles = SqliteAggregateRepository(database, PROFILE_CODEC)
    states = SqliteAggregateRepository(database, COACHING_STATE_CODEC)
    profiles.add(SCOPE, full_profile())
    states.add(SCOPE, sample_coaching_state())
    assert profiles.get(SCOPE, ATHLETE).athlete_id == states.get(SCOPE, ATHLETE).athlete_id
    assert len(profiles.list_all(SCOPE)) == len(states.list_all(SCOPE)) == 1


@pytest.mark.req("SEC-INJ-003")
def test_sql_metacharacters_are_stored_as_data(database: SqliteDatabase) -> None:
    payload = "Robert'); DROP TABLE aggregates;--"
    events = SqliteAggregateRepository(database, SPORT_EVENT_CODEC)
    event = replace(sample_event(), name=payload, location=payload)
    events.add(SCOPE, event)
    assert events.get(SCOPE, event.event_id).name == payload
    with database.read() as connection:
        assert connection.execute("SELECT count(*) FROM aggregates").fetchone()[0] == 1
