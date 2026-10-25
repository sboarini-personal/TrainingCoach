"""Consent ledger (PRIV-CON-001 to PRIV-CON-004).

Append-only. The current state of a consent type is its latest record. Only ``GRANTED``
under the required policy version allows processing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.text import clean_text
from coach.domain.common.time import ensure_utc

_POLICY_VERSION = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?$")


class ConsentType(StrEnum):
    ACTIVITIES = "ACTIVITIES"
    RECOVERY = "RECOVERY"
    SLEEP = "SLEEP"
    HRV = "HRV"
    PROVIDER_RECOVERY_METRICS = "PROVIDER_RECOVERY_METRICS"
    LONG_TERM_STORAGE = "LONG_TERM_STORAGE"
    AI_PROCESSING = "AI_PROCESSING"
    WOMENS_HEALTH = "WOMENS_HEALTH"


NOT_OFFERED_IN_V1: Final = frozenset({ConsentType.WOMENS_HEALTH})


class ConsentStatus(StrEnum):
    GRANTED = "GRANTED"
    NOT_GRANTED = "NOT_GRANTED"
    DECLINED = "DECLINED"
    REVOKED = "REVOKED"
    OUTDATED_POLICY = "OUTDATED_POLICY"


@dataclass(frozen=True, slots=True, kw_only=True)
class ConsentRecord:
    consent_id: UUID
    athlete_id: UUID
    consent_type: ConsentType
    granted: bool
    granted_at: datetime | None
    revoked_at: datetime | None
    policy_version: str
    purpose: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        for name in ("granted_at", "revoked_at", "recorded_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value))
        if self.granted and (self.granted_at is None or self.revoked_at is not None):
            raise DomainValidationError("a grant has a grant time and no revocation time")
        if not self.granted and self.revoked_at is None:
            raise DomainValidationError("a revocation or decline has a decision time")
        if not _POLICY_VERSION.match(self.policy_version):
            raise DomainValidationError(f"invalid policy version: {self.policy_version!r}")
        object.__setattr__(
            self, "purpose", clean_text(self.purpose, max_length=500, field="purpose")
        )


@dataclass(frozen=True, slots=True)
class ConsentLedger:
    athlete_id: UUID
    records: tuple[ConsentRecord, ...] = ()
    version: int = 1

    @classmethod
    def new(cls, athlete_id: UUID) -> ConsentLedger:
        return cls(athlete_id)

    @property
    def entity_id(self) -> UUID:
        return self.athlete_id

    def with_version(self, version: int) -> ConsentLedger:
        return replace(self, version=version)

    def latest(self, consent_type: ConsentType) -> ConsentRecord | None:
        matching = [record for record in self.records if record.consent_type is consent_type]
        return matching[-1] if matching else None

    def has_decision(self, consent_type: ConsentType) -> bool:
        return self.latest(consent_type) is not None

    def status(self, consent_type: ConsentType, *, policy_version: str) -> ConsentStatus:
        latest = self.latest(consent_type)
        if latest is None:
            return ConsentStatus.NOT_GRANTED
        if not latest.granted:
            return ConsentStatus.DECLINED if latest.granted_at is None else ConsentStatus.REVOKED
        if latest.policy_version != policy_version:
            return ConsentStatus.OUTDATED_POLICY
        return ConsentStatus.GRANTED

    def is_granted(self, consent_type: ConsentType, *, policy_version: str) -> bool:
        return self.status(consent_type, policy_version=policy_version) is ConsentStatus.GRANTED

    def grant(
        self,
        *,
        consent_id: UUID,
        consent_type: ConsentType,
        policy_version: str,
        purpose: str,
        at: datetime,
    ) -> ConsentLedger:
        if consent_type in NOT_OFFERED_IN_V1:
            raise DomainValidationError(f"{consent_type} cannot be granted in V1")
        if self.is_granted(consent_type, policy_version=policy_version):
            return self
        return self._append(
            ConsentRecord(
                consent_id=consent_id,
                athlete_id=self.athlete_id,
                consent_type=consent_type,
                granted=True,
                granted_at=at,
                revoked_at=None,
                policy_version=policy_version,
                purpose=purpose,
                recorded_at=at,
            )
        )

    def revoke(self, *, consent_id: UUID, consent_type: ConsentType, at: datetime) -> ConsentLedger:
        current = self.latest(consent_type)
        if current is None or not current.granted:
            raise DomainValidationError(f"{consent_type} is not granted")
        return self._append(
            ConsentRecord(
                consent_id=consent_id,
                athlete_id=self.athlete_id,
                consent_type=consent_type,
                granted=False,
                granted_at=current.granted_at,
                revoked_at=at,
                policy_version=current.policy_version,
                purpose=current.purpose,
                recorded_at=at,
            )
        )

    def decline(
        self,
        *,
        consent_id: UUID,
        consent_type: ConsentType,
        policy_version: str,
        purpose: str,
        at: datetime,
    ) -> ConsentLedger:
        current = self.latest(consent_type)
        if current is not None and current.granted:
            raise DomainValidationError(f"{consent_type} is granted; revoke it instead")
        return self._append(
            ConsentRecord(
                consent_id=consent_id,
                athlete_id=self.athlete_id,
                consent_type=consent_type,
                granted=False,
                granted_at=None,
                revoked_at=at,
                policy_version=policy_version,
                purpose=purpose,
                recorded_at=at,
            )
        )

    def _append(self, record: ConsentRecord) -> ConsentLedger:
        if self.records and record.recorded_at < self.records[-1].recorded_at:
            raise DomainValidationError("consent records must not go back in time")
        return replace(self, records=(*self.records, record))
