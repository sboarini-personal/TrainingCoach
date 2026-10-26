"""AT-DEG-001: degraded operation without Garmin, with the fake provider (garmin spec 5).

Given the provider returns errors or rate limits, or the key is expired, when a sync runs,
then retries follow the retry policy and honour Retry-After; INTEGRATION_ERROR is set with a
reason, the sync is marked pending and freshness is reported; local history and file
ingestion keep working; no missing data is fabricated.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from activity_world import SCOPE
from coach.application.providers import ProviderError
from coach.application.sync import SyncOutcome
from coach.domain.coaching.state import Condition
from coach.domain.integration.status import IntegrationReason, SyncState
from fake_provider import DETAILS, LIST
from file_samples import fit_file, fit_session
from sync_world import SYNC_NOW, SyncWorld

pytestmark = [
    pytest.mark.req("INT-GAR-001", "INT-GAR-008", "INT-GAR-010", "NFR-REL-002"),
    pytest.mark.acceptance("AT-DEG-001"),
]
_R = IntegrationReason


@pytest.mark.parametrize(
    ("errors", "reason", "waits"),
    [
        (
            [ProviderError(_R.PROVIDER_UNAVAILABLE, "HTTP_5XX", retry_after=3)] * 5,
            _R.PROVIDER_UNAVAILABLE,
            [3, 3, 4.0, 8.0],
        ),
        (
            [ProviderError(_R.PROVIDER_RATE_LIMITED, "HTTP_429", retry_after=600)],
            _R.PROVIDER_RATE_LIMITED,
            [],
        ),
        ([ProviderError(_R.PROVIDER_AUTH_FAILED, "KEY_EXPIRED")], _R.PROVIDER_AUTH_FAILED, []),
    ],
    ids=["unavailable", "rate-limited", "key-expired"],
)
def test_degraded_operation_without_garmin(
    tmp_path: Path, errors: list[ProviderError], reason: IntegrationReason, waits: list[float]
) -> None:
    world = SyncWorld(tmp_path)
    world.fake.add(1001, date(2026, 10, 20))
    assert world.run().outcome is SyncOutcome.SYNCED
    synced_at = world.status().last_success_at
    assert synced_at == SYNC_NOW
    before = {activity.activity_id for activity in world.activities.list_all(SCOPE)}

    world.fake.add(1002, date(2026, 10, 26), hour=9)  # exists at Garmin, unreachable now
    world.clock.advance(hours=6)
    world.fake.fail(LIST, *errors)
    report = world.run()

    # Retries follow the policy and honour Retry-After.
    assert world.waits == waits
    # INTEGRATION_ERROR with a reason, pending, freshness kept.
    status = world.status()
    assert report.outcome is SyncOutcome.PENDING
    assert (status.sync_state, status.reason) == (SyncState.PENDING, reason)
    assert status.last_success_at == synced_at
    assert Condition.INTEGRATION_ERROR in world.conditions_now()
    # Nothing fabricated: no activity for what could not be fetched.
    assert {activity.activity_id for activity in world.activities.list_all(SCOPE)} == before
    assert world.fake.count(DETAILS) == 1  # only the first, successful sync fetched details
    # Local history and file ingestion keep working.
    activity_id = next(iter(before))
    assert world.service.effective(SCOPE, activity_id).activity.activity_id == activity_id
    uploaded = world.upload(fit_file([fit_session(start=SYNC_NOW.replace(hour=11))]))
    assert uploaded.record.succeeded
    assert len(world.activities.list_all(SCOPE)) == len(before) + 1
