"""Pain below the red-flag threshold restricts load on its region (pain rules 1.0.0; safety
spec 5).

A check-in of the last 3 days reporting pain of 4 or more of 10 whose location names a body
region exactly (region rules 1.0.0; a location naming none restricts no region, it is never
interpreted) restricts that region until 3 days after the report: the policy engine denies
hard strength work loading it (POL-HEALTH-CONSTRAINT), the plan generator plans it light and
the adaptive loop eases the next days. Load management, not a diagnosis (SAFE-MED-001); red
flags follow the safety hold.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.strength.regions import region_named
from coach.domain.strength.vocabulary import BodyRegion

PAIN_RULES_VERSION: Final = "1.0.0"
PAIN_RESTRICTION_MIN: Final = 4
PAIN_RESTRICTION_DAYS: Final = 3


@dataclass(frozen=True, slots=True)
class PainRestriction:
    """Each restricted region with the first day it is no longer restricted (3 days after
    its own latest report)."""

    ends: tuple[tuple[BodyRegion, date], ...] = ()

    @property
    def regions(self) -> frozenset[BodyRegion]:
        return frozenset(region for region, _ in self.ends)

    @property
    def until(self) -> date | None:
        """The first day no region is restricted."""
        return max((end for _, end in self.ends), default=None)

    def applies(self, day: date) -> frozenset[BodyRegion]:
        return frozenset(region for region, end in self.ends if day < end)


def recent_pain(checkins: Iterable[DailyCheckIn], today: date) -> list[DailyCheckIn]:
    """The check-ins of the last 3 days reporting pain of 4 or more."""
    since = today - timedelta(days=PAIN_RESTRICTION_DAYS - 1)
    return [
        item
        for item in checkins
        if item.pain is not None
        and item.pain.intensity >= PAIN_RESTRICTION_MIN
        and since <= item.training_day <= today
    ]


def pain_restriction(checkins: Iterable[DailyCheckIn], today: date) -> PainRestriction:
    named = [
        (item.training_day, region)
        for item in recent_pain(checkins, today)
        if item.pain is not None and (region := region_named(item.pain.location)) is not None
    ]
    latest: dict[BodyRegion, date] = {}
    for day, region in named:
        latest[region] = max(day, latest.get(region, day))
    return PainRestriction(
        tuple(
            (region, latest[region] + timedelta(days=PAIN_RESTRICTION_DAYS))
            for region in sorted(latest, key=lambda item: item.value)
        )
    )
