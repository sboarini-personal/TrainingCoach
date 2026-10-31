"""Adaptation decisions (planning spec 7; FR-ADP-001 to FR-ADP-004, NFR-EXP-001).

A decision of the adaptive loop, recorded whatever it is: its trigger, the decision, the
reason codes, the evidence it rests on, a plain interpretation whose numbers come from that
evidence, its uncertainty and confidence, the planned workouts it changes and the version it
proposed (none for MAINTAIN). Reason codes and texts are the system's, never athlete text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.text import clean_text
from coach.domain.common.time import ensure_utc

ADAPTATION_RULES_VERSION: Final = "1.0.0"


class Decision(StrEnum):
    MAINTAIN = "MAINTAIN"
    PROGRESS = "PROGRESS"
    REDUCE = "REDUCE"
    RESCHEDULE = "RESCHEDULE"
    REPLACE = "REPLACE"
    REST = "REST"


class AdaptationTrigger(StrEnum):
    COMPLETED_WORKOUT = "COMPLETED_WORKOUT"
    RECOVERY_ASSESSMENT = "RECOVERY_ASSESSMENT"
    WEEKLY_REVIEW = "WEEKLY_REVIEW"
    ATHLETE_REQUEST = "ATHLETE_REQUEST"


class AdaptationReason(StrEnum):
    NO_SIGNAL = "NO_SIGNAL"
    ISOLATED_DEVIATION = "ISOLATED_DEVIATION"
    DEVIATIONS_WITHOUT_RECOVERY_SIGNAL = "DEVIATIONS_WITHOUT_RECOVERY_SIGNAL"
    DEVIATIONS_WITH_RECOVERY_SIGNAL = "DEVIATIONS_WITH_RECOVERY_SIGNAL"
    SAFETY_HOLD_ACTIVE = "SAFETY_HOLD_ACTIVE"
    RECOVERY_PRESCRIPTION = "RECOVERY_PRESCRIPTION"
    PAIN_REPORTED = "PAIN_REPORTED"
    UNPLANNED_LOAD_INTERFERENCE = "UNPLANNED_LOAD_INTERFERENCE"
    INTERFERENCE_ASSESSED = "INTERFERENCE_ASSESSED"
    CONSISTENT_COMPLETION = "CONSISTENT_COMPLETION"
    NOTHING_TO_CHANGE = "NOTHING_TO_CHANGE"


@dataclass(frozen=True, slots=True, kw_only=True)
class AdaptationDecision:
    decision_id: UUID
    athlete_id: UUID
    decided_at: datetime
    training_day: date
    trigger: AdaptationTrigger
    decision: Decision
    reasons: tuple[AdaptationReason, ...]
    evidence: tuple[EvidenceRef, ...]
    interpretation: str
    uncertainty_reasons: frozenset[UncertaintyReason]
    confidence: Confidence
    affected_workouts: tuple[UUID, ...] = ()
    plan_id: UUID | None = None
    from_version: int | None = None
    proposed_version: int | None = None
    rules_version: str = ADAPTATION_RULES_VERSION
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "decided_at", ensure_utc(self.decided_at))
        object.__setattr__(
            self,
            "interpretation",
            clean_text(self.interpretation, max_length=2000, field="interpretation"),
        )
        if not self.reasons:
            raise DomainValidationError("a decision states its reason")
        if self.decision is not Decision.MAINTAIN:
            if not self.evidence:
                raise DomainValidationError("a change of plan states its evidence (FR-ADP-004)")
            if not self.affected_workouts and self.decision is not Decision.PROGRESS:
                raise DomainValidationError("a change of plan names the workouts it changes")
        elif self.proposed_version is not None:
            raise DomainValidationError("MAINTAIN proposes no version (FR-ADP-003)")

    @property
    def entity_id(self) -> UUID:
        return self.decision_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)
