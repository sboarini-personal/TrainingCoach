"""Plan generator rules 1.0.0 (planning spec 5.1; FR-PLN-001, FR-PLN-004 to FR-PLN-006,
FR-PLN-008, FR-STR-001, FR-STR-004, FR-STR-007, FR-ONB-004, FR-ONB-005).

A pure, deterministic function from what the application read (``GenerationInputs``) to plan
content. It plans an endurance goal (running, cycling or swimming): phases to the target
date, a weekly volume that starts where the athlete is and grows by the athlete's own recent
steps, strength every week unless contraindicated or impossible, the athlete's own tennis as
load, and intensity by RPE only (heart rate, pace and power need reliable anchors, which
these rules do not read). It plans within availability, resources, equipment, health
constraints, a safety hold and a recovery block, so that the policy engine allows what it
produces; the policy engine still decides. Heavy lower-body strength that the interference
rules (P10) assess as HIGH or VERY_HIGH is replaced by an upper-body and core session
(AT-STR-001). What the plan could not do, or assumed, is stated in its assumptions. The same
inputs give the same content, workout identifiers included.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any, Final
from uuid import UUID, uuid5

from coach.domain.athlete.experience import StrengthExperience
from coach.domain.athlete.health import HealthConstraint, HealthDeclaration, RestrictionKind
from coach.domain.athlete.resources import Equipment, Resources, TennisSessionType, TennisSlot
from coach.domain.availability.availability import Availability, AvailabilityBasis
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday
from coach.domain.onboarding.readiness import ReadinessInput
from coach.domain.plans.plan import (
    EstimatedLoad,
    Phase,
    PhaseBlock,
    PlanContent,
    SportLoad,
    TrainingWeek,
)
from coach.domain.plans.workout import (
    SAFETY_STOP,
    EnduranceWorkout,
    EvaluationSession,
    IntensityTarget,
    PlannedWorkout,
    Repeat,
    RpeRange,
    Step,
    StepKind,
    TennisSession,
    WorkoutType,
)
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.equipment import equipment_available
from coach.domain.strength.exercise import Exercise, ExerciseCatalogue
from coach.domain.strength.prescription import (
    EffortCue,
    ExpectedInterference,
    Load,
    LoadKind,
    PrescribedExercise,
    StrengthPrescription,
)
from coach.domain.strength.regions import region_named, regions_loaded
from coach.domain.strength.vocabulary import (
    LOWER_BODY_PATTERNS,
    BodyRegion,
    ContraindicationTag,
    InterferenceLevel,
)
from coach.metrics.tennis import LoadLevel, load_level
from coach.planning.interference import (
    Demand,
    SessionLoad,
    assess,
    prescription_demands,
    run_demands,
    swim_demands,
    tennis_demands,
    worst,
)

GENERATOR_RULES_VERSION: Final = "1.0.0"
ENDURANCE_SPORTS: Final = frozenset({Sport.RUNNING, Sport.CYCLING, Sport.SWIMMING})
MAX_WEEKS: Final = 26
BASELINE_WEEKS: Final = 2
DEFAULT_START_MINUTES: Final = 90
DEFAULT_STEP_MINUTES: Final = 10
MAX_STEP_MINUTES: Final = 30
HISTORY_WEEKS: Final = 8
START_WEEKS: Final = 4  # the starting volume is the mean of the last 4 weeks
UNLOAD_EVERY: Final = 4
PEAK_STEPS: Final = 3  # the peak: the highest recent week plus three of the athlete's steps
MIN_SESSION_MINUTES: Final = 20
KEY_MINUTES: Final = (45, 75)
STRENGTH_MINUTES: Final = 45
TAPER_STRENGTH_MINUTES: Final = 30
EVALUATION_MINUTES: Final = 30
# Two-week taper: 65% then 45% of the last loading week (a 45% mean reduction); one week:
# 55%. Bosquet L, Montpetit J, Arvisais D, Mujika I. Effects of tapering on performance: a
# meta-analysis. Med Sci Sports Exerc. 2007;39(8):1358-1365 (PMID 17762369): a two-week
# taper with volume reduced by 41-60%, intensity and frequency kept.
TAPER_TWO: Final = (0.65, 0.45)
TAPER_ONE: Final = (0.55,)
TENNIS_RPE: Final = {
    TennisSessionType.MATCH: 7.0,
    TennisSessionType.PRACTICE: 5.0,
    TennisSessionType.UNKNOWN: 7.0,  # may be a match: read as hard
}
_IDS: Final = UUID("0193b2a0-7a51-7c3e-9d10-5e6f7a8b9c0d")
_NOON: Final = time(12, 0)
_INTENSIVE: Final = 7.0
_IMPACT_SPORTS: Final = frozenset({Sport.RUNNING, Sport.TENNIS})
_OVERHEAD_SPORTS: Final = frozenset({Sport.SWIMMING, Sport.TENNIS})
_EXCLUDED_TAGS: Final = frozenset({ContraindicationTag.HIGH_IMPACT, ContraindicationTag.OVERHEAD})
# Exercises whose resistance is the body: bodyweight, a bar to hang from, a bench to lean on.
_BODY_RESISTANCE: Final = frozenset(
    {Equipment.BODYWEIGHT_ONLY, Equipment.PULL_UP_BAR, Equipment.BENCH}
)


class Role(StrEnum):
    LONG = "LONG"
    KEY = "KEY"
    EASY = "EASY"
    EVALUATION = "EVALUATION"
    STRENGTH = "STRENGTH"


class Template(StrEnum):
    LOWER = "LOWER"  # lower body plus pull and core
    UPPER = "UPPER"  # upper body and core: no heavy legs


# Exercise slots in order of preference; the first the athlete can and may do is used.
TEMPLATES: Final[dict[Template, tuple[tuple[str, ...], ...]]] = {
    Template.LOWER: (
        ("ex.goblet_squat", "ex.leg_press", "ex.back_squat", "ex.bodyweight_squat"),
        (
            "ex.romanian_deadlift",
            "ex.single_leg_romanian_deadlift",
            "ex.hip_thrust",
            "ex.glute_bridge",
        ),
        ("ex.split_squat", "ex.reverse_lunge", "ex.step_up", "ex.lateral_lunge"),
        ("ex.dumbbell_row", "ex.seated_cable_row", "ex.inverted_row"),
        ("ex.dead_bug", "ex.plank", "ex.bird_dog"),
    ),
    Template.UPPER: (
        ("ex.dumbbell_bench_press", "ex.bench_press", "ex.push_up"),
        ("ex.dumbbell_row", "ex.seated_cable_row", "ex.inverted_row"),
        ("ex.lat_pulldown", "ex.pull_up", "ex.band_pull_apart"),
        ("ex.pallof_press", "ex.half_kneeling_cable_chop", "ex.side_plank"),
        ("ex.dead_bug", "ex.plank", "ex.bird_dog"),
    ),
}
_STRENGTH_RPE: Final = {
    Phase.ADAPTATION: 6.0,
    Phase.BASE: 7.0,
    Phase.BUILD: 7.0,
    Phase.SPECIFIC: 7.0,
    Phase.TAPER: 6.0,
}
_FOCUS: Final = {
    Phase.ADAPTATION: "Adaptation: easy, regular training",
    Phase.BASE: "Base: aerobic volume and strength",
    Phase.BUILD: "Build: intervals on an aerobic base",
    Phase.SPECIFIC: "Specific: sustained work toward the goal",
    Phase.TAPER: "Taper: less volume, intensity kept",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationInputs:
    """What the generator reads, assembled by the application from stored state."""

    today: date  # the athlete's local date
    goal_id: UUID
    sport: Sport
    target_date: date
    availability: Availability
    resources: Resources | None
    health: HealthDeclaration
    strength: StrengthExperience | None = None
    sport_years: float | None = None
    weekly_minutes: tuple[float, ...] = ()  # the goal sport, last complete weeks, oldest first
    baseline: bool = False  # BASELINE_COLLECTION: a baseline block, not a goal plan
    missing_preferred: frozenset[ReadinessInput] = frozenset()
    safety_hold: bool = False
    recovery_block: bool = False
    recovery_block_until: date | None = None  # with the block on, unknown: every day blocked
    catalogue: ExerciseCatalogue = CATALOGUE


@dataclass(slots=True)
class _Week:
    index: int
    start: date
    phase: Phase
    minutes: int
    rationale: str
    workouts: list[PlannedWorkout] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Day:
    day: date
    minutes: int
    sports: frozenset[Sport] | None

    def allows(self, sport: Sport) -> bool:
        return self.sports is None or sport in self.sports


_Slot = Role | tuple[Role, int]  # a session of the week: a role, or a numbered one


# --------------------------------------------------------------------------- helpers


def _round5(minutes: float) -> int:
    return int(5 * math.floor(minutes / 5 + 0.5))


def _monday(day: date) -> date:
    return day - timedelta(days=day.isoweekday() - 1)


def plan_start(today: date) -> date:
    """The plan starts on the next Monday (today when it is one)."""
    return today if today.isoweekday() == 1 else _monday(today) + timedelta(days=7)


def _active(health: HealthDeclaration, day: date, sport: Sport) -> list[HealthConstraint]:
    return [
        item
        for item in health.active_constraints(on=day)
        if not item.affected_sports or sport in item.affected_sports
    ]


def _restricted(health: HealthDeclaration, day: date, sport: Sport) -> bool:
    """Whether an active constraint excludes the sport that day (policy spec 5)."""
    for constraint in health.active_constraints(on=day):
        in_scope = not constraint.affected_sports or sport in constraint.affected_sports
        for restriction in constraint.restrictions:
            if restriction.kind is RestrictionKind.NO_SPORT and restriction.sport is sport:
                return True
            if not in_scope:
                continue
            if restriction.kind is RestrictionKind.NO_IMPACT and sport in _IMPACT_SPORTS:
                return True
            if restriction.kind is RestrictionKind.NO_OVERHEAD and sport in _OVERHEAD_SPORTS:
                return True
    return False


def _rpe_cap(health: HealthDeclaration, day: date, sport: Sport) -> float:
    caps = [
        float(restriction.max_rpe)
        for constraint in _active(health, day, sport)
        for restriction in constraint.restrictions
        if restriction.kind is RestrictionKind.MAX_RPE and restriction.max_rpe is not None
    ]
    return min(caps, default=10.0)


def _blocked(inputs: GenerationInputs, day: date) -> bool:
    """No intensity that day: a safety hold, or a recovery block not yet re-evaluated."""
    if inputs.safety_hold:
        return True
    if not inputs.recovery_block:
        return False
    return inputs.recovery_block_until is None or day < inputs.recovery_block_until


def _target(low: float, high: float, cap: float) -> IntensityTarget:
    top = min(high, cap)
    return IntensityTarget(rpe=RpeRange(min(low, top), top))


# --------------------------------------------------------------------------- phases


def phases(weeks: int, *, adaptation: bool) -> list[Phase]:
    """The phase of each week of a goal plan of ``weeks`` weeks."""
    taper = 2 if weeks >= 8 else 1 if weeks >= 4 else 0
    rest = weeks - taper
    start = 2 if adaptation and rest >= 6 else 0
    middle = rest - start
    if middle <= 2:
        body = [Phase.BUILD, Phase.SPECIFIC][2 - middle :] if middle else []
    else:
        base = math.ceil(middle * 0.5)
        build = round(middle * 0.3)
        specific = middle - base - build
        if specific < 1:
            build, specific = build - 1, 1
        body = [Phase.BASE] * base + [Phase.BUILD] * build + [Phase.SPECIFIC] * specific
    return [Phase.ADAPTATION] * start + body + [Phase.TAPER] * taper


def _blocks(start: date, kinds: Sequence[Phase]) -> tuple[PhaseBlock, ...]:
    found: list[PhaseBlock] = []
    index = 0
    while index < len(kinds):
        end = index
        while end + 1 < len(kinds) and kinds[end + 1] is kinds[index]:
            end += 1
        found.append(
            PhaseBlock(
                kinds[index],
                start + timedelta(weeks=index),
                start + timedelta(weeks=end, days=6),
                _FOCUS[kinds[index]],
            )
        )
        index = end + 1
    return tuple(found)


# --------------------------------------------------------------------------- volume


@dataclass(frozen=True, slots=True)
class Progression:
    start: int
    step: int
    start_reason: str
    step_reason: str
    peak: int  # the highest recent week plus PEAK_STEPS steps


def progression(inputs: GenerationInputs, *, provisional: bool) -> Progression:
    """Where the volume starts and how it grows (planning spec 5): from the athlete's own
    history, never a universal percentage."""
    history = list(inputs.weekly_minutes[-HISTORY_WEEKS:])
    recent = history[-START_WEEKS:]
    sport = inputs.sport.value.lower()
    if any(minutes > 0 for minutes in recent):
        start = max(MIN_SESSION_MINUTES * 2, _round5(sum(recent) / len(recent)))
        start_reason = f"your recent average of {start} min a week"
    else:
        start = DEFAULT_START_MINUTES
        start_reason = f"a conservative start (no recent {sport} history)"
    increases = [later - earlier for earlier, later in pairwise(history) if later > earlier]
    if increases:
        step = min(MAX_STEP_MINUTES, max(5, _round5(max(increases))))
        step_reason = "your largest recent weekly increase"
    else:
        step = DEFAULT_STEP_MINUTES
        step_reason = "a small step (no recent increase to build on)"
    cautious: list[str] = []
    if provisional:
        cautious.append("provisional plan")
    if inputs.sport_years is None or inputs.sport_years < 1:
        cautious.append("under a year of experience or unknown")
    if _active(inputs.health, inputs.today, inputs.sport):
        cautious.append("an active health constraint")
    if cautious:
        step = max(5, 5 * (step // 10))
        step_reason += ", halved: " + ", ".join(cautious)
    highest = max([start, *(_round5(minutes) for minutes in history)])
    return Progression(start, step, start_reason, step_reason, highest + PEAK_STEPS * step)


def volumes(kinds: Sequence[Phase], plan: Progression, *, held_weeks: int) -> list[tuple[int, str]]:
    """Each week's goal-sport minutes and the rationale of its change."""
    found: list[tuple[int, str]] = []
    current = last_loading = plan.start
    taper = [index for index, kind in enumerate(kinds) if kind is Phase.TAPER]
    factors = TAPER_TWO if len(taper) == 2 else TAPER_ONE
    for index, kind in enumerate(kinds):
        if kind is Phase.TAPER:
            factor = factors[taper.index(index)]
            minutes = max(MIN_SESSION_MINUTES * 2, _round5(last_loading * factor))
            found.append(
                (minutes, f"taper, {round(factor * 100)}% of the last loading week (Bosquet 2007)")
            )
        elif index == 0:
            found.append((current, "starting from " + plan.start_reason))
        elif (index + 1) % UNLOAD_EVERY == 0:
            found.append((plan.start, "unloading week, back to the starting volume"))
        elif index < held_weeks:
            found.append((current, "held while intensity is paused"))
        elif current >= plan.peak:
            last_loading = current
            found.append(
                (current, "held at the peak: your highest recent week plus three of your steps")
            )
        else:
            step = min(plan.step, plan.peak - current)
            current += step
            last_loading = current
            found.append((current, f"+{step} min, {plan.step_reason}"))
    return found


