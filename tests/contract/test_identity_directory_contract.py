"""Identity directory: the single installation owner and its athlete link (SEC-AUTHZ-001)."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from coach.application.identity import Identity, OwnerAlreadyRegisteredError
from coach.application.ports import IdentityDirectory
from coach.infrastructure.memory import InMemoryIdentityDirectory
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory
from samples import ATHLETE

pytestmark = pytest.mark.req("SEC-AUTHZ-001")

USER = UUID("0193b2a0-0000-7000-8000-0000000000a1")
OWNER = Identity(
    user_id=USER, athlete_id=ATHLETE, created_at=datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
)


def _in_memory(_: Path) -> IdentityDirectory:
    return InMemoryIdentityDirectory()


def _sqlite(directory: Path) -> IdentityDirectory:
    return SqliteIdentityDirectory(SqliteDatabase.open(directory / "coach.sqlite3"))


@pytest.fixture(params=[_in_memory, _sqlite], ids=["in-memory", "sqlite"])
def directory(request: pytest.FixtureRequest, tmp_path: Path) -> IdentityDirectory:
    factory: Callable[[Path], IdentityDirectory] = request.param
    return factory(tmp_path)


def test_owner_is_registered_once(directory: IdentityDirectory) -> None:
    assert directory.owner() is None
    assert directory.register_owner(OWNER) == OWNER
    assert directory.owner() == OWNER
    with pytest.raises(OwnerAlreadyRegisteredError):
        directory.register_owner(
            Identity(user_id=UUID(int=5), athlete_id=UUID(int=6), created_at=OWNER.created_at)
        )


def test_links_resolve_only_known_users(directory: IdentityDirectory) -> None:
    directory.register_owner(OWNER)
    assert directory.athlete_for(USER) == ATHLETE
    assert directory.athlete_for(UUID(int=99)) is None
