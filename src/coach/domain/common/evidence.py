"""References from conclusions to the evidence they rest on (NFR-TRC-001)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from coach.domain.common.text import clean_optional_text


class EvidenceKind(StrEnum):
    RAW_SOURCE = "RAW_SOURCE"
    ACTIVITY = "ACTIVITY"
    DERIVED_METRIC = "DERIVED_METRIC"
    DAILY_RECOVERY = "DAILY_RECOVERY"
    DAILY_CHECKIN = "DAILY_CHECKIN"
    STRENGTH_SET_RESULT = "STRENGTH_SET_RESULT"
    WORKOUT_EXECUTION = "WORKOUT_EXECUTION"
    RECOVERY_ASSESSMENT = "RECOVERY_ASSESSMENT"
    INSIGHT = "INSIGHT"
    PLAN_VERSION = "PLAN_VERSION"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: EvidenceKind
    ref_id: UUID
    field: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "note", clean_optional_text(self.note, max_length=500, field="note")
        )