# --------------------------------------------------------------------------- days


def _days(inputs: GenerationInputs, start: date, excluded: set[date]) -> list[_Day]:
    found = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        if day < inputs.today or day in excluded:
            continue
        effective = inputs.availability.resolve(day)
        if (
            effective.plannable
            and effective.available
            and effective.maximum_duration_minutes >= MIN_SESSION_MINUTES
        ):
            found.append(_Day(day, effective.maximum_duration_minutes, effective.allowed_sports))
    return found


def _own_tennis(
    inputs: GenerationInputs, start: date, excluded: set[date], notes: set[str]
) -> list[tuple[date, TennisSlot]]:
    """The athlete's declared tennis this week, unless they are away that date, a health
    constraint restricts it, or it would be hard while intensity is paused."""
    slots = () if inputs.resources is None else inputs.resources.tennis_schedule
    found = []
    for slot in slots:
        day = start + timedelta(days=slot.weekday - 1)
        if day < inputs.today or day in excluded:
            continue
        name = Weekday(slot.weekday).name.capitalize()
        rpe = TENNIS_RPE[slot.session_type]
        effective = inputs.availability.resolve(day)
        if effective.basis is AvailabilityBasis.EXCEPTION and not effective.available:
            continue
        if _restricted(inputs.health, day, Sport.TENNIS) or rpe > _rpe_cap(
            inputs.health, day, Sport.TENNIS
        ):
            notes.add(f"Tennis on {name} is left out: an active health constraint restricts it.")
            continue
        if rpe >= _INTENSIVE and _blocked(inputs, day):
            notes.add(
                f"Tennis on {name} is left out while intensity is paused (safety hold or "
                "recovery block); if you play, record it: it counts as load."
            )
            continue
        found.append((day, slot))
    return found


