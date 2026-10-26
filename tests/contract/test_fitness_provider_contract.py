"""FitnessProvider contract: the fake provider and the Garmin adapter behave alike (ADR-007).

The Garmin adapter runs over a scripted transport with payloads shaped by the Swagger
schemas; nothing contacts Garmin.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from io import BytesIO

import pytest

from coach.application.providers import (
    JSON_MEDIA_TYPE,
    ZIP_MEDIA_TYPE,
    ActivityRef,
    FitnessProvider,
)
from coach.domain.common.time import DateRange
from coach.domain.integration.capabilities import AccessTier
from fake_provider import FakeProvider
from garmin_world import GarminWorld, response

pytestmark = pytest.mark.req("INT-GAR-001", "INT-GAR-005", "INT-GAR-009")

WINDOW = DateRange(date(2026, 10, 14), date(2026, 10, 20))
IDS = (5001, 5002, 5003)
MISSING = 5999


@dataclass
class Subject:
    provider: FitnessProvider
    remove_file: Callable[[int], None]


def _fake() -> Subject:
    fake = FakeProvider()
    for hour, activity_id in enumerate(IDS, start=6):
        fake.add(activity_id, date(2026, 10, 15), hour=hour)
    fake.add(5100, date(2026, 10, 25))  # outside the window

    def remove(activity_id: int) -> None:
        fake.activities[activity_id].original = None

    return Subject(fake, remove)


def _garmin() -> Subject:
    world = GarminWorld()
    world.transport.activities["2026-10-15"] = list(IDS)
    world.transport.activities["2026-10-25"] = [5100]
    world.transport.script(f"/activity-service/activity/{MISSING}", response(404))
    world.transport.script(f"/download-service/files/activity/{MISSING}", response(404))

    def remove(activity_id: int) -> None:
        world.transport.script(f"/download-service/files/activity/{activity_id}", response(404))

    return Subject(world.provider, remove)


@pytest.fixture(params=[_fake, _garmin], ids=["fake", "garmin"])
def subject(request: pytest.FixtureRequest) -> Subject:
    factory: Callable[[], Subject] = request.param
    return factory()


def test_capabilities_name_the_provider(subject: Subject) -> None:
    capabilities = subject.provider.get_capabilities()
    assert capabilities.provider == subject.provider.provider == "garmin"
    assert capabilities.access_tier is AccessTier.PERSONAL


def test_a_window_is_listed_in_offset_pages_with_its_raw_payload(subject: Subject) -> None:
    first = subject.provider.get_activities(WINDOW, 0, 2)
    second = subject.provider.get_activities(WINDOW, 2, 2)
    listed = [ref.provider_activity_id for ref in (*first.refs, *second.refs)]
    assert listed == [str(item) for item in IDS]
    for page in (first, second):
        assert (page.payload.category, page.payload.media_type, page.payload.window) == (
            "GARMIN_ACTIVITY_LIST",
            JSON_MEDIA_TYPE,
            WINDOW,
        )
        assert page.payload.data
    assert subject.provider.get_activities(WINDOW, 3, 2).refs == ()


def test_details_are_a_json_record_named_by_the_activity(subject: Subject) -> None:
    payload = subject.provider.get_activity_details(ActivityRef("5001"))
    assert payload is not None
    assert (payload.category, payload.media_type, payload.provider_object_id) == (
        "GARMIN_ACTIVITY",
        JSON_MEDIA_TYPE,
        "5001",
    )
    assert payload.data.startswith(b"{")
    assert subject.provider.get_activity_details(ActivityRef(str(MISSING))) is None


def test_the_original_file_is_a_zip_or_nothing(subject: Subject) -> None:
    payload = subject.provider.get_activity_file(ActivityRef("5002"))
    assert payload is not None
    assert (payload.category, payload.media_type, payload.provider_object_id) == (
        "GARMIN_ACTIVITY_FILE",
        ZIP_MEDIA_TYPE,
        "5002",
    )
    with zipfile.ZipFile(BytesIO(payload.data)) as archive:
        assert len(archive.namelist()) == 1
    subject.remove_file(5003)
    assert subject.provider.get_activity_file(ActivityRef("5003")) is None
    assert subject.provider.get_activity_file(ActivityRef(str(MISSING))) is None
