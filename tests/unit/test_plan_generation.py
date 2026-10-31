"""The goal sport's weekly history the generator reads (planning spec 5.1). Synthetic data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest

from coach.application.consent import ConsentRequiredError
from coach.application.ownership import OwnerScope
from coach.application.plan_generation import weekly_minutes_from
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange
from coach.domain.consent.ledger import ConsentType
from samples import ATHLETE

SCOPE = OwnerScope(ATHLETE)
TODAY = date(2026, 10, 28)  # a Wednesday: its week is not complete


@dataclass(frozen=True)
class View:
    sport: Sport
    training_day: date
    seconds: float | None
    counts_toward_totals: bool = True

    def number(self, path: str) -> float | None:
        assert path == "timing.elapsedSeconds"
        return self.seconds


class Analytics:
    def __init__(self, views: list[View], *, consent: bool = True) -> None:
        self.views = views
        self.consent = consent
        self.asked: list[DateRange] = []

    def activities(self, scope: OwnerScope, period: DateRange) -> list[View]:
        if not self.consent:
            raise ConsentRequiredError((ConsentType.ACTIVITIES,))
        self.asked.append(period)
        return [view for view in self.views if period.start <= view.training_day <= period.end]


@pytest.mark.req("FR-PLN-006")
def test_weekly_minutes_are_the_last_complete_weeks_of_the_sport() -> None:
    monday = date(2026, 10, 26)
    views = [
        View(Sport.RUNNING, monday - timedelta(days=1), 3600),  # last week: 60 min
        View(Sport.RUNNING, monday - timedelta(days=3), 1800),  # last week: 30 min more
        View(Sport.RUNNING, monday - timedelta(weeks=8), 2400),  # oldest week: 40 min
        View(Sport.CYCLING, monday - timedelta(days=2), 7200),  # another sport
        View(Sport.RUNNING, monday - timedelta(days=4), 600, counts_toward_totals=False),
        View(Sport.RUNNING, monday - timedelta(days=5), None),  # duration unknown
        View(Sport.RUNNING, TODAY, 3000),  # this week: not complete
    ]
    analytics: Any = Analytics(views)
    weekly = weekly_minutes_from(analytics)(SCOPE, Sport.RUNNING, TODAY)
    assert weekly == (40.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 90.0)
    assert analytics.asked == [DateRange(monday - timedelta(weeks=8), monday - timedelta(days=1))]
    refused: Any = Analytics(views, consent=False)
    assert weekly_minutes_from(refused)(SCOPE, Sport.RUNNING, TODAY) == ()