# --------------------------------------------------------------------------- strength


def _usable(inputs: GenerationInputs, exercise: Exercise) -> bool:
    owned = frozenset() if inputs.resources is None else inputs.resources.strength_equipment
    if set(exercise.contraindication_tags) & _EXCLUDED_TAGS:
        return False
    if not equipment_available(exercise, owned):
        return False
    experience = inputs.strength
    familiar = experience is not None and bool(experience.rpe_familiar or experience.rir_familiar)
    return familiar or _body_resistance(exercise) or _declared(experience, exercise) is not None


def _body_resistance(exercise: Exercise) -> bool:
    return set(exercise.equipment) <= _BODY_RESISTANCE


def _declared(experience: StrengthExperience | None, exercise: Exercise) -> float | None:
    if experience is None:
        return None
    return next(
        (
            item.load_kg
            for item in experience.approximate_loads
            if item.exercise_id == exercise.exercise_id
        ),
        None,
    )


def _regions(constraints: Sequence[HealthConstraint]) -> frozenset[BodyRegion]:
    found: set[BodyRegion] = set()
    for constraint in constraints:
        for name in constraint.affected_regions:
            region = BodyRegion(name) if name in BodyRegion.__members__ else region_named(name)
            if region is not None:
                found.add(region)
    return frozenset(found)


