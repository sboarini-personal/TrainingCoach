"""What equipment an exercise needs (equipment rules 1.0.0; strength spec section 2).

An exercise lists its equipment as one tuple. BENCH is needed in addition to the rest; the
other items are alternatives, any one of which is enough (a goblet squat takes dumbbells or
kettlebells, a bench press a barbell and a bench). BODYWEIGHT_ONLY needs nothing. The
athlete's equipment is what they declared they can use; gym access is never expanded into
equipment.
"""

from __future__ import annotations

from collections.abc import Set
from typing import Final

from coach.domain.athlete.resources import Equipment
from coach.domain.strength.exercise import Exercise

EQUIPMENT_RULES_VERSION: Final = "1.0.0"
_REQUIRED_ALONGSIDE: Final = frozenset({Equipment.BENCH})


def equipment_available(exercise: Exercise, owned: Set[Equipment]) -> bool:
    """Whether the athlete's equipment is enough for the exercise."""
    needed = set(exercise.equipment) - {Equipment.BODYWEIGHT_ONLY}
    alternatives = needed - _REQUIRED_ALONGSIDE
    return needed & _REQUIRED_ALONGSIDE <= owned and (
        not alternatives or not alternatives.isdisjoint(owned)
    )
