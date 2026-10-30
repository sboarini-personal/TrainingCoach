"""Progression rules 1.0.0 (strength spec section 6; FR-STR-006).

A decision about one exercise reads its execution history: the last two exposures (the
sessions in which it was done), their sets against their targets, and pain reported in the
period they cover. It never reads planned workouts alone and never assumes a value it does
not have: without targets or with unknown values the decision is INSUFFICIENT_DATA.

- PROGRESS: both exposures met their target repetitions at or below their target RPE.
- HOLD: targets met with RPE above target, or not two exposures in a row met.
- REGRESS: repetitions missed or RPE above target by more than 1.5 in both exposures, or pain
  reported for a region the exercise loads. Pain whose location names no region blocks a
  progression (HOLD) but cannot regress an exercise it may not concern.

RPE is the RIR-based scale: an actual RIR without an RPE reads as RPE 10 - RIR.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.activity.fields import Pain
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.strength.exercise import Exercise
from coach.domain.strength.regions import region_named, regions_loaded
from coach.domain.strength.session import StrengthSetResult

PROGRESSION_RULES_VERSION: Final = "1.0.0"
RPE_OVERSHOOT: Final = 1.5
MAX_RPE: Final = 10.0
EXPOSURES: Final = 2


class Decision(StrEnum):
    PROGRESS = "PROGRESS"
    HOLD = "HOLD"
    REGRESS = "REGRESS"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Outcome(StrEnum):
    """One exposure against its targets."""

    MET = "MET"  # target repetitions met at or below the target RPE
    MET_ABOVE_RPE = "MET_ABOVE_RPE"  # met, RPE above target by at most 1.5
    REPS_MET = "REPS_MET"  # repetitions met; no RPE target to judge the effort by
    MISSED = "MISSED"  # repetitions missed, or RPE above target by more than 1.5
    NO_TARGETS = "NO_TARGETS"
    UNKNOWN = "UNKNOWN"  # a value the targets need is unknown


class Reason(StrEnum):
    TWO_EXPOSURES_MET = "TWO_EXPOSURES_MET"
    TARGETS_MET_ABOVE_RPE = "TARGETS_MET_ABOVE_RPE"
    NOT_TWO_EXPOSURES_MET = "NOT_TWO_EXPOSURES_MET"
    ONE_EXPOSURE_MISSED = "ONE_EXPOSURE_MISSED"
    TWO_EXPOSURES_MISSED = "TWO_EXPOSURES_MISSED"
    PAIN_IN_REGION = "PAIN_IN_REGION"
    PAIN_REGION_UNKNOWN = "PAIN_REGION_UNKNOWN"
    FEWER_THAN_TWO_EXPOSURES = "FEWER_THAN_TWO_EXPOSURES"
    NO_TARGETS = "NO_TARGETS"
    NO_TARGET_RPE = "NO_TARGET_RPE"
    MISSING_VALUES = "MISSING_VALUES"


@dataclass(frozen=True, slots=True)
class Exposure:
    """One session in which the exercise was done, with its sets of that exercise."""

    activity_id: UUID
    training_day: date
    sets: tuple[StrengthSetResult, ...]


@dataclass(frozen=True, slots=True)
class PainReport:
    activity_id: UUID
    training_day: date
    pain: Pain


@dataclass(frozen=True, slots=True)
class ProgressionDecision:
    exercise_id: str
    decision: Decision
    reasons: tuple[Reason, ...]
    exposures: tuple[tuple[Exposure, Outcome], ...]  # the last two at most, oldest first
    evidence: tuple[EvidenceRef, ...]
    rules_version: str = PROGRESSION_RULES_VERSION


def actual_rpe(item: StrengthSetResult) -> float | None:
    if item.actual_rpe is not None:
        return item.actual_rpe
    return None if item.actual_rir is None else MAX_RPE - item.actual_rir


def outcome(sets: Iterable[StrengthSetResult]) -> Outcome:
    targeted = [
        item for item in sets if item.target_reps is not None or item.target_rpe is not None
    ]
    if not targeted:
        return Outcome.NO_TARGETS
    missed = any(
        item.target_reps is not None
        and item.completed_reps is not None
        and item.completed_reps < item.target_reps
        for item in targeted
    )
    overshoots = [
        rpe - item.target_rpe
        for item in targeted
        if item.target_rpe is not None and (rpe := actual_rpe(item)) is not None
    ]
    if missed or any(overshoot > RPE_OVERSHOOT for overshoot in overshoots):
        return Outcome.MISSED  # a known failure, whatever else is unknown
    unknown = any(
        (item.target_reps is not None and item.completed_reps is None)
        or (item.target_rpe is not None and actual_rpe(item) is None)
        for item in targeted
    )
    if unknown:
        return Outcome.UNKNOWN
    if not any(item.target_rpe is not None for item in targeted):
        return Outcome.REPS_MET
    return Outcome.MET_ABOVE_RPE if any(gap > 0 for gap in overshoots) else Outcome.MET


def _pain_evidence(report: PainReport) -> EvidenceRef:
    return EvidenceRef(EvidenceKind.ACTIVITY, report.activity_id, field="subjective.pain")


def decide(
    exercise: Exercise, exposures: Sequence[Exposure], pains: Sequence[PainReport]
) -> ProgressionDecision:
    """The decision for one exercise; ``exposures`` in time order, ``pains`` reported in the
    period from the first of the last two exposures on (the caller selects it)."""
    recent = tuple(sorted(exposures, key=lambda item: item.training_day)[-EXPOSURES:])
    judged = tuple((item, outcome(item.sets)) for item in recent)
    evidence = [
        EvidenceRef(EvidenceKind.STRENGTH_SET_RESULT, item.activity_id, note=exercise.exercise_id)
        for item in recent
    ]
    loaded = regions_loaded(exercise)
    flagged = [report for report in pains if report.pain.flag]
    in_region = [report for report in flagged if region_named(report.pain.location) in loaded]
    unnamed = [report for report in flagged if region_named(report.pain.location) is None]

    def result(
        decision: Decision, *reasons: Reason, pain: Sequence[PainReport] = ()
    ) -> ProgressionDecision:
        refs = (*evidence, *(_pain_evidence(report) for report in pain))
        return ProgressionDecision(exercise.exercise_id, decision, reasons, judged, refs)

    if in_region:
        return result(Decision.REGRESS, Reason.PAIN_IN_REGION, pain=in_region)
    if len(judged) < EXPOSURES:
        return result(Decision.INSUFFICIENT_DATA, Reason.FEWER_THAN_TWO_EXPOSURES)
    outcomes = [found for _, found in judged]
    if all(found is Outcome.MISSED for found in outcomes):
        return result(Decision.REGRESS, Reason.TWO_EXPOSURES_MISSED)
    if Outcome.NO_TARGETS in outcomes:
        return result(Decision.INSUFFICIENT_DATA, Reason.NO_TARGETS)
    if Outcome.UNKNOWN in outcomes:
        return result(Decision.INSUFFICIENT_DATA, Reason.MISSING_VALUES)
    if Outcome.MISSED in outcomes:
        return result(Decision.HOLD, Reason.ONE_EXPOSURE_MISSED)
    if Outcome.REPS_MET in outcomes:
        return result(Decision.INSUFFICIENT_DATA, Reason.NO_TARGET_RPE)
    if all(found is Outcome.MET for found in outcomes):
        if unnamed:
            return result(Decision.HOLD, Reason.PAIN_REGION_UNKNOWN, pain=unnamed)
        return result(Decision.PROGRESS, Reason.TWO_EXPOSURES_MET)
    if outcomes[-1] is Outcome.MET_ABOVE_RPE:
        return result(Decision.HOLD, Reason.TARGETS_MET_ABOVE_RPE)
    return result(Decision.HOLD, Reason.NOT_TWO_EXPOSURES_MET)
