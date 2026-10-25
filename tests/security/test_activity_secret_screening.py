"""SEC-SEC-005: a secret pasted into an activity correction is never stored (AT-SEC-001)."""

import pytest

from activity_world import SCOPE, World
from coach.application.events import EventName
from coach.application.identity import Principal
from coach.domain.activity.overrides import EntityType
from samples import USER, sample_observation

PLANTED = "Zx81Qp0LmN4vB7cT2wYe9KdR"  # pragma: allowlist secret
PASTED = "the app said api_key=Zx81Qp0LmN4vB7cT2wYe9KdR to fix it"  # pragma: allowlist secret


@pytest.mark.req("SEC-SEC-005", "FR-ACT-006")
@pytest.mark.acceptance("AT-SEC-001")
def test_secret_in_a_correction_is_redacted_before_storage() -> None:
    world = World()
    activity = world.ingest(sample_observation()).activity
    correction = world.service.correct(
        SCOPE, Principal(USER), activity.activity_id, "metadata.name", PASTED, PASTED
    )
    assert "[REDACTED-SECRET]" in correction.override.reason
    assert "[REDACTED-SECRET]" in str(correction.override.corrected_value)
    assert correction.secret_notice is not None
    assert EventName.SECRET_DETECTED in world.events.names()
    stored = world.overrides.for_entity(SCOPE, EntityType.ACTIVITY, activity.activity_id)
    view = world.service.effective(SCOPE, activity.activity_id).activity
    for text in (
        str(stored),
        str(view.values),
        str(world.events.events),
        str(world.audit.all_records()),
    ):
        assert PLANTED not in text


@pytest.mark.req("SEC-SEC-005")
def test_ordinary_corrections_carry_no_notice() -> None:
    world = World()
    activity = world.ingest(sample_observation()).activity
    correction = world.service.correct(
        SCOPE, Principal(USER), activity.activity_id, "metadata.name", "Hill repeats", "Rename"
    )
    assert correction.secret_notice is None
    assert correction.override.reason == "Rename"
