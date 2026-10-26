"""A fake FitnessProvider for contract, sync and degradation tests (ADR-007). No network.

Holds synthetic activities by day and answers like the port says: offset pages over a date
window, details and original files by ID. Failures are scripted per method.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from coach.application.providers import (
    JSON_MEDIA_TYPE,
    ZIP_MEDIA_TYPE,
    ActivityPage,
    ActivityRef,
    ProviderError,
    RawPayload,
)
from coach.domain.common.time import DateRange
from coach.domain.integration.capabilities import ProviderCapabilities
from coach.integrations.garmin.capabilities import personal_capabilities
from garmin_samples import activity_on

LIST, DETAILS, FILE = "list", "details", "file"


@dataclass
class FakeActivity:
    day: date
    details: bytes | None
    original: bytes | None


@dataclass
class FakeProvider:
    capabilities: ProviderCapabilities = field(default_factory=personal_capabilities)
    activities: dict[int, FakeActivity] = field(default_factory=dict)
    failures: dict[str, list[ProviderError | Exception]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    before_call: Callable[[str], None] | None = None

    @property
    def provider(self) -> str:
        return "garmin"

    def add(
        self,
        activity_id: int,
        day: date,
        *,
        details: bytes | bool | None = True,
        original: bytes | bool | None = True,
        hour: int = 7,
    ) -> None:
        made_details, made_file = activity_on(day, activity_id, hour=hour)
        self.activities[activity_id] = FakeActivity(
            day,
            made_details if details is True else (details or None),
            made_file if original is True else (original or None),
        )

    def fail(self, method: str, *errors: ProviderError | Exception) -> None:
        self.failures.setdefault(method, []).extend(errors)

    def count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)

    def _call(self, method: str, argument: str) -> None:
        self.calls.append((method, argument))
        if self.before_call is not None:
            self.before_call(method)
        queue = self.failures.get(method)
        if queue:
            raise queue.pop(0)

    def get_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    def get_activities(self, window: DateRange, offset: int, limit: int) -> ActivityPage:
        self._call(LIST, f"{window.start}:{offset}")
        ids = sorted(
            (item.day, activity_id)
            for activity_id, item in self.activities.items()
            if window.start <= item.day <= window.end
        )
        chosen = [activity_id for _, activity_id in ids[offset : offset + limit]]
        body = json.dumps([{"activityId": activity_id} for activity_id in chosen]).encode()
        payload = RawPayload("garmin", "GARMIN_ACTIVITY_LIST", JSON_MEDIA_TYPE, body, window=window)
        return ActivityPage(tuple(ActivityRef(str(item)) for item in chosen), payload)

    def get_activity_details(self, ref: ActivityRef) -> RawPayload | None:
        self._call(DETAILS, ref.provider_activity_id)
        item = self.activities.get(int(ref.provider_activity_id))
        if item is None or item.details is None:
            return None
        return RawPayload(
            "garmin",
            "GARMIN_ACTIVITY",
            JSON_MEDIA_TYPE,
            item.details,
            provider_object_id=ref.provider_activity_id,
        )

    def get_activity_file(self, ref: ActivityRef) -> RawPayload | None:
        self._call(FILE, ref.provider_activity_id)
        item = self.activities.get(int(ref.provider_activity_id))
        if item is None or item.original is None:
            return None
        return RawPayload(
            "garmin",
            "GARMIN_ACTIVITY_FILE",
            ZIP_MEDIA_TYPE,
            item.original,
            provider_object_id=ref.provider_activity_id,
        )
