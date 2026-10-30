"""Interference engine, rules 1.0.0 (strength spec section 7; FR-STR-007).

Sessions, planned or completed, are described by what they demand (``Demand``) and the body
regions they load; the baseline rules find the combinations within 48 hours and assess them
as ``InterferenceAssessment``s. A demand that holds only if unknown efforts were hard is
*possible*: an assessment resting on one keeps its level and carries MISSING_DATA, so a
planner still avoids it. Context modifiers raise or lower a level by one step each, only
with stated evidence, and are recorded.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import permutations
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceRef
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.strength.exercise import ExerciseCatalogue
from coach.domain.strength.prescription import EffortCue, StrengthPrescription
from coach.domain.strength.regions import regions_loaded
from coach.domain.strength.session import SetSource, StrengthSetResult
from coach.domain.strength.vocabulary import (
    LOWER_BODY_PATTERNS,
    UPPER_BODY_PATTERNS,
    BodyRegion,
    ContraindicationTag,
    InterferenceLevel,
    MuscleGroup,
)
from coach.metrics.strength import is_hard

INTERFERENCE_RULES_VERSION: Final = "1.0.0"
DAY: Final = timedelta(hours=24)
TWO_DAYS: Final = timedelta(hours=48)
HEAVY_HARD_SETS: Final = 3  # hard sets of one body half that make a session heavy
INTENSIVE_CALF_SETS: Final = 3
HARD_RPE: Final = 7.0  # a hard run or swim: session RPE 7 or more
LONG_RUN_SECONDS: Final = 90 * 60
MAX_CUE_RPE: Final = 9.0  # the HARD cue reads as hard
EASY_CUE_RPE: Final = 5.0  # EASY and MODERATE cues read as not hard
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)  # planned sets are not dated
_LEVELS: Final = tuple(InterferenceLevel)
_CALF_ACHILLES: Final = (BodyRegion.CALVES, BodyRegion.SOLEUS, BodyRegion.ACHILLES_REGION)
_CALF_MUSCLES: Final = frozenset({MuscleGroup.CALVES, MuscleGroup.SOLEUS})
TENNIS_REGIONS: Final = (
    BodyRegion.CALVES,
    BodyRegion.SOLEUS,
    BodyRegion.ACHILLES_REGION,
    BodyRegion.KNEES,
    BodyRegion.ADDUCTORS,
    BodyRegion.SHOULDERS,
    BodyRegion.ELBOWS,
)
LOWER_REGIONS: Final = frozenset(
    {
        BodyRegion.QUADRICEPS,
        BodyRegion.HAMSTRINGS,
        BodyRegion.GLUTES,
        BodyRegion.CALVES,
        BodyRegion.SOLEUS,
        BodyRegion.ADDUCTORS,
        BodyRegion.ABDUCTORS,
        BodyRegion.FOOT_ANKLE,
        BodyRegion.ACHILLES_REGION,
        BodyRegion.KNEES,
    }
)


class Demand(StrEnum):
    HEAVY_LOWER_BODY = "HEAVY_LOWER_BODY"
    HEAVY_UPPER_BODY = "HEAVY_UPPER_BODY"
    PLYOMETRIC = "PLYOMETRIC"
    CALF_ACHILLES_INTENSIVE = "CALF_ACHILLES_INTENSIVE"
    HARD_INTERVALS = "HARD_INTERVALS"
    LONG_RUN = "LONG_RUN"
    HARD_SWIM = "HARD_SWIM"
    TENNIS = "TENNIS"
    DEMANDING_TENNIS = "DEMANDING_TENNIS"


class Rule(StrEnum):
    HEAVY_LEGS_BEFORE_KEY_RUN = "HEAVY_LEGS_BEFORE_KEY_RUN"  # within 24 h: HIGH
    HEAVY_LEGS_BEFORE_INTERVALS = "HEAVY_LEGS_BEFORE_INTERVALS"  # 24 to 48 h: MODERATE
    HEAVY_UPPER_BEFORE_HARD_SWIM = "HEAVY_UPPER_BEFORE_HARD_SWIM"  # within 24 h: MODERATE
    TENNIS_INTERVALS_HEAVY_LEGS = "TENNIS_INTERVALS_HEAVY_LEGS"  # within 48 h: VERY_HIGH
    CALF_ACHILLES_LOAD = "CALF_ACHILLES_LOAD"  # within 24 h: HIGH


@dataclass(frozen=True, slots=True)
class SessionLoad:
    """A session as interference sees it; ``possible`` demands hold if unknown efforts were
    hard (or an unknown level was high)."""

    session_id: UUID
    start: datetime
    sport: Sport
    planned: bool
    demands: frozenset[Demand] = frozenset()
    possible: frozenset[Demand] = frozenset()
    regions: frozenset[BodyRegion] = frozenset()
    evidence: tuple[EvidenceRef, ...] = ()

    def has(self, demand: Demand) -> bool | None:
        """True when it demands it, None when it possibly does, False otherwise."""
        if demand in self.demands:
            return True
        return None if demand in self.possible else False


@dataclass(frozen=True, slots=True)
class Modifier:
    """Context that moves every level one step, only with stated evidence."""

    code: str
    step: int  # +1 raises, -1 lowers
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.step not in (-1, 1):
            raise DomainValidationError("a modifier moves a level one step")
        if not self.evidence:
            raise DomainValidationError("a modifier states its evidence")


@dataclass(frozen=True, slots=True)
class InterferenceAssessment:
    rule: Rule
    level: InterferenceLevel
    base_level: InterferenceLevel
    affected_regions: tuple[BodyRegion, ...]
    sessions: tuple[UUID, ...]  # in time order
    evidence: tuple[EvidenceRef, ...]
    reasons: frozenset[UncertaintyReason] = frozenset()
    modifiers: tuple[Modifier, ...] = ()
    rules_version: str = field(default=INTERFERENCE_RULES_VERSION)


# --------------------------------------------------------------------------- demands


def strength_demands(
    sets: Iterable[StrengthSetResult], catalogue: ExerciseCatalogue
) -> tuple[frozenset[Demand], frozenset[Demand], frozenset[BodyRegion]]:
    """Demands, possible demands and loaded regions of a strength session's executed sets.

    Heavy: at least 3 hard sets (strength spec 8) of lower-body (or upper-body) patterns;
    possible when hard and unknown-effort sets together reach 3. Plyometric: a HIGH_IMPACT
    exercise. Calf and Achilles intensive: at least 3 sets of an exercise whose primary
    muscles include the calves or soleus.
    """
    hard = {"lower": 0, "upper": 0}
    unknown = {"lower": 0, "upper": 0}
    plyometric, calf_sets = False, 0
    regions: set[BodyRegion] = set()
    for item in sets:
        if item.exercise_id is None or item.exercise_id not in catalogue:
            continue
        exercise = catalogue.get(item.exercise_id)
        regions |= regions_loaded(exercise)
        plyometric = plyometric or ContraindicationTag.HIGH_IMPACT in exercise.contraindication_tags
        if _CALF_MUSCLES & set(exercise.primary_muscles):
            calf_sets += 1
        half = (
            "lower"
            if exercise.movement_pattern in LOWER_BODY_PATTERNS
            else "upper"
            if exercise.movement_pattern in UPPER_BODY_PATTERNS
            else None
        )
        if half is None:
            continue
        effort = is_hard(item)
        if effort:
            hard[half] += 1
        elif effort is None:
            unknown[half] += 1
    demands, possible = set(), set()
    for half, demand in (("lower", Demand.HEAVY_LOWER_BODY), ("upper", Demand.HEAVY_UPPER_BODY)):
        if hard[half] >= HEAVY_HARD_SETS:
            demands.add(demand)
        elif hard[half] + unknown[half] >= HEAVY_HARD_SETS:
            possible.add(demand)
    if plyometric:
        demands.add(Demand.PLYOMETRIC)
    if calf_sets >= INTENSIVE_CALF_SETS:
        demands.add(Demand.CALF_ACHILLES_INTENSIVE)
    return frozenset(demands), frozenset(possible), frozenset(regions)


def prescription_demands(
    prescription: StrengthPrescription, catalogue: ExerciseCatalogue
) -> tuple[frozenset[Demand], frozenset[Demand], frozenset[BodyRegion]]:
    """A planned strength session: its sets read as executed at their targets (a target RIR
    of 4 or less, else a target RPE of 6 or more, or the HARD cue, is hard; EASY and MODERATE
    cues are not)."""
    sets: list[StrengthSetResult] = []
    for item in prescription.exercises:
        rpe = item.target_rpe
        if item.target_rpe is None and item.target_rir is None:
            rpe = MAX_CUE_RPE if item.effort_cue is EffortCue.HARD else EASY_CUE_RPE
        sets.extend(
            StrengthSetResult(
                exercise_id=item.exercise_id,
                set_number=number,
                recorded_at=EPOCH,
                source=SetSource.ATHLETE,
                actual_rpe=rpe,
                actual_rir=item.target_rir,
            )
            for number in range(1, item.sets + 1)
        )
    return strength_demands(sets, catalogue)


def _by_rpe(demand: Demand, rpe: float | None) -> tuple[set[Demand], set[Demand]]:
    if rpe is None:
        return set(), {demand}
    return ({demand} if rpe >= HARD_RPE else set()), set()


def run_demands(
    *, rpe: float | None, elapsed_seconds: float | None, structured_intervals: bool
) -> tuple[frozenset[Demand], frozenset[Demand]]:
    """Hard intervals: a structured interval session (running spec 4.3) at RPE 7 or more
    (possible with RPE unknown); long run: 90 minutes or more."""
    demands, possible = (
        _by_rpe(Demand.HARD_INTERVALS, rpe) if structured_intervals else (set(), set())
    )
    if elapsed_seconds is not None and elapsed_seconds >= LONG_RUN_SECONDS:
        demands.add(Demand.LONG_RUN)
    return frozenset(demands), frozenset(possible)


def swim_demands(*, rpe: float | None) -> tuple[frozenset[Demand], frozenset[Demand]]:
    demands, possible = _by_rpe(Demand.HARD_SWIM, rpe)
    return frozenset(demands), frozenset(possible)


def tennis_demands(*, high_load: bool | None) -> tuple[frozenset[Demand], frozenset[Demand]]:
    """Every tennis session; demanding when its regional load level is HIGH (tennis spec 4)."""
    demands = {Demand.TENNIS}
    if high_load:
        demands.add(Demand.DEMANDING_TENNIS)
    possible = {Demand.DEMANDING_TENNIS} if high_load is None else set()
    return frozenset(demands), frozenset(possible)


# --------------------------------------------------------------------------- rules


def _gap(first: SessionLoad, second: SessionLoad) -> timedelta:
    return second.start - first.start


def _assessment(
    rule: Rule,
    level: InterferenceLevel,
    sessions: Sequence[SessionLoad],
    regions: Iterable[BodyRegion],
    certain: bool,
) -> InterferenceAssessment:
    ordered = sorted(sessions, key=lambda item: (item.start, str(item.session_id)))
    return InterferenceAssessment(
        rule=rule,
        level=level,
        base_level=level,
        affected_regions=tuple(region for region in BodyRegion if region in set(regions)),
        sessions=tuple(item.session_id for item in ordered),
        evidence=tuple(ref for item in ordered for ref in item.evidence),
        reasons=frozenset() if certain else frozenset({UncertaintyReason.MISSING_DATA}),
    )


def _holds(*answers: bool | None) -> bool | None:
    """All demands hold: True; one only possibly: None; one does not: False."""
    if any(answer is False for answer in answers):
        return False
    return None if any(answer is None for answer in answers) else True


def _best(*answers: bool | None) -> bool | None:
    """Any of the alternatives holds: True before None before False."""
    if any(answer is True for answer in answers):
        return True
    return None if any(answer is None for answer in answers) else False


def _impact(session: SessionLoad) -> bool | None:
    return _best(session.has(Demand.TENNIS), session.has(Demand.PLYOMETRIC))


def _ordered_pairs(sessions: Sequence[SessionLoad]) -> list[InterferenceAssessment]:
    """Rules where the strength session comes first."""
    found = []
    for first, second in permutations(sessions, 2):
        gap = _gap(first, second)
        legs = first.has(Demand.HEAVY_LOWER_BODY)
        lower = first.regions & LOWER_REGIONS
        if timedelta(0) <= gap <= DAY:
            holds = _holds(
                legs, _best(second.has(Demand.HARD_INTERVALS), second.has(Demand.LONG_RUN))
            )
            if holds is not False:
                found.append(
                    _assessment(
                        Rule.HEAVY_LEGS_BEFORE_KEY_RUN,
                        InterferenceLevel.HIGH,
                        (first, second),
                        lower,
                        bool(holds),
                    )
                )
            holds = _holds(first.has(Demand.HEAVY_UPPER_BODY), second.has(Demand.HARD_SWIM))
            if holds is not False:
                found.append(
                    _assessment(
                        Rule.HEAVY_UPPER_BEFORE_HARD_SWIM,
                        InterferenceLevel.MODERATE,
                        (first, second),
                        first.regions - LOWER_REGIONS,
                        bool(holds),
                    )
                )
        elif DAY < gap <= TWO_DAYS:
            holds = _holds(legs, second.has(Demand.HARD_INTERVALS))
            if holds is not False:
                found.append(
                    _assessment(
                        Rule.HEAVY_LEGS_BEFORE_INTERVALS,
                        InterferenceLevel.MODERATE,
                        (first, second),
                        lower,
                        bool(holds),
                    )
                )
    return found


def _calf_pairs(sessions: Sequence[SessionLoad]) -> list[InterferenceAssessment]:
    """Tennis or plyometrics and calf and Achilles intensive work within 24 h, either order."""
    found = []
    for index, first in enumerate(sessions):
        for second in sessions[index + 1 :]:
            if abs(_gap(first, second)) > DAY:
                continue
            holds = _best(
                _holds(_impact(first), second.has(Demand.CALF_ACHILLES_INTENSIVE)),
                _holds(_impact(second), first.has(Demand.CALF_ACHILLES_INTENSIVE)),
            )
            if holds is not False:
                found.append(
                    _assessment(
                        Rule.CALF_ACHILLES_LOAD,
                        InterferenceLevel.HIGH,
                        (first, second),
                        _CALF_ACHILLES,
                        bool(holds),
                    )
                )
    return found


def _triples(sessions: Sequence[SessionLoad]) -> list[InterferenceAssessment]:
    found = []
    tennis = [item for item in sessions if item.has(Demand.DEMANDING_TENNIS) is not False]
    runs = [item for item in sessions if item.has(Demand.HARD_INTERVALS) is not False]
    legs = [item for item in sessions if item.has(Demand.HEAVY_LOWER_BODY) is not False]
    for court in tennis:
        for run in runs:
            for gym in legs:
                group = (court, run, gym)
                if len({item.session_id for item in group}) < 3:
                    continue
                starts = [item.start for item in group]
                if max(starts) - min(starts) > TWO_DAYS:
                    continue
                holds = _holds(
                    court.has(Demand.DEMANDING_TENNIS),
                    run.has(Demand.HARD_INTERVALS),
                    gym.has(Demand.HEAVY_LOWER_BODY),
                )
                regions = set(TENNIS_REGIONS) | (gym.regions & LOWER_REGIONS)
                found.append(
                    _assessment(
                        Rule.TENNIS_INTERVALS_HEAVY_LEGS,
                        InterferenceLevel.VERY_HIGH,
                        group,
                        regions,
                        bool(holds),
                    )
                )
    return found


def _moved(level: InterferenceLevel, steps: int) -> InterferenceLevel:
    index = _LEVELS.index(level) + steps
    return _LEVELS[max(_LEVELS.index(InterferenceLevel.LOW), min(len(_LEVELS) - 1, index))]


def assess(
    sessions: Sequence[SessionLoad], modifiers: Sequence[Modifier] = ()
) -> list[InterferenceAssessment]:
    """Every baseline combination among the sessions (rules 1.0.0), most severe first."""
    found = _ordered_pairs(sessions) + _calf_pairs(sessions) + _triples(sessions)
    unique: dict[tuple[Rule, tuple[UUID, ...]], InterferenceAssessment] = {}
    for item in found:
        key = (item.rule, item.sessions)
        if key not in unique or (item.reasons < unique[key].reasons):
            unique[key] = item
    steps = sum(modifier.step for modifier in modifiers)
    adjusted = [
        InterferenceAssessment(
            rule=item.rule,
            level=_moved(item.level, steps) if modifiers else item.level,
            base_level=item.base_level,
            affected_regions=item.affected_regions,
            sessions=item.sessions,
            evidence=(
                *item.evidence,
                *(ref for modifier in modifiers for ref in modifier.evidence),
            ),
            reasons=item.reasons,
            modifiers=tuple(modifiers),
        )
        for item in unique.values()
    ]
    return sorted(
        adjusted,
        key=lambda item: (
            -_LEVELS.index(item.level),
            item.rule.value,
            [str(i) for i in item.sessions],
        ),
    )


def worst(assessments: Iterable[InterferenceAssessment]) -> InterferenceLevel:
    """The highest level among the assessments; NONE without any."""
    levels = [item.level for item in assessments]
    return max(levels, key=_LEVELS.index) if levels else InterferenceLevel.NONE
