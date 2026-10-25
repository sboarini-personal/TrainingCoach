"""Contract for derived value repositories: append-only with one current value (FR-MET-004)."""

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from coach.application.errors import ConcurrencyConflictError, NotFoundError, OwnershipError
from coach.application.ownership import OwnerScope
from coach.application.ports import DerivedValueRepository
from coach.domain.common.errors import DomainValidationError
from coach.infrastructure.memory import InMemoryDerivedValueRepository
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.derived_values import SqliteDerivedValueRepository
from coach.metrics.derived import MetricSubject
from samples import ATHLETE, OTHER_ATHLETE, sample_derived_value

SCOPE_A = OwnerScope(ATHLETE)
SCOPE_B = OwnerScope(OTHER_ATHLETE)
FIRST = sample_derived_value(value_id=UUID(int=1))
SECOND = sample_derived_value(value_id=UUID(int=2), rpe=6)
THIRD = sample_derived_value(value_id=UUID(int=3), rpe=7)
SUBJECT = FIRST.subject


def _in_memory(_: Path) -> DerivedValueRepository:
    return InMemoryDerivedValueRepository()


def _sqlite(directory: Path) -> DerivedValueRepository:
    return SqliteDerivedValueRepository(SqliteDatabase.open(directory / "coach.sqlite3"))


@pytest.fixture(params=[_in_memory, _sqlite], ids=["in-memory", "sqlite"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> DerivedValueRepository:
    factory: Callable[[Path], DerivedValueRepository] = request.param
    return factory(tmp_path)


@pytest.mark.req("FR-MET-004")
def test_one_current_value_per_metric_and_subject(repository: DerivedValueRepository) -> None:
    repository.add(SCOPE_A, FIRST)
    assert repository.current(SCOPE_A, FIRST.metric_id, SUBJECT) == FIRST
    with pytest.raises(ConcurrencyConflictError):
        repository.add(SCOPE_A, SECOND)


@pytest.mark.req("FR-MET-004", "NFR-TRC-001")
def test_supersession_keeps_the_old_value(repository: DerivedValueRepository) -> None:
    repository.add(SCOPE_A, FIRST)
    repository.supersede(SCOPE_A, FIRST.value_id, SECOND)
    assert repository.current(SCOPE_A, FIRST.metric_id, SUBJECT) == SECOND
    old = repository.get(SCOPE_A, FIRST.value_id)
    assert old.superseded_by == SECOND.value_id
    assert old.value == FIRST.value
    assert [value.value_id for value in repository.history(SCOPE_A, FIRST.metric_id, SUBJECT)] == [
        FIRST.value_id,
        SECOND.value_id,
    ]


@pytest.mark.req("FR-MET-004")
def test_a_value_is_superseded_only_once(repository: DerivedValueRepository) -> None:
    repository.add(SCOPE_A, FIRST)
    repository.supersede(SCOPE_A, FIRST.value_id, SECOND)
    with pytest.raises(ConcurrencyConflictError):
        repository.supersede(SCOPE_A, FIRST.value_id, THIRD)


@pytest.mark.req("FR-MET-004")
def test_supersession_requires_same_metric_and_subject(repository: DerivedValueRepository) -> None:
    repository.add(SCOPE_A, FIRST)
    other_subject = sample_derived_value(
        value_id=UUID(int=9), subject=MetricSubject.activity(UUID(int=77))
    )
    with pytest.raises(DomainValidationError):
        repository.supersede(SCOPE_A, FIRST.value_id, other_subject)


@pytest.mark.req("SEC-IDOR-001", "SEC-AUTHZ-001")
def test_values_are_owner_scoped(repository: DerivedValueRepository) -> None:
    repository.add(SCOPE_B, sample_derived_value(OTHER_ATHLETE, value_id=UUID(int=5)))
    with pytest.raises(NotFoundError):
        repository.get(SCOPE_A, UUID(int=5))
    assert repository.current(SCOPE_A, FIRST.metric_id, SUBJECT) is None
    with pytest.raises(NotFoundError):
        repository.supersede(SCOPE_A, UUID(int=5), SECOND)
    with pytest.raises(OwnershipError):
        repository.add(SCOPE_A, sample_derived_value(OTHER_ATHLETE))


@pytest.mark.req("FR-MET-004", "NFR-REL-003")
def test_simultaneous_supersessions_have_one_winner(repository: DerivedValueRepository) -> None:
    repository.add(SCOPE_A, FIRST)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(new_value_id: int) -> None:
        barrier.wait()
        try:
            repository.supersede(
                SCOPE_A,
                FIRST.value_id,
                sample_derived_value(value_id=UUID(int=new_value_id), rpe=8),
            )
            result = "ok"
        except ConcurrencyConflictError:
            result = "conflict"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt, args=(value_id,)) for value_id in (11, 12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict", "ok"]
    assert len(repository.history(SCOPE_A, FIRST.metric_id, SUBJECT)) == 2


@pytest.mark.req("FR-MET-004")
def test_database_rejects_rewriting_a_value(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    SqliteDerivedValueRepository(database).add(SCOPE_A, FIRST)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), database.write() as connection:
        connection.execute(
            "UPDATE derived_metric_values SET document = json_set(document, '$.value', 1)"
        )
