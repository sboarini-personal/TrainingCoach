"""Contract for audit logs: append-only chain, owner-scoped reads, forgetting (SEC-AUD-001)."""

import inspect
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coach.application.audit import GENESIS_HASH, ActorKind, AuditEvent, AuditOutcome
from coach.application.ownership import OwnerScope
from coach.application.ports import AuditLog
from coach.infrastructure.memory import InMemoryAuditLog
from coach.infrastructure.sqlite.audit import SqliteAuditLog
from coach.infrastructure.sqlite.database import SqliteDatabase
from samples import ATHLETE, OTHER_ATHLETE

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
SCOPE_A = OwnerScope(ATHLETE)
SCOPE_B = OwnerScope(OTHER_ATHLETE)


def event(athlete: object = ATHLETE, action: str = "consent.granted") -> AuditEvent:
    return AuditEvent(
        occurred_at=NOW,
        actor=ActorKind.USER,
        action=action,
        outcome=AuditOutcome.SUCCESS,
        athlete_id=athlete,  # type: ignore[arg-type]
    )


def _in_memory(_: Path) -> AuditLog:
    return InMemoryAuditLog()


def _sqlite(directory: Path) -> AuditLog:
    return SqliteAuditLog(SqliteDatabase.open(directory / "coach.sqlite3"))


@pytest.fixture(params=[_in_memory, _sqlite], ids=["in-memory", "sqlite"])
def audit_log(request: pytest.FixtureRequest, tmp_path: Path) -> AuditLog:
    factory: Callable[[Path], AuditLog] = request.param
    return factory(tmp_path)


@pytest.mark.req("SEC-AUD-001")
@pytest.mark.acceptance("AT-AUD-001")
def test_events_form_a_gapless_chain(audit_log: AuditLog) -> None:
    first = audit_log.append(event())
    second = audit_log.append(event(OTHER_ATHLETE))
    assert (first.sequence, first.previous_hash) == (1, GENESIS_HASH)
    assert (second.sequence, second.previous_hash) == (2, first.hash)
    result = audit_log.verify()
    assert (result.valid, result.event_count) == (True, 2)


@pytest.mark.req("SEC-AUD-001", "SEC-IDOR-001")
def test_reads_are_owner_scoped(audit_log: AuditLog) -> None:
    audit_log.append(event())
    audit_log.append(event(OTHER_ATHLETE))
    audit_log.append(event(None, action="auth.authenticate"))
    mine = audit_log.events_for(SCOPE_A)
    assert [record.athlete_id for record in mine] == [ATHLETE]
    assert audit_log.events_for(SCOPE_B)[0].athlete_id == OTHER_ATHLETE


@pytest.mark.req("PRIV-DEL-003")
@pytest.mark.acceptance("AT-AUD-001")
def test_forgetting_an_athlete_keeps_the_chain_valid(audit_log: AuditLog) -> None:
    audit_log.append(event())
    audit_log.append(event(OTHER_ATHLETE))
    audit_log.append(event())
    assert audit_log.forget(SCOPE_A) == 2
    assert audit_log.events_for(SCOPE_A) == []
    assert len(audit_log.events_for(SCOPE_B)) == 1
    assert audit_log.verify().valid


@pytest.mark.req("SEC-AUD-001")
def test_concurrent_appends_stay_linear(audit_log: AuditLog) -> None:
    barrier = threading.Barrier(4)

    def write() -> None:
        barrier.wait()
        for _ in range(5):
            audit_log.append(event())

    threads = [threading.Thread(target=write) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    result = audit_log.verify()
    assert (result.valid, result.event_count) == (True, 20)


@pytest.mark.req("SEC-IDOR-001")
@pytest.mark.parametrize("implementation", [InMemoryAuditLog, SqliteAuditLog])
def test_record_reading_methods_take_the_owner_scope(implementation: type) -> None:
    for name in ("events_for", "forget"):
        parameters = list(inspect.signature(getattr(implementation, name)).parameters)
        assert parameters[1] == "scope", f"{implementation.__name__}.{name}"
