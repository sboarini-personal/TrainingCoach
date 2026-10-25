"""Audit events, sealing and chain verification (SEC-AUD-001, PRIV-DEL-003, ADR-023)."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from coach.application.audit import (
    GENESIS_HASH,
    ActorKind,
    AuditEvent,
    AuditOutcome,
    AuditRecord,
    content_hash,
    pseudonym,
    seal,
    verify_chain,
)
from coach.domain.common.errors import DomainValidationError
from samples import ATHLETE, OTHER_ATHLETE
from schemas import EXAMPLES_DIR, load_json, validation_errors

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
SALT = "5a" * 32
CONSENT_ID = UUID("0193b2a0-0000-7000-8000-000000001001")


def event(**overrides: Any) -> AuditEvent:
    values: dict[str, Any] = {
        "occurred_at": NOW,
        "actor": ActorKind.USER,
        "action": "consent.granted",
        "outcome": AuditOutcome.SUCCESS,
        "athlete_id": ATHLETE,
        "target_type": "HRV",
        "target_id": str(CONSENT_ID),
        "reason_codes": (),
    }
    values.update(overrides)
    return AuditEvent(**values)


def chain(count: int) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    previous = GENESIS_HASH
    for number in range(1, count + 1):
        record = seal(
            event(), event_id=UUID(int=number), sequence=number, previous_hash=previous, salt=SALT
        )
        records.append(record)
        previous = record.hash
    return records


# ------------------------------------------------------------------ events


@pytest.mark.req("SEC-AUD-001")
@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "Consent Granted"},
        {"action": "consent"},
        {"reason_codes": ("consent required",)},
        {"reason_codes": ("A", "A")},
        {"target_type": "hrv"},
        {"target_type": "HRV", "target_id": None},
        {"target_type": None, "target_id": "x"},
        {"target_id": "free text with spaces"},
        {"occurred_at": datetime(2026, 10, 25, 10, 0)},  # noqa: DTZ001
    ],
)
def test_events_hold_only_codes_and_identifiers(overrides: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        event(**overrides)


# ------------------------------------------------------------------ sealing


@pytest.mark.req("SEC-AUD-001", "PRIV-DEL-003")
def test_sealed_document_is_the_contract_with_pseudonymous_identifiers() -> None:
    record = seal(event(), event_id=UUID(int=1), sequence=1, previous_hash=GENESIS_HASH, salt=SALT)
    document = dict(record.document)
    assert validation_errors("audit-event", document) == []
    assert document["athleteRef"] == pseudonym(SALT, str(ATHLETE))
    assert document["target"] == {"type": "HRV", "ref": pseudonym(SALT, str(CONSENT_ID))}
    assert str(ATHLETE) not in str(document)
    assert str(CONSENT_ID) not in str(document)
    assert record.athlete_id == ATHLETE
    assert (record.sequence, record.previous_hash) == (1, GENESIS_HASH)


@pytest.mark.req("PRIV-DEL-003")
def test_pseudonyms_depend_on_the_salt() -> None:
    assert pseudonym(SALT, str(ATHLETE)) != pseudonym("6b" * 32, str(ATHLETE))
    assert pseudonym(SALT, str(ATHLETE)) != pseudonym(SALT, str(OTHER_ATHLETE))
    with pytest.raises(DomainValidationError):
        pseudonym("short", str(ATHLETE))


def test_events_without_athlete_or_target_have_null_refs() -> None:
    anonymous = event(
        athlete_id=None,
        target_type=None,
        target_id=None,
        outcome=AuditOutcome.FAILED,
        action="auth.authenticate",
        reason_codes=("INVALID_CREDENTIALS",),
    )
    document = seal(
        anonymous, event_id=UUID(int=1), sequence=1, previous_hash=GENESIS_HASH, salt=SALT
    ).document
    assert (document["athleteRef"], document["target"]) == (None, None)
    assert validation_errors("audit-event", dict(document)) == []


# ------------------------------------------------------------------ verification


@pytest.mark.req("SEC-AUD-001")
@pytest.mark.acceptance("AT-AUD-001")
def test_intact_chain_verifies() -> None:
    result = verify_chain(chain(4), SALT)
    assert result.valid
    assert result.event_count == 4
    assert verify_chain([], SALT).valid


@pytest.mark.req("SEC-AUD-001")
@pytest.mark.acceptance("AT-AUD-001")
def test_edited_event_is_detected() -> None:
    records = chain(4)
    tampered = dict(records[1].document)
    tampered["outcome"] = "DENIED"
    records[1] = replace(records[1], document=tampered)
    result = verify_chain(records, SALT)
    assert (result.valid, result.first_invalid_sequence, result.problem) == (
        False,
        2,
        "HASH_MISMATCH",
    )


@pytest.mark.req("SEC-AUD-001")
@pytest.mark.acceptance("AT-AUD-001")
def test_removed_event_is_detected() -> None:
    records = chain(4)
    del records[2]
    result = verify_chain(records, SALT)
    assert (result.valid, result.first_invalid_sequence, result.problem) == (
        False,
        4,
        "SEQUENCE_GAP",
    )


@pytest.mark.req("SEC-AUD-001")
@pytest.mark.acceptance("AT-AUD-001")
def test_resealed_event_breaks_the_link_to_the_next_one() -> None:
    records = chain(3)
    forged = seal(
        event(outcome=AuditOutcome.DENIED),
        event_id=UUID(int=2),
        sequence=2,
        previous_hash=records[0].hash,
        salt=SALT,
    )
    records[1] = forged
    result = verify_chain(records, SALT)
    assert (result.valid, result.first_invalid_sequence, result.problem) == (
        False,
        3,
        "BROKEN_LINK",
    )


@pytest.mark.req("SEC-AUD-001")
def test_reassigning_the_plain_athlete_column_is_detected() -> None:
    records = chain(2)
    records[0] = replace(records[0], athlete_id=OTHER_ATHLETE)
    result = verify_chain(records, SALT)
    assert (result.valid, result.first_invalid_sequence, result.problem) == (
        False,
        1,
        "ATHLETE_MISMATCH",
    )


@pytest.mark.req("PRIV-DEL-003")
@pytest.mark.acceptance("AT-AUD-001")
def test_forgotten_athlete_keeps_the_chain_valid() -> None:
    forgotten = [replace(record, athlete_id=None) for record in chain(3)]
    assert verify_chain(forgotten, SALT).valid


@pytest.mark.req("SEC-AUD-001")
def test_published_example_hash_is_correct() -> None:
    example = load_json(EXAMPLES_DIR / "audit-event.example.json")
    assert validation_errors("audit-event", example) == []
    assert content_hash(example) == example["hash"]
