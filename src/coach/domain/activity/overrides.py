"""Athlete corrections (FR-ACT-006; spec/functional/activities.spec.md section 4).

Overrides are append-only: each correctable field of an entity has one chain, and the last
override of the chain applies. They are applied at read time to build the EFFECTIVE values;
RAW and NORMALIZED values never change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.activity.activity import Activity, FieldProvenance
from coach.domain.activity.fields import field_spec, sport_of, subtype_fits, subtype_of
from coach.domain.activity.resolution import derive_pace
from coach.domain.common.errors import DomainError, DomainValidationError
from coach.domain.common.text import clean_text
from coach.domain.common.time import TrainingDayBasis, ensure_utc

OVERRIDE_RULE: Final = "OVERRIDE"
TRAINING_DAY_FIELD: Final = "timing.trainingDay"
BASIS_FIELD: Final = "timing.trainingDayBasis"
SUBTYPE_FIELD: Final = "classification.subtype"


class EntityType(StrEnum):
    ACTIVITY = "ACTIVITY"


class StaleOverrideError(DomainError):
    """The override does not supersede the field's current override (concurrent change)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DataOverride:
    override_id: UUID
    athlete_id: UUID
    entity_type: EntityType
    entity_id: UUID
    field: str
    original_value: object
    corrected_value: object
    reason: str
    corrected_at: datetime
    corrected_by: UUID
    supersedes_override_id: UUID | None = None

    def __post_init__(self) -> None:
        spec = field_spec(self.field)
        if not spec.correctable:
            raise DomainValidationError(f"{self.field} cannot be corrected")
        if self.original_value is not None:
            object.__setattr__(self, "original_value", spec.check(self.original_value))
        object.__setattr__(self, "corrected_value", spec.check(self.corrected_value))
        object.__setattr__(self, "reason", clean_text(self.reason, max_length=500, field="reason"))
        object.__setattr__(self, "corrected_at", ensure_utc(self.corrected_at))
        if self.supersedes_override_id == self.override_id:
            raise DomainValidationError("an override cannot supersede itself")


def current_overrides(overrides: Iterable[DataOverride]) -> dict[str, DataOverride]:
    """The override that applies to each field: the one no other override supersedes."""
    items = list(overrides)
    superseded = {item.supersedes_override_id for item in items}
    current: dict[str, DataOverride] = {}
    for item in items:
        if item.override_id in superseded:
            continue
        if item.field in current:
            raise DomainValidationError(f"two current overrides for {item.field}")
        current[item.field] = item
    return current


def check_new_override(existing: Sequence[DataOverride], new: DataOverride) -> None:
    """``new`` must extend the chain of its field; raise StaleOverrideError otherwise."""
    for item in existing:
        if (item.athlete_id, item.entity_type, item.entity_id) != (
            new.athlete_id,
            new.entity_type,
            new.entity_id,
        ):
            raise DomainValidationError("overrides of different entities were mixed")
        if item.override_id == new.override_id:
            raise StaleOverrideError("override id already used")
    current = current_overrides(existing).get(new.field)
    expected = None if current is None else current.override_id
    if new.supersedes_override_id != expected:
        raise StaleOverrideError(f"{new.field} was corrected concurrently")


@dataclass(frozen=True, slots=True)
class EffectiveValues:
    values: Mapping[str, object]
    provenance: Mapping[str, FieldProvenance]


def apply_overrides(activity: Activity, overrides: Iterable[DataOverride]) -> EffectiveValues:
    """NORMALIZED values with the current corrections applied (the EFFECTIVE values)."""
    values = dict(activity.values)
    provenance = dict(activity.provenance)
    primary = activity.primary_source.source_id
    for path, item in current_overrides(overrides).items():
        if (item.athlete_id, item.entity_type, item.entity_id) != (
            activity.athlete_id,
            EntityType.ACTIVITY,
            activity.activity_id,
        ):
            raise DomainValidationError("override belongs to another entity")
        base = provenance.get(path)
        origin = FieldProvenance(
            primary if base is None else base.source_id,
            OVERRIDE_RULE,
            None if base is None else base.path,
            item.override_id,
        )
        values[path] = item.corrected_value
        provenance[path] = origin
        if path == TRAINING_DAY_FIELD:
            values[BASIS_FIELD] = TrainingDayBasis.CONFIGURED
            provenance[BASIS_FIELD] = origin
    if not subtype_fits(sport_of(values), subtype_of(values)):
        values[SUBTYPE_FIELD] = None
        provenance.pop(SUBTYPE_FIELD, None)
    derive_pace(values, provenance)
    return EffectiveValues(values, provenance)