def _prescribed(
    exercise: Exercise, sets: int, rpe: float, experience: StrengthExperience | None
) -> PrescribedExercise:
    """RPE for an athlete familiar with it, RIR for one familiar with RIR only, otherwise a
    plain-language cue with the body's resistance or a declared load (strength spec 4)."""
    fields: dict[str, Any] = {"sets": sets, "reps_min": 8, "reps_max": 12, "rest_seconds": 90}
    if experience is not None and experience.rpe_familiar:
        fields |= {"load": Load(LoadKind.RPE_BASED), "target_rpe": rpe}
    elif experience is not None and experience.rir_familiar:
        fields |= {"load": Load(LoadKind.RPE_BASED), "target_rir": int(10 - rpe)}
    else:
        declared = _declared(experience, exercise)
        load = (
            Load(LoadKind.BODYWEIGHT) if declared is None else Load(LoadKind.ABSOLUTE_KG, declared)
        )
        fields |= {"load": load, "effort_cue": EffortCue.EASY if rpe < 5 else EffortCue.MODERATE}
    return PrescribedExercise.of(exercise, **fields)


def _exercises(
    inputs: GenerationInputs, day: date, phase: Phase, template: Template
) -> tuple[PrescribedExercise, ...]:
    constraints = _active(inputs.health, day, Sport.STRENGTH)
    no_heavy_legs = any(
        restriction.kind is RestrictionKind.NO_HEAVY_LOWER_BODY
        for constraint in constraints
        for restriction in constraint.restrictions
    )
    protected = _regions(constraints)
    cap = _rpe_cap(inputs.health, day, Sport.STRENGTH)
    rpe = _STRENGTH_RPE.get(phase, 6.0)
    if _blocked(inputs, day):
        rpe = min(rpe, 6.0)
    sets = 2 if phase is Phase.TAPER else 3
    found: list[PrescribedExercise] = []
    for slot in TEMPLATES[template]:
        usable = [
            inputs.catalogue.get(item)
            for item in slot
            if item in inputs.catalogue and _usable(inputs, inputs.catalogue.get(item))
        ]
        if not usable:
            continue
        exercise = usable[0]
        light = (no_heavy_legs and exercise.movement_pattern in LOWER_BODY_PATTERNS) or bool(
            regions_loaded(exercise) & protected
        )
        found.append(
            _prescribed(exercise, sets, min(rpe, cap, 5.0 if light else 10.0), inputs.strength)
        )
    return tuple(found)


# --------------------------------------------------------------------------- workouts


