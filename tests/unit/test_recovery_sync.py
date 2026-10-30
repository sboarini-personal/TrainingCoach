"""Daily recovery metrics from Garmin: normaliser, adapter and sync by consent (recovery spec
3 and 5; FR-REC-005, AT-CON-001). Payloads shaped by the Swagger schemas; nothing contacts
Garmin."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from activity_world import SCOPE
from coach.application.providers import JSON_MEDIA_TYPE, ProviderError, RawPayload
from coach.application.recovery_sync import BACKFILL_DAYS, MetricOutcome
from coach.application.sync import SyncOutcome
from coach.domain.coaching.state import Condition
from coach.domain.common.time import DateRange
from coach.domain.consent.ledger import ConsentType
from coach.domain.integration.status import IntegrationReason
from coach.domain.recovery.daily import RecoveryMetric, in_force
from coach.integrations.garmin.recovery import CATEGORIES, GarminDailyRecords
from garmin_samples import body_battery_payload, hrv_payload, resting_hr_payload, sleep_payload
from garmin_world import GarminWorld, response
from sync_world import SyncWorld

DAY = date(2026, 10, 20)
RECORDS = GarminDailyRecords()


def payload(metric: RecoveryMetric, body: bytes, window: DateRange | None = None) -> RawPayload:
    return RawPayload(
        "garmin", CATEGORIES[metric], JSON_MEDIA_TYPE, body, window=window or DateRange(DAY, DAY)
    )


# --------------------------------------------------------------------------- normaliser


@pytest.mark.req("FR-REC-003", "INT-GAR-009")
def test_each_payload_yields_its_metric_per_calendar_day() -> None:
    (resting,) = RECORDS.decode(
        RecoveryMetric.RESTING_HR, payload(RecoveryMetric.RESTING_HR, resting_hr_payload(DAY, 48))
    )
    assert (resting.day, resting.value, resting.source_path) == (DAY, 48.0, "restingHeartRate")
    week = DateRange(DAY, DAY + timedelta(days=6))
    hrv = RECORDS.decode(
        RecoveryMetric.HRV_NIGHTLY,
        payload(RecoveryMetric.HRV_NIGHTLY, hrv_payload({DAY: 55, DAY + timedelta(1): None}), week),
    )
    assert [(item.day, item.value) for item in hrv] == [(DAY, 55.0)]  # an absent value is left out
    assert hrv[0].source_path == "hrvSummaries[0].lastNightAvg"
    sleep = RECORDS.decode(
        RecoveryMetric.SLEEP_DURATION,
        payload(RecoveryMetric.SLEEP_DURATION, sleep_payload({DAY: 26_000}), week),
    )
    assert sleep[0].value == 26_000.0
    battery = RECORDS.decode(
        RecoveryMetric.BODY_BATTERY_CHARGED,
        payload(RecoveryMetric.BODY_BATTERY_CHARGED, body_battery_payload({DAY: 70}), week),
    )
    assert battery[0].value == 70.0


@pytest.mark.req("FR-REC-003", "FR-PRF-002", "SEC-INJ-001")
@pytest.mark.parametrize(
    ("metric", "body"),
    [
        (RecoveryMetric.SLEEP_DURATION, b"{not json"),
        (RecoveryMetric.SLEEP_DURATION, b'{"calendarDate": "2026-10-20"}'),  # not an array
        (RecoveryMetric.SLEEP_DURATION, b'[{"calendarDate": "20/10/2026", "sleepTimeSeconds": 1}]'),
        (RecoveryMetric.HRV_NIGHTLY, b'{"hrvSummaries": [1, 2]}'),
        (RecoveryMetric.RESTING_HR, resting_hr_payload(DAY + timedelta(days=1), 50)),  # another day
        (RecoveryMetric.RESTING_HR, b'{"restingHeartRate": NaN}'),
    ],
)
def test_a_payload_unlike_the_document_is_a_protocol_error(
    metric: RecoveryMetric, body: bytes
) -> None:
    with pytest.raises(ProviderError) as raised:
        RECORDS.decode(metric, payload(metric, body))
    assert raised.value.detail == "DAILY_FORMAT"


@pytest.mark.req("FR-REC-003", "FR-PRF-002")
def test_values_out_of_range_or_stated_twice_differently_are_left_out() -> None:
    week = DateRange(DAY, DAY + timedelta(days=6))
    items = [
        {"calendarDate": DAY.isoformat(), "sleepTimeSeconds": 90_000},  # over a day
        {"calendarDate": (DAY + timedelta(1)).isoformat(), "sleepTimeSeconds": "7h"},
        {"calendarDate": (DAY + timedelta(2)).isoformat(), "sleepTimeSeconds": 25_000},
        {"calendarDate": (DAY + timedelta(2)).isoformat(), "sleepTimeSeconds": 27_000},
        {"calendarDate": (DAY + timedelta(3)).isoformat(), "sleepTimeSeconds": 24_000},
        {"calendarDate": (DAY + timedelta(30)).isoformat(), "sleepTimeSeconds": 24_000},  # outside
    ]
    body = json.dumps(items).encode()
    found = RECORDS.decode(
        RecoveryMetric.SLEEP_DURATION, payload(RecoveryMetric.SLEEP_DURATION, body, week)
    )
    assert [(item.day, item.value) for item in found] == [(DAY + timedelta(3), 24_000.0)]
    with pytest.raises(ProviderError):  # a payload of another category
        RECORDS.decode(RecoveryMetric.HRV_NIGHTLY, payload(RecoveryMetric.SLEEP_DURATION, body))


# --------------------------------------------------------------------------- adapter


@pytest.mark.req("INT-GAR-005", "INT-GAR-001")
def test_the_adapter_calls_one_documented_endpoint_per_metric() -> None:
    world = GarminWorld()
    world.transport.script(
        "/wellness-service/wellness/dailyHeartRate",
        response(200, resting_hr_payload(DAY, 49)),
    )
    found = world.provider.get_daily(RecoveryMetric.RESTING_HR, DateRange(DAY, DAY))
    assert found is not None
    assert found.category == "GARMIN_DAILY_HEART_RATE"
    (sent,) = world.requests_to("/wellness-service/wellness/dailyHeartRate")
    assert sent.query == {"date": ["2026-10-20"]}
    month = DateRange(DAY, DAY + timedelta(days=27))
    assert world.provider.get_daily(RecoveryMetric.HRV_NIGHTLY, month) is None  # 404: none
    assert world.transport.paths()[-1] == "/hrv-service/hrv/daily/2026-10-20/2026-11-16"
    world.provider.get_daily(RecoveryMetric.SLEEP_DURATION, month)
    (sleep,) = world.requests_to("/sleep-service/")
    assert sleep.query == {"startDate": ["2026-10-20"], "endDate": ["2026-11-16"]}
    with pytest.raises(ValueError, match="at most 1 days"):
        world.provider.get_daily(RecoveryMetric.RESTING_HR, DateRange(DAY, DAY + timedelta(1)))
    data_calls = [item for item in world.transport.sent if "personal-api-key" not in item.path]
    assert all(item.method == "GET" for item in data_calls)  # read-only


# --------------------------------------------------------------------------- sync


def grant(world: SyncWorld, *types: ConsentType) -> None:
    for consent_type in types:
        world.consents.grant(SCOPE, consent_type, purpose="Assess my recovery.")


@pytest.mark.req("FR-REC-005", "PRIV-CON-002", "INT-GAR-009")
@pytest.mark.acceptance("AT-CON-001")
def test_only_consented_metrics_are_fetched_and_stored(tmp_path: Path) -> None:
    world = SyncWorld(tmp_path)
    today = date(2026, 10, 26)
    world.fake.daily[RecoveryMetric.RESTING_HR] = {
        today - timedelta(n): 48 + n % 3 for n in range(40)
    }
    world.fake.daily[RecoveryMetric.HRV_NIGHTLY] = {today - timedelta(n): 55 for n in range(40)}
    grant(world, ConsentType.RECOVERY)
    report = world.recovery_sync.sync(SCOPE)
    assert report.outcome is SyncOutcome.SYNCED
    by_metric = {item.metric: item for item in report.metrics}
    assert by_metric[RecoveryMetric.HRV_NIGHTLY].outcome is MetricOutcome.NO_CONSENT
    assert world.fake.count("daily:HRV_NIGHTLY") == 0  # never fetched without HRV consent
    resting = by_metric[RecoveryMetric.RESTING_HR]
    assert resting.requested == DateRange(today - timedelta(days=BACKFILL_DAYS - 1), today)
    assert (resting.requests, resting.stored) == (BACKFILL_DAYS, BACKFILL_DAYS)
    stored = world.recovery_observations.list_all(SCOPE)
    assert {item.metric for item in stored} == {RecoveryMetric.RESTING_HR}
    assert {item.raw_source_id for item in stored} <= {
        raw.raw_source_id for raw in world.raw.list_all(SCOPE)
    }
    again = world.recovery_sync.sync(SCOPE)  # only the settling days are asked again
    resting_again = next(item for item in again.metrics if item.metric is RecoveryMetric.RESTING_HR)
    assert (resting_again.requests, resting_again.stored) == (3, 0)


@pytest.mark.req("FR-REC-005", "PRIV-CON-002")
def test_a_revoked_consent_hides_stored_data_at_once(tmp_path: Path) -> None:
    world = SyncWorld(tmp_path)
    today = date(2026, 10, 26)
    world.fake.daily[RecoveryMetric.SLEEP_DURATION] = {
        today - timedelta(n): 25_000 for n in range(5)
    }
    grant(world, ConsentType.SLEEP)
    world.recovery_sync.sync(SCOPE)
    stored = world.recovery_observations.list_all(SCOPE)
    assert len(in_force(stored, allowed=world.consents_gate.granted(SCOPE))) == 5
    world.consents.revoke(SCOPE, ConsentType.SLEEP)
    assert in_force(stored, allowed=world.consents_gate.granted(SCOPE)) == {}


@pytest.mark.req("INT-GAR-008", "FR-REC-005")
def test_a_provider_failure_keeps_what_was_stored_and_marks_the_integration(tmp_path: Path) -> None:
    world = SyncWorld(tmp_path)
    grant(world, ConsentType.HRV)
    world.fake.daily[RecoveryMetric.HRV_NIGHTLY] = {date(2026, 10, 25): 50}
    failure = ProviderError(IntegrationReason.PROVIDER_AUTH_FAILED, "HTTP_403")
    world.fake.fail("daily:HRV_NIGHTLY", failure)
    report = world.recovery_sync.sync(SCOPE)
    assert (report.outcome, report.error) == (SyncOutcome.PENDING, "PROVIDER_AUTH_FAILED")
    assert Condition.INTEGRATION_ERROR in world.conditions_now()
    assert world.recovery_observations.list_all(SCOPE) == []
