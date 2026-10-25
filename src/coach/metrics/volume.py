"""Weekly volume per sport over logical activities (FR-ACT-008).

Only ACTIVE activities count; a linked source never contributes on its own, and each
activity contributes one resolved value per total (spec/integration/deduplication.spec.md
section 4). Unknown totals are counted, never invented. Sums use ``math.fsum``, which is
correctly rounded and therefore independent of the order of the activities.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from coach.domain.activity.activity import Activity
from coach.domain.common.sport import Sport
from coach.metrics.derived import MetricSubject


@dataclass(frozen=True, slots=True)
class WeeklyVolume:
    week: str
    sport: Sport
    activities: int = 0
    duration_seconds: float = 0.0
    distance_meters: float = 0.0
    unknown_duration: int = 0
    unknown_distance: int = 0


def weekly_volume(activities: Iterable[Activity]) -> dict[tuple[str, Sport], WeeklyVolume]:
    """Volume per ISO week of the training day and sport."""
    groups: dict[tuple[str, Sport], list[Activity]] = defaultdict(list)
    for activity in activities:
        if activity.counts_toward_totals:
            week = MetricSubject.week(activity.training_day).key
            groups[(week, activity.sport)].append(activity)
    totals = {}
    for (week, sport), members in sorted(groups.items()):
        durations = [activity.number("timing.elapsedSeconds") for activity in members]
        distances = [activity.number("distance.meters") for activity in members]
        totals[(week, sport)] = WeeklyVolume(
            week=week,
            sport=sport,
            activities=len(members),
            duration_seconds=math.fsum(value for value in durations if value is not None),
            distance_meters=math.fsum(value for value in distances if value is not None),
            unknown_duration=durations.count(None),
            unknown_distance=distances.count(None),
        )
    return totals
