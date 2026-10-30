"""Body regions an exercise loads, and pain locations named as regions (rules 1.0.0;
strength spec sections 2, 6 and 7).

An exercise loads the regions of its muscle groups plus the joint and tendon regions its
movement pattern and contraindication tags load (a fixed, versioned table). A pain location
is the athlete's free text: it names a region only when, normalised like an alias, it is
exactly one of the region names below; any other text names no region. Never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from coach.domain.strength.exercise import Exercise, normalise_alias
from coach.domain.strength.vocabulary import (
    BodyRegion,
    ContraindicationTag,
    MovementPattern,
)

REGION_RULES_VERSION: Final = "1.0.0"

_PATTERN_REGIONS: Final[Mapping[MovementPattern, frozenset[BodyRegion]]] = {
    MovementPattern.SQUAT: frozenset({BodyRegion.KNEES}),
    MovementPattern.LUNGE: frozenset({BodyRegion.KNEES}),
    MovementPattern.CALF_RAISE: frozenset({BodyRegion.ACHILLES_REGION}),
    MovementPattern.HORIZONTAL_PUSH: frozenset({BodyRegion.SHOULDERS, BodyRegion.ELBOWS}),
    MovementPattern.VERTICAL_PUSH: frozenset({BodyRegion.SHOULDERS, BodyRegion.ELBOWS}),
    MovementPattern.HORIZONTAL_PULL: frozenset({BodyRegion.SHOULDERS, BodyRegion.ELBOWS}),
    MovementPattern.VERTICAL_PULL: frozenset({BodyRegion.SHOULDERS, BodyRegion.ELBOWS}),
    MovementPattern.SCAPULAR_STABILITY: frozenset({BodyRegion.SHOULDERS}),
}
_TAG_REGIONS: Final[Mapping[ContraindicationTag, frozenset[BodyRegion]]] = {
    ContraindicationTag.HIGH_IMPACT: frozenset({BodyRegion.KNEES, BodyRegion.ACHILLES_REGION}),
    ContraindicationTag.OVERHEAD: frozenset({BodyRegion.SHOULDERS}),
}

# English and Spanish names of each region, as an athlete writes a pain location.
REGION_NAMES: Final[Mapping[BodyRegion, tuple[str, ...]]] = {
    BodyRegion.QUADRICEPS: ("quadriceps", "quads", "quad", "cuadriceps"),
    BodyRegion.HAMSTRINGS: ("hamstrings", "hamstring", "isquiotibiales", "isquios"),
    BodyRegion.GLUTES: ("glutes", "glute", "gluteos", "gluteo"),
    BodyRegion.CALVES: ("calves", "calf", "gemelos", "gemelo", "pantorrillas", "pantorrilla"),
    BodyRegion.SOLEUS: ("soleus", "soleo"),
    BodyRegion.ADDUCTORS: ("adductors", "adductor", "aductores", "aductor", "ingle", "groin"),
    BodyRegion.ABDUCTORS: ("abductors", "abductor", "abductores"),
    BodyRegion.FOOT_ANKLE: (
        "foot",
        "ankle",
        "feet",
        "ankles",
        "pie",
        "pies",
        "tobillo",
        "tobillos",
    ),
    BodyRegion.CORE: ("core", "abdomen", "abdominales", "abs"),
    BodyRegion.ERECTOR_SPINAE: ("lower back", "low back", "lumbar", "lumbares", "espalda baja"),
    BodyRegion.LATS: ("lats", "dorsales", "dorsal"),
    BodyRegion.RHOMBOIDS: ("rhomboids", "romboides"),
    BodyRegion.TRAPEZIUS: ("traps", "trapezius", "trapecio", "trapecios"),
    BodyRegion.PECTORALS: ("chest", "pecs", "pectorals", "pecho", "pectorales"),
    BodyRegion.DELTOIDS: ("deltoids", "delts", "deltoides"),
    BodyRegion.ROTATOR_CUFF: ("rotator cuff", "manguito rotador"),
    BodyRegion.BICEPS: ("biceps",),
    BodyRegion.TRICEPS: ("triceps",),
    BodyRegion.FOREARMS: ("forearms", "forearm", "antebrazos", "antebrazo"),
    BodyRegion.ACHILLES_REGION: ("achilles", "achilles tendon", "aquiles", "tendon de aquiles"),
    BodyRegion.KNEES: ("knee", "knees", "rodilla", "rodillas"),
    BodyRegion.SHOULDERS: ("shoulder", "shoulders", "hombro", "hombros"),
    BodyRegion.ELBOWS: ("elbow", "elbows", "codo", "codos"),
}
_BY_NAME: Final[Mapping[str, BodyRegion]] = {
    normalise_alias(name): region
    for region, names in REGION_NAMES.items()
    for name in (*names, region.value)
}


def regions_loaded(exercise: Exercise) -> frozenset[BodyRegion]:
    """The regions an exercise loads: its muscles' and its pattern's and tags' joints."""
    regions = {BodyRegion.of(muscle) for muscle in exercise.muscles}
    regions |= _PATTERN_REGIONS.get(exercise.movement_pattern, frozenset())
    for tag in exercise.contraindication_tags:
        regions |= _TAG_REGIONS.get(tag, frozenset())
    return frozenset(regions)


def region_named(location: str | None) -> BodyRegion | None:
    """The region a pain location names exactly; ``None`` for anything else."""
    if location is None:
        return None
    return _BY_NAME.get(normalise_alias(location))
