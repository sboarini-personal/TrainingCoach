"""No workout publication without the capability (AT-GAR-001; INT-GAR-002 to INT-GAR-005)."""

import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from coach.application.consent import ConsentGate
from coach.application.errors import NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.policy_gate import PolicyGate
from coach.application.workouts import (
    ConfirmationToken,
    LocalWorkoutPublisher,
    PublishStatus,
)
from coach.domain.coaching.state import CoachingState
from coach.domain.common.errors import DomainValidationError
from coach.domain.consent.ledger import ConsentLedger
from coach.domain.integration.capabilities import Capability, CapabilityValue
from coach.domain.integration.status import IntegrationStatus
from coach.infrastructure.memory import InMemoryAuditLog, InMemoryRepository, RecordingEventSink
from coach.integrations.garmin.capabilities import personal_capabilities
from samples import ATHLETE, OTHER_ATHLETE

SCOPE = OwnerScope(ATHLETE)
NOW = datetime(2026, 10, 26, 18, 0, tzinfo=UTC)
WORKOUT: dict[str, Any] = {
    "schemaVersion": "1.0.0",
    "id": "0193b2a0-0000-7000-8000-000000000501",
    "type": "ENDURANCE",
    "trainingDay": "2026-10-28",
    "order": 0,
    "isKeyWorkout": True,
    "objective": "Aerobic base",
    "explanation": {"what": "45 min easy run", "why": "Build aerobic base"},
    "endurance": {"sport": "RUNNING"},
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[object]]:
    """Any attempt to open a connection fails the test (AT-GAR-001: no network request)."""
    attempts: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    yield attempts
    assert attempts == []


class World:
    def __init__(self) -> None:
        self.integrations = InMemoryRepository[IntegrationStatus]()
        self.audit, self.events = InMemoryAuditLog(), RecordingEventSink()
        gate = PolicyGate(
            ConsentGate(InMemoryRepository[ConsentLedger](), policy_version="1.0"),
            InMemoryRepository[CoachingState](),
            audit=self.audit,
            events=self.events,
            clock=lambda: NOW,
            integrations=self.integrations,
        )
        self.integrations.add(
            SCOPE,
            IntegrationStatus(
                integration_id=UUID(int=7),
                athlete_id=ATHLETE,
                provider="garmin",
                capabilities=personal_capabilities(),
            ),
        )
        self.publisher = LocalWorkoutPublisher(
            gate, audit=self.audit, events=self.events, clock=lambda: NOW
        )


@pytest.mark.req("INT-GAR-002", "INT-GAR-003", "INT-GAR-004", "INT-GAR-005")
@pytest.mark.acceptance("AT-GAR-001")
def test_publication_without_training_publish_is_capability_unavailable() -> None:
    world = World()
    status = world.integrations.list_all(SCOPE)[0]
    assert status.capabilities.value(Capability.TRAINING_PUBLISH) is CapabilityValue.UNSUPPORTED
    proposal = world.publisher.propose_workout(SCOPE, WORKOUT)
    assert proposal.document == WORKOUT
    assert proposal.workout_id == UUID(WORKOUT["id"])
    result = world.publisher.publish_workout(SCOPE, proposal.proposal_id)
    assert result.status is PublishStatus.CAPABILITY_UNAVAILABLE
    assert result.reason_codes == ("CAPABILITY_UNAVAILABLE",)
    confirmed = world.publisher.publish_workout(
        SCOPE, proposal.proposal_id, ConfirmationToken(proposal.proposal_id, "human")
    )
    assert confirmed.status is PublishStatus.CAPABILITY_UNAVAILABLE
    assert not hasattr(world.publisher, "_transport")
    names = world.events.names()
    assert names.count(EventName.EXTERNAL_WRITE_PROPOSED) == 1
    assert names.count(EventName.EXTERNAL_WRITE_REFUSED) == 2
    denials = [
        record.document
        for record in world.audit.events_for(SCOPE)
        if record.document["action"] == "workout.publish"
    ]
    assert [(item["outcome"], item["reasonCodes"]) for item in denials] == [
        ("DENIED", ["CAPABILITY_UNAVAILABLE"]),
        ("DENIED", ["CAPABILITY_UNAVAILABLE"]),
    ]


@pytest.mark.req("INT-GAR-002")
@pytest.mark.acceptance("AT-GAR-001")
def test_proposing_is_local_and_idempotent() -> None:
    world = World()
    first = world.publisher.propose_workout(SCOPE, WORKOUT)
    again = world.publisher.propose_workout(SCOPE, dict(reversed(list(WORKOUT.items()))))
    assert again == first
    changed = world.publisher.propose_workout(SCOPE, {**WORKOUT, "objective": "Tempo"})
    assert changed.proposal_id != first.proposal_id


@pytest.mark.req("INT-GAR-002", "SEC-AUTHZ-001")
def test_proposals_are_owner_scoped() -> None:
    world = World()
    proposal = world.publisher.propose_workout(SCOPE, WORKOUT)
    with pytest.raises(NotFoundError):
        world.publisher.publish_workout(OwnerScope(OTHER_ATHLETE), proposal.proposal_id)
    with pytest.raises(NotFoundError):
        world.publisher.publish_workout(SCOPE, UUID(int=99))


@pytest.mark.req("INT-GAR-002")
@pytest.mark.parametrize(
    "broken",
    [
        {k: v for k, v in WORKOUT.items() if k != "id"},
        {**WORKOUT, "id": "not-a-uuid"},
        {**WORKOUT, "type": "SWIM"},
        {k: v for k, v in WORKOUT.items() if k != "schemaVersion"},
        {**WORKOUT, "objective": float("nan")},
    ],
)
def test_workout_documents_need_their_identity(broken: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        World().publisher.propose_workout(SCOPE, broken)
