"""Contract every owner-scoped repository implementation must satisfy (ADR-016, NFR-REL-003)."""

import inspect
import threading
import typing
from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from coach.application.errors import ConcurrencyConflictError, NotFoundError, OwnershipError
from coach.application.ids import new_id
from coach.application.ownership import OwnerScope
from coach.application.ports import OwnedRepository
from coach.domain.common.sport import Sport
from coach.domain.goals.goal import Goal, GoalPriority, GoalStatus
from coach.infrastructure.memory import InMemoryRepository

ATHLETE_A = UUID("0193b2a0-0000-7000-8000-00000000000a")
ATHLETE_B = UUID("0193b2a0-0000-7000-8000-00000000000b")
SCOPE_A = OwnerScope(ATHLETE_A)
SCOPE_B = OwnerScope(ATHLETE_B)
NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)

IMPLEMENTATIONS: list[Callable[[], OwnedRepository[Goal]]] = [InMemoryRepository]


def goal_for(athlete_id: UUID) -> Goal:
    return Goal.new(
        goal_id=new_id(),
        athlete_id=athlete_id,
        sport=Sport.RUNNING,
        target_date=date(2026, 12, 13),
        priority=GoalPriority.A,
        created_at=NOW,
    )


@pytest.fixture(params=IMPLEMENTATIONS, ids=lambda factory: getattr(factory, "__name__", "repo"))
def repository(request: pytest.FixtureRequest) -> OwnedRepository[Goal]:
    factory: Callable[[], OwnedRepository[Goal]] = request.param
    return factory()


# ------------------------------------------------------------------ ownership


@pytest.mark.req("SEC-IDOR-001", "SEC-AUTHZ-001")
def test_foreign_resource_is_indistinguishable_from_a_missing_one(
    repository: OwnedRepository[Goal],
) -> None:
    foreign = repository.add(SCOPE_B, goal_for(ATHLETE_B))
    with pytest.raises(NotFoundError) as foreign_error:
        repository.get(SCOPE_A, foreign.entity_id)
    with pytest.raises(NotFoundError) as missing_error:
        repository.get(SCOPE_A, new_id())
    assert str(foreign_error.value) == str(missing_error.value)


@pytest.mark.req("SEC-IDOR-001")
def test_listing_returns_only_owned_resources(repository: OwnedRepository[Goal]) -> None:
    mine = repository.add(SCOPE_A, goal_for(ATHLETE_A))
    repository.add(SCOPE_B, goal_for(ATHLETE_B))
    assert [goal.entity_id for goal in repository.list_all(SCOPE_A)] == [mine.entity_id]


@pytest.mark.req("SEC-AUTHZ-001")
def test_resource_cannot_be_added_for_another_athlete(repository: OwnedRepository[Goal]) -> None:
    with pytest.raises(OwnershipError):
        repository.add(SCOPE_A, goal_for(ATHLETE_B))


@pytest.mark.req("SEC-IDOR-001")
def test_foreign_resource_cannot_be_updated(repository: OwnedRepository[Goal]) -> None:
    foreign = repository.add(SCOPE_B, goal_for(ATHLETE_B))
    with pytest.raises(NotFoundError):
        repository.update(SCOPE_A, foreign, expected_version=foreign.version)


@pytest.mark.req("SEC-AUTHZ-001")
def test_update_cannot_transfer_ownership(repository: OwnedRepository[Goal]) -> None:
    mine = repository.add(SCOPE_A, goal_for(ATHLETE_A))
    stolen = Goal.new(
        goal_id=mine.goal_id,
        athlete_id=ATHLETE_B,
        sport=mine.sport,
        target_date=mine.target_date,
        priority=mine.priority,
        created_at=mine.created_at,
    )
    with pytest.raises(OwnershipError):
        repository.update(SCOPE_A, stolen, expected_version=mine.version)


@pytest.mark.req("SEC-IDOR-001")
def test_repository_port_requires_owner_scope_on_every_method() -> None:
    for implementation in (OwnedRepository, InMemoryRepository):
        # PEP 695 type parameters (T) live in the class scope; expose them to the resolver.
        type_params = {param.__name__: param for param in implementation.__type_params__}
        for name, member in inspect.getmembers(implementation, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            parameters = list(inspect.signature(member).parameters)
            hints = typing.get_type_hints(member, localns=type_params)
            assert parameters[1] == "scope", f"{implementation.__name__}.{name} lacks scope"
            assert hints["scope"] is OwnerScope, f"{implementation.__name__}.{name} scope type"


# ------------------------------------------------------------------ concurrency


@pytest.mark.req("NFR-REL-003")
def test_new_resources_start_at_version_one(repository: OwnedRepository[Goal]) -> None:
    stored = repository.add(SCOPE_A, goal_for(ATHLETE_A).with_version(9))
    assert stored.version == 1
    with pytest.raises(ConcurrencyConflictError):
        repository.add(SCOPE_A, stored)


@pytest.mark.req("NFR-REL-003")
def test_stale_update_is_rejected_not_overwritten(repository: OwnedRepository[Goal]) -> None:
    stored = repository.add(SCOPE_A, goal_for(ATHLETE_A))
    first = repository.update(SCOPE_A, stored.transition(GoalStatus.ACTIVE), expected_version=1)
    assert first.version == 2
    with pytest.raises(ConcurrencyConflictError):
        repository.update(SCOPE_A, stored.transition(GoalStatus.ABANDONED), expected_version=1)
    current = repository.get(SCOPE_A, stored.entity_id)
    assert (current.status, current.version) == (GoalStatus.ACTIVE, 2)


@pytest.mark.req("NFR-REL-003")
def test_simultaneous_updates_produce_exactly_one_winner(repository: OwnedRepository[Goal]) -> None:
    stored = repository.add(SCOPE_A, goal_for(ATHLETE_A))
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(status: GoalStatus) -> None:
        barrier.wait()
        try:
            repository.update(SCOPE_A, stored.transition(status), expected_version=1)
            result = "ok"
        except ConcurrencyConflictError:
            result = "conflict"
        with lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=attempt, args=(status,))
        for status in (GoalStatus.ACTIVE, GoalStatus.ABANDONED)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict", "ok"]
    assert repository.get(SCOPE_A, stored.entity_id).version == 2