class _Factory:
    """Builds workouts with identifiers derived from the goal, the day and the role."""

    def __init__(self, inputs: GenerationInputs) -> None:
        self._inputs = inputs
        self._seed = f"{inputs.goal_id}/{inputs.today.isoformat()}"

    def ids(self, day: date, role: str) -> UUID:
        return uuid5(_IDS, f"{self._seed}/{day.isoformat()}/{role}")

    def endurance(self, day: date, role: Role, phase: Phase, minutes: int) -> PlannedWorkout:
        inputs = self._inputs
        cap = _rpe_cap(inputs.health, day, inputs.sport)
        hard = not _blocked(inputs, day) and cap >= _INTENSIVE
        warmup: tuple[Step, ...] = ()
        cooldown: tuple[Step, ...] = ()
        main: tuple[Step | Repeat, ...]
        if role is Role.KEY and hard and minutes >= 45 and phase in (Phase.BUILD, Phase.TAPER):
            reps = 3 if phase is Phase.TAPER else max(3, min(6, (minutes - 25) // 6))
            warmup = (Step(kind=StepKind.WARMUP, duration_seconds=900, target=_target(2, 3, cap)),)
            main = (
                Repeat(
                    reps,
                    (
                        Step(kind=StepKind.WORK, duration_seconds=240, target=_target(7, 8, cap)),
                        Step(
                            kind=StepKind.RECOVERY, duration_seconds=120, target=_target(2, 3, cap)
                        ),
                    ),
                ),
            )
            cooldown = (
                Step(kind=StepKind.COOLDOWN, duration_seconds=600, target=_target(1, 2, cap)),
            )
            what = f"{reps} x 4 minutes hard (RPE 7 to 8), 2 minutes easy between"
            objective, why = "Intervals", "Raise the effort you can hold, on an aerobic base"
        elif role is Role.KEY and hard and minutes >= 45 and phase is Phase.SPECIFIC:
            reps = max(2, min(4, (minutes - 25) // 10))
            warmup = (Step(kind=StepKind.WARMUP, duration_seconds=900, target=_target(2, 3, cap)),)
            main = (
                Repeat(
                    reps,
                    (
                        Step(kind=StepKind.WORK, duration_seconds=480, target=_target(6, 7, cap)),
                        Step(
                            kind=StepKind.RECOVERY, duration_seconds=120, target=_target(2, 3, cap)
                        ),
                    ),
                ),
            )
            cooldown = (
                Step(kind=StepKind.COOLDOWN, duration_seconds=600, target=_target(1, 2, cap)),
            )
            what = f"{reps} x 8 minutes sustained (RPE 6 to 7), 2 minutes easy between"
            objective, why = "Sustained work", "Practise the effort of the goal"
        elif role is Role.KEY and minutes >= 30:
            work = minutes - 20
            warmup = (Step(kind=StepKind.WARMUP, duration_seconds=600, target=_target(2, 3, cap)),)
            main = (
                Step(kind=StepKind.STEADY, duration_seconds=work * 60.0, target=_target(5, 6, cap)),
            )
            cooldown = (
                Step(kind=StepKind.COOLDOWN, duration_seconds=600, target=_target(2, 3, cap)),
            )
            what = f"{work} minutes steady (RPE 5 to 6) between an easy warm-up and cool-down"
            objective, why = "Steady session", "Aerobic strength without hard intensity"
        else:
            main = (
                Step(
                    kind=StepKind.STEADY,
                    duration_seconds=minutes * 60.0,
                    target=_target(3, 4, cap),
                ),
            )
            long = role is Role.LONG
            what = f"{minutes} minutes easy (RPE 3 to 4)"
            objective = "Long easy session" if long else "Easy session"
            why = "Endurance at an easy effort" if long else "Aerobic volume at a low cost"
        return PlannedWorkout(
            workout_id=self.ids(day, role.value),
            type=WorkoutType.ENDURANCE,
            training_day=day,
            order=0,
            is_key=role in (Role.KEY, Role.LONG),
            objective=f"{objective} ({inputs.sport.value.lower()})",
            what=what,
            why=why,
            endurance=EnduranceWorkout(
                sport=inputs.sport,
                warmup=warmup,
                main_set=main,
                cooldown=cooldown,
                stop_conditions=(SAFETY_STOP,),
            ),
        )

    def evaluation(self, day: date) -> PlannedWorkout:
        sport = self._inputs.sport
        return PlannedWorkout(
            workout_id=self.ids(day, Role.EVALUATION.value),
            type=WorkoutType.EVALUATION,
            training_day=day,
            order=0,
            is_key=True,
            objective=f"Submaximal assessment ({sport.value.lower()})",
            what="10 minutes easy, then 20 minutes steady at RPE 4 to 5, recording heart rate",
            why="Learn your current level without a maximal effort (baseline collection)",
            evaluation=EvaluationSession(
                sport=sport,
                protocol_id=f"eval.submaximal_{sport.value.lower()}",
                description=(
                    "Warm up 10 minutes easy, then hold 20 minutes at a steady effort you could "
                    "keep talking through (RPE 4 to 5). Record the session with heart rate."
                ),
                submaximal=True,
                requires_opt_in=False,
                safety_notes=(SAFETY_STOP,),
                duration_seconds=EVALUATION_MINUTES * 60,
            ),
        )

    def tennis(self, day: date, slot: TennisSlot) -> PlannedWorkout:
        return PlannedWorkout(
            workout_id=self.ids(day, "TENNIS"),
            type=WorkoutType.TENNIS,
            training_day=day,
            order=0,
            is_key=False,
            objective="Your tennis",
            what=f"{slot.duration_minutes} minutes ({slot.session_type.value.lower()})",
            why="Your own tennis: it counts as load in the plan",
            tennis=TennisSession(
                slot.session_type, slot.duration_minutes * 60, TENNIS_RPE[slot.session_type]
            ),
        )

    def strength(self, day: date, phase: Phase, template: Template) -> PlannedWorkout | None:
        exercises = _exercises(self._inputs, day, phase, template)
        if not exercises:
            return None
        minutes = TAPER_STRENGTH_MINUTES if phase is Phase.TAPER else STRENGTH_MINUTES
        return PlannedWorkout(
            workout_id=self.ids(day, Role.STRENGTH.value),
            type=WorkoutType.STRENGTH,
            training_day=day,
            order=0,
            is_key=False,
            objective="Strength: lower body, pull and core"
            if template is Template.LOWER
            else "Strength: upper body and core",
            what=f"{len(exercises)} exercises, {exercises[0].sets} sets of 8 to 12",
            why="Strength is planned every week (FR-STR-001): it supports the goal and its load",
            strength=StrengthPrescription(
                purpose=_FOCUS[phase],
                estimated_duration_seconds=minutes * 60,
                warmup=("5 minutes easy cardio", "Mobility for the session's joints"),
                exercises=exercises,
                progression_rule=(
                    "Add repetitions within the range before adding load; progress when the last "
                    "two sessions met the target at or below the target effort"
                ),
                regression_rule=(
                    "Reduce load or sets when repetitions are missed, effort is more than 1.5 "
                    "above the target, or a loaded region hurts"
                ),
                expected_interference=ExpectedInterference(level=InterferenceLevel.LOW),
            ),
        )


# --------------------------------------------------------------------------- placement


def _distance(day: _Day, others: Sequence[date]) -> int:
    return min((abs((day.day - other).days) for other in others), default=7)


def _place(
    days: list[_Day], roles: Sequence[Role], sport: Sport, tennis_days: set[date], strength: int
) -> dict[_Slot, _Day]:
    """Days for the week's sessions: the long one on the longest day, the key one far from
    it, strength not the day before either, easy sessions spread out."""
    used: dict[_Slot, _Day] = {}
    free = list(days)

    def take(slot: _Slot, day: _Day) -> None:
        used[slot] = day
        free.remove(day)

    endurance = [day for day in free if day.allows(sport)]
    if Role.LONG in roles and endurance:
        take(Role.LONG, max(endurance, key=lambda day: (day.minutes, day.day)))
    key = next((role for role in (Role.KEY, Role.EVALUATION) if role in roles), None)
    if key is not None:
        least = EVALUATION_MINUTES if key is Role.EVALUATION else MIN_SESSION_MINUTES
        candidates = [day for day in free if day.allows(sport) and day.minutes >= least]
        anchors = [used[Role.LONG].day] if Role.LONG in used else []
        if candidates:
            take(
                key,
                max(
                    candidates,
                    key=lambda day: (
                        min(_distance(day, anchors), 3),
                        day.day - timedelta(days=1) not in tennis_days,
                        -day.day.toordinal(),
                    ),
                ),
            )
    hard = [used[role].day for role in (Role.LONG, Role.KEY) if role in used]
    for number in range(roles.count(Role.STRENGTH)):
        candidates = [day for day in free if day.allows(Sport.STRENGTH) and day.minutes >= strength]
        if not candidates:
            break
        placed = [
            day.day
            for slot, day in used.items()
            if isinstance(slot, tuple) and slot[0] is Role.STRENGTH
        ]
        take(
            (Role.STRENGTH, number),
            max(
                candidates,
                key=lambda day: (
                    all(day.day + timedelta(days=1) != other for other in hard),
                    any(day.day - timedelta(days=1) == other for other in hard),
                    _distance(day, placed) >= 2,
                    -day.day.toordinal(),
                ),
            ),
        )
    for number in range(roles.count(Role.EASY)):
        candidates = [day for day in free if day.allows(sport)]
        if not candidates:
            break
        taken = [
            day.day
            for slot, day in used.items()
            if slot in (Role.LONG, Role.KEY, Role.EVALUATION)
            or (isinstance(slot, tuple) and slot[0] is Role.EASY)
        ]
        take(
            (Role.EASY, number),
            max(candidates, key=lambda day: (min(_distance(day, taken), 3), -day.day.toordinal())),
        )
    return used


def _roles(phase: Phase, sessions: int, strength: int, *, baseline: bool, race: bool) -> list[Role]:
    """Sessions in order of priority: the long one (an easy one in the goal's week),
    strength, the key one (an assessment in a baseline block), easy ones, a second strength
    session."""
    roles = [Role.EASY if race else Role.LONG]
    if strength:
        roles.append(Role.STRENGTH)
    key = Role.EVALUATION if baseline else None if phase is Phase.ADAPTATION else Role.KEY
    remaining = sessions - 1
    if key is not None and remaining >= 1:
        roles.append(key)
        remaining -= 1
    roles.extend([Role.EASY] * remaining)
    if strength >= 2:
        roles.append(Role.STRENGTH)
    return roles


def _split(minutes: int, roles: Sequence[Role]) -> dict[Role, int]:
    """Minutes of the long, the key and each easy session from the week's volume."""
    easy = roles.count(Role.EASY)
    key = 0
    if Role.KEY in roles:
        key = max(KEY_MINUTES[0], min(KEY_MINUTES[1], _round5(minutes * 0.25)))
    elif Role.EVALUATION in roles:
        key = EVALUATION_MINUTES
    rest = max(0, minutes - key)
    if Role.LONG not in roles:  # the goal's week: easy sessions alike
        long, each = 0, _round5(rest / easy) if easy else 0
    else:
        long = _round5(rest * 1.5 / (1.5 + easy)) if easy else _round5(rest)
        each = _round5((rest - long) / easy) if easy else 0
    return {
        Role.LONG: max(MIN_SESSION_MINUTES, long),
        Role.KEY: key,
        Role.EVALUATION: key,
        Role.EASY: max(MIN_SESSION_MINUTES, each),
    }


def _fit(wanted: dict[_Slot, int], room: int) -> dict[_Slot, int]:
    """Endurance minutes within the weekly room: shorter sessions, then fewer easy ones, the
    key one, and last a shorter long one."""
    total = sum(wanted.values())
    if total <= room:
        return wanted
    fitted = {
        slot: max(MIN_SESSION_MINUTES, 5 * math.floor(minutes * room / total / 5))
        for slot, minutes in wanted.items()
    }
    while fitted and sum(fitted.values()) > room:
        easy = [slot for slot in fitted if isinstance(slot, tuple)]
        if easy:
            del fitted[max(easy, key=lambda slot: slot[1] if isinstance(slot, tuple) else 0)]
        elif Role.KEY in fitted:
            del fitted[Role.KEY]
        elif Role.EVALUATION in fitted:
            del fitted[Role.EVALUATION]
        elif room >= MIN_SESSION_MINUTES:
            fitted[Role.LONG] = 5 * (room // 5)
        else:
            fitted.clear()
    return fitted


# --------------------------------------------------------------------------- weeks


def _strength_reason(inputs: GenerationInputs, day: date) -> str | None:
    """Why strength is contraindicated that day, if it is (strength spec 1)."""
    if inputs.safety_hold:
        return "the safety hold is active"
    if _restricted(inputs.health, day, Sport.STRENGTH):
        return "an active health constraint restricts strength"
    if _rpe_cap(inputs.health, day, Sport.STRENGTH) < 3:
        return "an active health constraint limits effort below an easy strength session"
    return None


def _build_week(
    inputs: GenerationInputs, week: _Week, factory: _Factory, notes: set[str], excluded: set[date]
) -> None:
    tennis = _own_tennis(inputs, week.start, excluded, notes)
    tennis_days = {day for day, _ in tennis}
    days = [day for day in _days(inputs, week.start, excluded) if day.day not in tennis_days]
    recurring = inputs.availability.recurring
    slots = min(len(days), recurring.days_per_week if recurring is not None else 7)
    reason = _strength_reason(inputs, week.start)
    declared = 0.0 if inputs.strength is None else inputs.strength.sessions_per_week or 0.0
    strength = 0 if reason else 2 if declared >= 2 and week.phase is not Phase.TAPER else 1
    if reason:
        notes.add(f"Strength is not planned while {reason}.")
    strength_minutes = TAPER_STRENGTH_MINUTES if week.phase is Phase.TAPER else STRENGTH_MINUTES
    budget = None if recurring is None else int(recurring.maximum_hours_per_week * 60)
    if budget is not None:
        strength = min(strength, budget // strength_minutes)
    sessions = max(2, min(5, round(week.minutes / 45)))
    race = not inputs.baseline and week.start <= inputs.target_date <= week.start + timedelta(
        days=6
    )
    roles = _roles(week.phase, sessions, strength, baseline=inputs.baseline, race=race)[:slots]
    placed = _place(days, roles, inputs.sport, tennis_days, strength_minutes)
    strength_slots = [
        slot for slot in placed if isinstance(slot, tuple) and slot[0] is Role.STRENGTH
    ]
    if strength and not strength_slots:
        notes.add("Strength could not be planned in some weeks: no suitable day was left.")
    split = _split(week.minutes, roles)
    wanted = {
        slot: min(day.minutes, split[slot if isinstance(slot, Role) else slot[0]])
        for slot, day in placed.items()
        if slot not in strength_slots
    }
    if budget is not None:
        wanted = _fit(wanted, budget - len(strength_slots) * strength_minutes)
    for slot, minutes in wanted.items():
        role = slot if isinstance(slot, Role) else slot[0]
        day = placed[slot].day
        if role is Role.EVALUATION:
            week.workouts.append(factory.evaluation(day))
        else:
            week.workouts.append(factory.endurance(day, role, week.phase, minutes))
    for slot in strength_slots:
        template = Template.LOWER if slot[1] == 0 else Template.UPPER
        built = factory.strength(placed[slot].day, week.phase, template)
        if built is None:
            notes.add("Strength is not planned: no exercise fits your equipment and restrictions.")
        else:
            week.workouts.append(built)
    for day, declared_slot in tennis:
        week.workouts.append(factory.tennis(day, declared_slot))


# --------------------------------------------------------------------------- interference


def session_load(workout: PlannedWorkout, catalogue: ExerciseCatalogue) -> SessionLoad | None:
    """A planned workout as the interference rules see it (strength spec 7), starting at noon
    of its day; ``None`` for one they do not read (cycling, recovery, rest, evaluation)."""
    start = datetime.combine(workout.training_day, _NOON, tzinfo=UTC)
    if workout.strength is not None:
        demands, possible, regions = prescription_demands(workout.strength, catalogue)
        return SessionLoad(
            workout.workout_id, start, Sport.STRENGTH, True, demands, possible, regions
        )
    if workout.tennis is not None:
        level = load_level(
            workout.tennis.duration_seconds / 60,
            workout.tennis.expected_rpe,
            match=workout.tennis.session_type is TennisSessionType.MATCH,
        )
        demands, possible = tennis_demands(high_load=level is LoadLevel.HIGH)
        return SessionLoad(workout.workout_id, start, Sport.TENNIS, True, demands, possible)
    if workout.endurance is None:
        return None
    if workout.endurance.sport is Sport.RUNNING:
        demands, possible = run_demands(
            rpe=workout.max_rpe,
            elapsed_seconds=workout.planned_seconds,
            structured_intervals=any(
                isinstance(item, Repeat) for item in workout.endurance.main_set
            ),
        )
    elif workout.endurance.sport is Sport.SWIMMING:
        demands, possible = swim_demands(rpe=workout.max_rpe)
    else:
        return None
    return SessionLoad(workout.workout_id, start, workout.endurance.sport, True, demands, possible)


def _heavy_legs(load: SessionLoad) -> bool:
    return load.has(Demand.HEAVY_LOWER_BODY) is not False


def _resolve_interference(inputs: GenerationInputs, weeks: list[_Week], factory: _Factory) -> None:
    """Replace heavy lower-body sessions in HIGH or VERY_HIGH combinations by upper-body and
    core sessions (AT-STR-001), then state each strength session's expected interference."""
    catalogue = inputs.catalogue
    for _ in range(len(weeks) * 2 + 1):
        loads = {
            item.workout_id: load
            for week in weeks
            for item in week.workouts
            if (load := session_load(item, catalogue)) is not None
        }
        severe = [
            item
            for item in assess(list(loads.values()))
            if item.level in (InterferenceLevel.HIGH, InterferenceLevel.VERY_HIGH)
        ]
        heavy = {
            session
            for item in severe
            for session in item.sessions
            if loads[session].sport is Sport.STRENGTH and _heavy_legs(loads[session])
        }
        if not heavy:
            break
        for week in weeks:
            week.workouts = [
                (factory.strength(item.training_day, week.phase, Template.UPPER) or item)
                if item.workout_id in heavy
                else item
                for item in week.workouts
            ]
    loads = {
        item.workout_id: load
        for week in weeks
        for item in week.workouts
        if (load := session_load(item, catalogue)) is not None
    }
    assessments = assess(list(loads.values()))
    for week in weeks:
        updated = []
        for item in week.workouts:
            if item.strength is None:
                updated.append(item)
                continue
            mine = [entry for entry in assessments if item.workout_id in entry.sessions]
            level = worst(mine) if mine else InterferenceLevel.LOW
            regions = tuple(
                dict.fromkeys(region for entry in mine for region in entry.affected_regions)
            )
            interference = ExpectedInterference(level=level, affected_regions=regions)
            updated.append(
                replace(item, strength=replace(item.strength, expected_interference=interference))
            )
        week.workouts = updated


# --------------------------------------------------------------------------- plan


def _assumptions(inputs: GenerationInputs, notes: set[str], *, truncated: bool) -> tuple[str, ...]:
    sport = inputs.sport.value.lower()
    found: list[str] = []
    if inputs.baseline:
        found.append(
            f"Baseline collection: few recent {sport} activities or no reliable intensity "
            "anchor. Two weeks of easy training with submaximal assessments; no heart-rate or "
            "pace threshold is set until your data provide one."
        )
    if ReadinessInput.ACTIVITY_HISTORY in inputs.missing_preferred:
        found.append(
            "No activity in the last 8 weeks: the plan starts at a conservative volume and "
            "grows in small steps until your history shows how you respond."
        )
    if ReadinessInput.RECOVERY_HISTORY in inputs.missing_preferred:
        found.append(
            "No check-in or recovery data in the last 14 days: the plan cannot yet adjust to "
            "your recovery; daily check-ins let it do so."
        )
    if ReadinessInput.GARMIN_INTEGRATION in inputs.missing_preferred:
        found.append(
            "Garmin is not connected: completed workouts are known only from the files you import."
        )
    if truncated:
        found.append(
            f"Your goal is more than {MAX_WEEKS} weeks away: this version covers the first "
            f"{MAX_WEEKS} weeks and a later version continues to the goal."
        )
    if any(
        restriction.kind is RestrictionKind.OTHER
        for constraint in inputs.health.active_constraints(on=inputs.today)
        for restriction in constraint.restrictions
    ):
        found.append(
            "A health constraint has a free-text restriction the plan cannot check: review "
            "each workout against it."
        )
    found.append(
        "Intensity is set by RPE (0 to 10) only: heart-rate, pace and power targets need "
        "reliable data and are not set by these rules."
    )
    found.extend(sorted(notes))
    return tuple(found)


def _rest(factory: _Factory, day: date) -> PlannedWorkout:
    return PlannedWorkout(
        workout_id=factory.ids(day, "REST"),
        type=WorkoutType.REST,
        training_day=day,
        order=0,
        is_key=False,
        objective="Rest before the goal",
        what="Full rest",
        why="Arrive fresh at the goal",
    )


def _training_week(
    inputs: GenerationInputs, week: _Week, factory: _Factory, rest_day: date | None
) -> TrainingWeek:
    days: list[list[PlannedWorkout]] = [[] for _ in range(7)]
    for item in week.workouts:
        days[(item.training_day - week.start).days].append(item)
    if rest_day is not None and week.start <= rest_day <= week.start + timedelta(days=6):
        offset = (rest_day - week.start).days
        if rest_day >= inputs.today and not days[offset]:
            days[offset].append(_rest(factory, rest_day))
    ordered = tuple(
        tuple(replace(item, order=order) for order, item in enumerate(workouts))
        for workouts in days
    )
    seconds = sum(
        item.planned_seconds or 0.0 for item in week.workouts if item.sport is inputs.sport
    )
    sport = inputs.sport.value.capitalize()
    return TrainingWeek(
        week_index=week.index,
        start=week.start,
        phase=week.phase,
        primary_goal=f"{_FOCUS[week.phase]}. {sport} {week.minutes} min: {week.rationale}.",
        estimated_load=EstimatedLoad(None, {inputs.sport: SportLoad(seconds or None, None)}),
        days=ordered,
    )


def generate(inputs: GenerationInputs) -> PlanContent:
    """The content of a new plan version (generator rules 1.0.0)."""
    if inputs.sport not in ENDURANCE_SPORTS:
        raise DomainValidationError("rules 1.0.0 plan running, cycling or swimming goals")
    start = plan_start(inputs.today)
    if inputs.target_date < start and not inputs.baseline:
        raise DomainValidationError("the goal date is before the plan could start")
    if _restricted(inputs.health, start, inputs.sport):
        raise DomainValidationError(
            "an active health constraint excludes the goal sport: review the goal first"
        )
    resources = inputs.resources
    if inputs.sport is Sport.SWIMMING and not (
        resources is not None and resources.pool is not None and resources.pool.available
    ):
        raise DomainValidationError("a swimming plan needs declared pool access")
    if inputs.sport is Sport.CYCLING and not (
        resources is not None and (resources.bicycle is True or resources.smart_trainer is True)
    ):
        raise DomainValidationError("a cycling plan needs a bicycle or a smart trainer")
    provisional = bool(inputs.missing_preferred) or inputs.baseline
    plan = progression(inputs, provisional=provisional)
    truncated = False
    excluded: set[date] = set()
    rest_day: date | None = None
    if inputs.baseline:
        kinds = [Phase.ADAPTATION] * BASELINE_WEEKS
        weekly = [(plan.start, "baseline, " + plan.start_reason)] * BASELINE_WEEKS
    else:
        count = (_monday(inputs.target_date) - start).days // 7 + 1
        truncated = count > MAX_WEEKS
        count = min(count, MAX_WEEKS)
        insufficient = ReadinessInput.ACTIVITY_HISTORY in inputs.missing_preferred or (
            inputs.sport_years is None or inputs.sport_years < 1
        )
        kinds = phases(count, adaptation=insufficient)
        held = 2 if inputs.safety_hold or inputs.recovery_block else 0
        weekly = volumes(kinds, plan, held_weeks=held)
        rest_day = inputs.target_date - timedelta(days=1)
        excluded = {inputs.target_date + timedelta(days=offset) for offset in range(-1, 7)}
    factory = _Factory(inputs)
    notes: set[str] = set()
    weeks = [
        _Week(index + 1, start + timedelta(weeks=index), kind, minutes, rationale)
        for index, (kind, (minutes, rationale)) in enumerate(zip(kinds, weekly, strict=True))
    ]
    for week in weeks:
        _build_week(inputs, week, factory, notes, excluded)
    _resolve_interference(inputs, weeks, factory)
    if any(not week.workouts for week in weeks):
        notes.add("Some weeks have no session: no available day was left in them.")
    return PlanContent(
        start=start,
        end=weeks[-1].start + timedelta(days=6),
        phases=_blocks(start, kinds),
        weeks=tuple(_training_week(inputs, week, factory, rest_day) for week in weeks),
        provisional=provisional,
        assumptions=_assumptions(inputs, notes, truncated=truncated),
    )
