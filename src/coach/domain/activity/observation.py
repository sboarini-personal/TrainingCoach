"""Source observations: the normalised content of one part of one RawSource.

An observation lists the fields its source provides, keyed by canonical field path
(spec/integration/canonical-model.spec.md section 6). It is immutable; Activities are resolved
from their sources' observations (spec/functional/activities.spec.md section 3).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from coach.domain.activity.fields import (
    OBSERVABLE_FIELDS,
    SUBJECTIVE_FIELDS,
    StreamRef,
    check_streams,
    check_subtype,
    check_values,
    instant_of,
    sport_of,
    subtype_of,
)
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import Anomaly
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_optional_text, clean_text
from coach.domain.common.time import ensure_utc
from coach.domain.raw.source import DetectedType, SourceOrigin, require_sha256

_NAME: Final = re.compile(r"^[a-z0-9_]{1,40}$")
_PROVIDER: Final = re.compile(r"^[a-z0-9_]+$")
_SEMVER: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class SourceKind(StrEnum):
    FIT_FILE = "FIT_FILE"
    PROVIDER_RECORD = "PROVIDER_RECORD"
    OTHER_FILE = "OTHER_FILE"
    ATHLETE_ENTRY = "ATHLETE_ENTRY"


_FILE_ORIGINS: Final = frozenset({SourceOrigin.FILE_UPLOAD, SourceOrigin.GARMIN_PERSONAL_API})
_ORIGINS: Final = {
    # A file downloaded from a provider is still a file (files spec 9.1).
    SourceKind.FIT_FILE: _FILE_ORIGINS,
    SourceKind.OTHER_FILE: _FILE_ORIGINS,
    SourceKind.PROVIDER_RECORD: frozenset({SourceOrigin.GARMIN_PERSONAL_API}),
    # The CSV activity template holds the athlete's own declarations (files spec 8.4).
    SourceKind.ATHLETE_ENTRY: frozenset({SourceOrigin.MANUAL_ENTRY, SourceOrigin.FILE_UPLOAD}),
}
_PROVIDER_KINDS: Final = {
    DetectedType.JSON: SourceKind.PROVIDER_RECORD,
    DetectedType.FIT: SourceKind.FIT_FILE,
    DetectedType.TCX: SourceKind.OTHER_FILE,
    DetectedType.GPX: SourceKind.OTHER_FILE,
}


def source_kind_of(origin: SourceOrigin, detected_type: DetectedType) -> SourceKind:
    if origin is SourceOrigin.GARMIN_PERSONAL_API:
        kind = _PROVIDER_KINDS.get(detected_type)
        if kind is None:
            raise DomainValidationError(f"a provider does not supply {detected_type} sources")
        return kind
    if origin is SourceOrigin.MANUAL_ENTRY or detected_type is DetectedType.CSV:
        return SourceKind.ATHLETE_ENTRY
    return SourceKind.FIT_FILE if detected_type is DetectedType.FIT else SourceKind.OTHER_FILE


@dataclass(frozen=True, slots=True)
class SourceKey:
    """One part of one raw source; ``part`` is 0 unless a file holds several sessions."""

    raw_source_id: UUID
    part: int = 0

    def __post_init__(self) -> None:
        if self.part < 0:
            raise DomainValidationError("source part must not be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class Observation:
    raw_source_id: UUID
    part: int = 0
    athlete_id: UUID
    origin: SourceOrigin
    kind: SourceKind
    raw_sha256: str
    received_at: datetime
    normaliser: str
    normaliser_version: str
    values: Mapping[str, object]
    provider: str | None = None
    provider_activity_id: str | None = None
    source_paths: Mapping[str, str] = field(default_factory=dict)
    streams: tuple[StreamRef, ...] = ()
    anomalies: tuple[Anomaly, ...] = ()

    def __post_init__(self) -> None:
        SourceKey(self.raw_source_id, self.part)
        if self.origin not in _ORIGINS[self.kind]:
            raise DomainValidationError(f"{self.kind} sources cannot come from {self.origin}")
        require_sha256(self.raw_sha256)
        object.__setattr__(self, "received_at", ensure_utc(self.received_at))
        if not _NAME.match(self.normaliser) or not _SEMVER.match(self.normaliser_version):
            raise DomainValidationError("normaliser needs a name and a MAJOR.MINOR.PATCH version")
        self._check_provider()
        self._check_values()
        check_streams(self.streams)
        if self.kind is SourceKind.ATHLETE_ENTRY and self.streams:
            raise DomainValidationError("athlete entries carry no streams")

    def _check_provider(self) -> None:
        record = self.kind is SourceKind.PROVIDER_RECORD
        provider_id = clean_optional_text(
            self.provider_activity_id, max_length=100, field="provider activity id"
        )
        object.__setattr__(self, "provider_activity_id", provider_id)
        if record != (self.provider is not None) or record != (provider_id is not None):
            raise DomainValidationError(
                "provider records, and only they, name their provider and record"
            )
        if self.provider is not None and not _PROVIDER.match(self.provider):
            raise DomainValidationError(f"invalid provider: {self.provider!r}")

    def _check_values(self) -> None:
        not_observable = sorted(set(self.values) - OBSERVABLE_FIELDS)
        if not_observable:
            raise DomainValidationError(f"fields no source provides: {not_observable}")
        if self.kind is not SourceKind.ATHLETE_ENTRY and set(self.values) & SUBJECTIVE_FIELDS:
            raise DomainValidationError("only athlete entries carry subjective fields")
        values = check_values(self.values)
        for required in ("classification.sport", "timing.startTimeUtc"):
            if values.get(required) is None:
                raise DomainValidationError(f"an observation needs {required}")
        check_subtype(sport_of(values), subtype_of(values))
        object.__setattr__(self, "values", MappingProxyType(values))
        paths = {
            path: clean_text(location, max_length=300, field="source path")
            for path, location in self.source_paths.items()
        }
        if not set(paths) <= set(values):
            raise DomainValidationError("source paths only describe provided fields")
        object.__setattr__(self, "source_paths", MappingProxyType(paths))

    @property
    def key(self) -> SourceKey:
        return SourceKey(self.raw_source_id, self.part)

    @property
    def sport(self) -> Sport:
        return sport_of(self.values)

    @property
    def start_time_utc(self) -> datetime:
        return instant_of(self.values, "timing.startTimeUtc")

    @property
    def provider_record(self) -> tuple[str, str] | None:
        if self.provider is None or self.provider_activity_id is None:
            return None
        return self.provider, self.provider_activity_id


def version_key(version: str) -> tuple[int, ...]:
    """MAJOR.MINOR.PATCH as numbers, so 1.10.0 is newer than 1.9.0."""
    if not _SEMVER.match(version):
        raise DomainValidationError(f"not a MAJOR.MINOR.PATCH version: {version!r}")
    return tuple(int(part) for part in version.split("."))


def supersedes(new: Observation, old: Observation) -> bool:
    """A re-normalisation of the same source (activities spec 1.1): same source and part,
    same normaliser, a newer version."""
    return (
        new.key == old.key
        and new.normaliser == old.normaliser
        and version_key(new.normaliser_version) > version_key(old.normaliser_version)
    )


def same_session(new: Observation, old: Observation) -> bool:
    """Whether a re-normalised observation still describes the session it replaces."""
    return (new.sport, new.start_time_utc) == (old.sport, old.start_time_utc)
