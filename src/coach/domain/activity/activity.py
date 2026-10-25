"""Canonical logical Activity (FR-ACT-001; spec/integration/canonical-model.spec.md).

One Activity is one logical session linked to one or more sources (ADR-004). Field values are
keyed by field path: a missing path is not applicable, ``None`` is UNKNOWN. The stored
Activity is the NORMALIZED layer; the EFFECTIVE view is an Activity with corrections applied.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from coach.domain.activity.fields import (
    CATALOG,
    REQUIRED_FIELDS,
    STREAM_KEYS,
    StreamRef,
    check_streams,
    check_subtype,
    check_values,
    date_of,
    instant_of,
    known_number,
    sport_of,
    subtype_of,
)
from coach.domain.activity.observation import Observation, SourceKey
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.quality import DataQuality
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_optional_text
from coach.domain.common.time import ensure_utc
from coach.domain.raw.source import SourceOrigin, require_sha256

_SEMVER: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_RULE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
_PROVENANCE_KEYS: Final = frozenset(CATALOG) | frozenset(STREAM_KEYS.values())


class ActivityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PENDING_REVIEW = "PENDING_REVIEW"
    MERGED = "MERGED"


class LinkKind(StrEnum):
    PRIMARY = "PRIMARY"
    ENRICHMENT = "ENRICHMENT"


def check_semver(value: str, name: str) -> str:
    if not _SEMVER.match(value):
        raise DomainValidationError(f"{name} must be MAJOR.MINOR.PATCH")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceLink:
    source_id: UUID
    part: int = 0
    origin: SourceOrigin
    provider: str | None
    provider_activity_id: str | None
    file_hash: str | None
    link_kind: LinkKind
    linked_at: datetime
    match_score: float | None = None
    dedup_rules_version: str | None = None

    def __post_init__(self) -> None:
        SourceKey(self.source_id, self.part)
        object.__setattr__(self, "linked_at", ensure_utc(self.linked_at))
        if (self.origin is SourceOrigin.FILE_UPLOAD) != (self.file_hash is not None):
            raise DomainValidationError("uploaded files, and only they, carry a file hash")
        if self.file_hash is not None:
            require_sha256(self.file_hash)
        if self.match_score is not None and not 0 <= require_finite(self.match_score, "score") <= 1:
            raise DomainValidationError("match score must be between 0 and 1")
        if self.match_score is not None and self.dedup_rules_version is None:
            raise DomainValidationError("a scored link names its deduplication rules")
        if self.dedup_rules_version is not None:
            check_semver(self.dedup_rules_version, "dedup rules version")
        object.__setattr__(
            self,
            "provider_activity_id",
            clean_optional_text(
                self.provider_activity_id, max_length=100, field="provider activity id"
            ),
        )

    @property
    def key(self) -> SourceKey:
        return SourceKey(self.source_id, self.part)

    @classmethod
    def of(
        cls,
        observation: Observation,
        *,
        link_kind: LinkKind,
        linked_at: datetime,
        match_score: float | None = None,
        dedup_rules_version: str | None = None,
    ) -> SourceLink:
        uploaded = observation.origin is SourceOrigin.FILE_UPLOAD
        return cls(
            source_id=observation.raw_source_id,
            part=observation.part,
            origin=observation.origin,
            provider=observation.provider,
            provider_activity_id=observation.provider_activity_id,
            file_hash=observation.raw_sha256 if uploaded else None,
            link_kind=link_kind,
            linked_at=linked_at,
            match_score=match_score,
            dedup_rules_version=dedup_rules_version,
        )


@dataclass(frozen=True, slots=True)
class FieldProvenance:
    """Which source a resolved field came from, by which rule, and any correction."""

    source_id: UUID
    rule: str
    path: str | None = None
    override_id: UUID | None = None

    def __post_init__(self) -> None:
        if not _RULE.match(self.rule):
            raise DomainValidationError(f"invalid resolution rule: {self.rule!r}")
        object.__setattr__(
            self, "path", clean_optional_text(self.path, max_length=300, field="source path")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Activity:
    activity_id: UUID
    athlete_id: UUID
    status: ActivityStatus
    sources: tuple[SourceLink, ...]
    imported_at: datetime
    resolution_rules_version: str
    quality_rules_version: str
    values: Mapping[str, object]
    provenance: Mapping[str, FieldProvenance]
    quality: DataQuality
    streams: tuple[StreamRef, ...] = ()
    merged_into: UUID | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "imported_at", ensure_utc(self.imported_at))
        check_semver(self.resolution_rules_version, "resolution rules version")
        check_semver(self.quality_rules_version, "quality rules version")
        self._check_sources()
        if (self.status is ActivityStatus.MERGED) != (self.merged_into is not None):
            raise DomainValidationError("MERGED activities, and only they, name their target")
        if self.merged_into == self.activity_id:
            raise DomainValidationError("an activity cannot be merged into itself")
        values = check_values(self.values)
        missing = sorted(REQUIRED_FIELDS - set(values))
        if missing:
            raise DomainValidationError(f"missing required fields: {missing}")
        check_subtype(sport_of(values), subtype_of(values))
        object.__setattr__(self, "values", MappingProxyType(values))
        self._check_provenance()
        check_streams(self.streams)

    def _check_sources(self) -> None:
        if not self.sources:
            raise DomainValidationError("an activity has at least one source")
        source_ids = [link.source_id for link in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise DomainValidationError("a raw source links at most one part to an activity")
        primaries = [link for link in self.sources if link.link_kind is LinkKind.PRIMARY]
        if len(primaries) != 1:
            raise DomainValidationError("an activity has exactly one primary source")

    def _check_provenance(self) -> None:
        source_ids = {link.source_id for link in self.sources}
        for key, origin in self.provenance.items():
            if key not in _PROVENANCE_KEYS:
                raise DomainValidationError(f"provenance for an unknown field: {key!r}")
            if origin.source_id not in source_ids:
                raise DomainValidationError("provenance names a source the activity lacks")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

    @property
    def entity_id(self) -> UUID:
        return self.activity_id

    def with_version(self, version: int) -> Activity:
        return replace(self, version=version)

    @property
    def sport(self) -> Sport:
        return sport_of(self.values)

    @property
    def start_time_utc(self) -> datetime:
        return instant_of(self.values, "timing.startTimeUtc")

    @property
    def training_day(self) -> date:
        return date_of(self.values, "timing.trainingDay")

    @property
    def primary_source(self) -> SourceLink:
        return next(link for link in self.sources if link.link_kind is LinkKind.PRIMARY)

    @property
    def source_keys(self) -> tuple[SourceKey, ...]:
        return tuple(link.key for link in self.sources)

    @property
    def is_live(self) -> bool:
        """ACTIVE or PENDING_REVIEW: can receive sources and corrections."""
        return self.status is not ActivityStatus.MERGED

    @property
    def counts_toward_totals(self) -> bool:
        return self.status is ActivityStatus.ACTIVE

    def number(self, path: str) -> float | None:
        return known_number(self.values, path)
