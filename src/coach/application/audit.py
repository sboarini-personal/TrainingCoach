"""Hash-chained audit events with pseudonymous identifiers (SEC-AUD-001, ADR-023).

``seal`` turns an event into its contract document: every identifier becomes a salted hash,
and the document hashes its predecessor. ``verify_chain`` recomputes the chain. Both are pure;
storage and the salt belong to the ``AuditLog`` adapters.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import ensure_utc

SCHEMA_VERSION: Final = "1.0.0"
GENESIS_HASH: Final = "0" * 64

_ACTION: Final = re.compile(r"^[a-z]+(\.[a-z_]+)+$")
_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TARGET_ID: Final = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_SALT: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_CODE_LENGTH: Final = 64
_MAX_REASON_CODES: Final = 20


class ActorKind(StrEnum):
    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class AuditOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    FAILED = "FAILED"


def _is_code(value: str) -> bool:
    return len(value) <= _MAX_CODE_LENGTH and bool(_CODE.match(value))


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditEvent:
    """What happened, in codes and identifiers only; no free text."""

    occurred_at: datetime
    actor: ActorKind
    action: str
    outcome: AuditOutcome
    athlete_id: UUID | None = None
    target_type: str | None = None
    target_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    correlation_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))
        if len(self.action) > _MAX_CODE_LENGTH or not _ACTION.match(self.action):
            raise DomainValidationError(f"invalid audit action: {self.action!r}")
        if (self.target_type is None) != (self.target_id is None):
            raise DomainValidationError("an audit target has both a type and an id")
        if self.target_type is not None and not _is_code(self.target_type):
            raise DomainValidationError(f"invalid audit target type: {self.target_type!r}")
        if self.target_id is not None and not _TARGET_ID.match(self.target_id):
            raise DomainValidationError("invalid audit target id")
        codes = self.reason_codes
        if len(codes) > _MAX_REASON_CODES or len(set(codes)) != len(codes):
            raise DomainValidationError("audit reason codes must be unique and at most 20")
        if not all(_is_code(code) for code in codes):
            raise DomainValidationError("audit reason codes are upper-case codes")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """A sealed event: the contract document plus the plain athlete ID kept outside the hash."""

    document: Mapping[str, Any]
    athlete_id: UUID | None

    @property
    def sequence(self) -> int:
        return int(self.document["sequence"])

    @property
    def hash(self) -> str:
        return str(self.document["hash"])

    @property
    def previous_hash(self) -> str:
        return str(self.document["previousHash"])


@dataclass(frozen=True, slots=True)
class AuditVerification:
    event_count: int
    first_invalid_sequence: int | None = None
    problem: str | None = None

    @property
    def valid(self) -> bool:
        return self.problem is None


def pseudonym(salt: str, value: str) -> str:
    """Salted SHA-256 of an identifier (PRIV-DEL-003)."""
    if not _SALT.match(salt):
        raise DomainValidationError("audit salt must be 64 hexadecimal characters")
    return hashlib.sha256(bytes.fromhex(salt) + value.encode("utf-8")).hexdigest()


def content_hash(document: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of every field except ``hash``."""
    body = {key: value for key, value in document.items() if key != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def seal(
    event: AuditEvent, *, event_id: UUID, sequence: int, previous_hash: str, salt: str
) -> AuditRecord:
    target = None
    if event.target_type is not None and event.target_id is not None:
        target = {"type": event.target_type, "ref": pseudonym(salt, event.target_id)}
    document: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "eventId": str(event_id),
        "sequence": sequence,
        "occurredAt": _instant(event.occurred_at),
        "actor": event.actor.value,
        "action": event.action,
        "outcome": event.outcome.value,
        "athleteRef": None if event.athlete_id is None else pseudonym(salt, str(event.athlete_id)),
        "target": target,
        "reasonCodes": list(event.reason_codes),
        "correlationId": None if event.correlation_id is None else str(event.correlation_id),
        "previousHash": previous_hash,
    }
    document["hash"] = content_hash(document)
    return AuditRecord(document, event.athlete_id)


def verify_chain(records: Iterable[AuditRecord], salt: str) -> AuditVerification:
    """Check sequence, hashes, links and plain athlete column; report the first problem."""
    previous = GENESIS_HASH
    verified = 0
    for expected, record in enumerate(records, start=1):
        document = record.document
        sequence = document.get("sequence")
        problem = None
        if sequence != expected:
            problem = "SEQUENCE_GAP"
        elif content_hash(document) != document.get("hash"):
            problem = "HASH_MISMATCH"
        elif document.get("previousHash") != previous:
            problem = "BROKEN_LINK"
        elif record.athlete_id is not None and document.get("athleteRef") != pseudonym(
            salt, str(record.athlete_id)
        ):
            problem = "ATHLETE_MISMATCH"
        if problem is not None:
            return AuditVerification(verified, int(sequence) if sequence else expected, problem)
        previous = record.hash
        verified += 1
    return AuditVerification(verified)
