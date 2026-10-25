"""Data quality issues shown to the athlete (FR-ACT-009, FR-ACT-007).

ANOMALY issues mirror an anomaly on an activity field; AMBIGUOUS_MATCH issues ask the athlete
whether a pending activity is the same session as an existing one
(spec/functional/activities.spec.md section 5.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.activity.activity import check_semver
from coach.domain.activity.fields import field_spec
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.common.numbers import require_finite
from coach.domain.common.quality import Anomaly
from coach.domain.common.text import clean_optional_text
from coach.domain.common.time import ensure_utc

AMBIGUOUS_MATCH_CODE: Final = "AMBIGUOUS_MATCH"
_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class IssueKind(StrEnum):
    ANOMALY = "ANOMALY"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"


class IssueStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class IssueResolution(StrEnum):
    MERGED = "MERGED"
    KEPT_SEPARATE = "KEPT_SEPARATE"
    NO_LONGER_DETECTED = "NO_LONGER_DETECTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


RESOLUTIONS: Final = {
    IssueKind.ANOMALY: frozenset(
        {IssueResolution.NO_LONGER_DETECTED, IssueResolution.ACKNOWLEDGED}
    ),
    IssueKind.AMBIGUOUS_MATCH: frozenset({IssueResolution.MERGED, IssueResolution.KEPT_SEPARATE}),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class DataQualityIssue:
    issue_id: UUID
    athlete_id: UUID
    kind: IssueKind
    activity_id: UUID
    code: str
    field: str | None
    created_at: datetime
    detail: str | None = None
    related_activity_id: UUID | None = None
    match_score: float | None = None
    dedup_rules_version: str | None = None
    status: IssueStatus = IssueStatus.OPEN
    closed_at: datetime | None = None
    resolution: IssueResolution | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if not _CODE.match(self.code):
            raise DomainValidationError(f"invalid issue code: {self.code!r}")
        object.__setattr__(
            self, "detail", clean_optional_text(self.detail, max_length=300, field="detail")
        )
        if self.kind is IssueKind.AMBIGUOUS_MATCH:
            self._check_ambiguous_match()
        else:
            self._check_anomaly()
        self._check_status()

    def _check_ambiguous_match(self) -> None:
        if self.code != AMBIGUOUS_MATCH_CODE or self.field is not None:
            raise DomainValidationError("an ambiguous match has its own code and no field")
        if (
            self.related_activity_id is None
            or self.match_score is None
            or self.dedup_rules_version is None
        ):
            raise DomainValidationError("an ambiguous match names its candidate, score and rules")
        if self.related_activity_id == self.activity_id:
            raise DomainValidationError("an activity is not ambiguous with itself")
        if not 0 <= require_finite(self.match_score, "match score") <= 1:
            raise DomainValidationError("match score must be between 0 and 1")
        check_semver(self.dedup_rules_version, "dedup rules version")

    def _check_anomaly(self) -> None:
        if self.field is None:
            raise DomainValidationError("an anomaly issue names its field")
        field_spec(self.field)
        if (
            self.related_activity_id is not None
            or self.match_score is not None
            or self.dedup_rules_version is not None
        ):
            raise DomainValidationError("only ambiguous matches name a candidate")

    def _check_status(self) -> None:
        closed = self.status is IssueStatus.CLOSED
        if closed != (self.closed_at is not None) or closed != (self.resolution is not None):
            raise DomainValidationError("closed issues, and only they, have a time and resolution")
        if self.closed_at is not None:
            object.__setattr__(self, "closed_at", ensure_utc(self.closed_at))
        if self.resolution is not None and self.resolution not in RESOLUTIONS[self.kind]:
            raise DomainValidationError(f"{self.resolution} does not resolve {self.kind}")

    @classmethod
    def for_anomaly(
        cls,
        *,
        issue_id: UUID,
        athlete_id: UUID,
        activity_id: UUID,
        anomaly: Anomaly,
        created_at: datetime,
    ) -> DataQualityIssue:
        return cls(
            issue_id=issue_id,
            athlete_id=athlete_id,
            kind=IssueKind.ANOMALY,
            activity_id=activity_id,
            code=anomaly.code,
            field=anomaly.field,
            detail=anomaly.detail or None,
            created_at=created_at,
        )

    @classmethod
    def for_ambiguous_match(
        cls,
        *,
        issue_id: UUID,
        athlete_id: UUID,
        activity_id: UUID,
        related_activity_id: UUID,
        match_score: float,
        dedup_rules_version: str,
        created_at: datetime,
    ) -> DataQualityIssue:
        return cls(
            issue_id=issue_id,
            athlete_id=athlete_id,
            kind=IssueKind.AMBIGUOUS_MATCH,
            activity_id=activity_id,
            code=AMBIGUOUS_MATCH_CODE,
            field=None,
            related_activity_id=related_activity_id,
            match_score=match_score,
            dedup_rules_version=dedup_rules_version,
            created_at=created_at,
        )

    @property
    def entity_id(self) -> UUID:
        return self.issue_id

    def with_version(self, version: int) -> DataQualityIssue:
        return replace(self, version=version)

    @property
    def is_open(self) -> bool:
        return self.status is IssueStatus.OPEN

    def anomaly_key(self) -> tuple[UUID, str, str | None]:
        return self.activity_id, self.code, self.field

    def close(self, resolution: IssueResolution, *, at: datetime) -> DataQualityIssue:
        if not self.is_open:
            raise InvalidTransitionError("issue is already closed")
        if resolution not in RESOLUTIONS[self.kind]:
            raise InvalidTransitionError(f"{resolution} does not resolve {self.kind}")
        return replace(self, status=IssueStatus.CLOSED, closed_at=at, resolution=resolution)
