"""Definition of Ready, baseline sufficiency and the coaching lifecycle (onboarding spec 2 to
4; FR-ONB-003 to FR-ONB-006). Synthetic data only."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from activity_world import MutableClock
from coach.application.coaching import CoachingLifecycle
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.domain.athlete.experience import (
    CyclingExperience,
    FtpValue,
    RaceResult,
    RunningExperience,
)
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.coaching.state import CoachingState, LifecycleState, Trigger
from coach.domain.common.provenance import FieldValue, ValueSource
from coach.domain.common.sport import Sport
from coach.domain.onboarding.planner import OnboardingSnapshot
from coach.domain.onboarding.readiness import (
    HistoryFacts,
    HistoryGap,
    ReadinessInput,
    ReadinessStatus,
    assess_readiness,
    history_sufficiency,
    reliable_anchor,
)
from coach.infrastructure.memory import InMemoryAuditLog, InMemoryRepository, RecordingEventSink
from samples import (
    ATHLETE,
    NOW,
    age_predicted_zones,
    full_profile,
    sample_availability,
    sample_consent_ledger,
    sample_goal,
)

TODAY = NOW.date()
FULL = HistoryFacts(
    activities=20,
    goal_sport_activities=12,
    goal_sport_recent=8,
    recovery_days=10,
    garmin_connected=True,
)


def snapshot(**changes: object) -> OnboardingSnapshot:
    fields: dict[str, object] = {
        "profile": full_profile(),
        "consents": sample_consent_ledger(),
        "availability": sample_availability(),
        "goals": (sample_goal(),),
    }
    fields.update(changes)
    return OnboardingSnapshot(**fields)  # type: ignore[arg-type]


@pytest.mark.req("FR-ONB-003")
@pytest.mark.acceptance("AT-ONB-002")
def test_without_weekly_availability_readiness_is_not_ready() -> None:
    found = assess_readiness(snapshot(availability=None), FULL, now=NOW)
    assert found.status is ReadinessStatus.NOT_READY
    assert found.missing_required == (ReadinessInput.WEEKLY_AVAILABILITY,)
    empty = assess_readiness(
        snapshot(profile=AthleteProfile.new(ATHLETE), goals=(), availability=None),
        HistoryFacts(0, 0, 0, 0, False),
        now=NOW,
    )
    assert set(empty.missing_required) == {
        ReadinessInput.PROFILE,
        ReadinessInput.PRIMARY_GOAL,
        ReadinessInput.CURRENT_LEVEL,
        ReadinessInput.WEEKLY_AVAILABILITY,
        ReadinessInput.HEALTH_CONSTRAINTS,
        ReadinessInput.RESOURCES,
        ReadinessInput.STRENGTH_EXPERIENCE,
        ReadinessInput.TENNIS_SCHEDULE,
    }


@pytest.mark.req("FR-ONB-004")
@pytest.mark.acceptance("AT-ONB-003")
def test_without_recent_history_only_a_provisional_plan_is_possible() -> None:
    facts = HistoryFacts(0, 0, 0, 0, garmin_connected=False)
    found = assess_readiness(snapshot(), facts, now=NOW)
    assert found.status is ReadinessStatus.PROVISIONAL
    assert found.missing_preferred == (
        ReadinessInput.ACTIVITY_HISTORY,
        ReadinessInput.RECOVERY_HISTORY,
        ReadinessInput.GARMIN_INTEGRATION,
    )
    assert assess_readiness(snapshot(), FULL, now=NOW).status is ReadinessStatus.READY


@pytest.mark.req("FR-ONB-003", "FR-ONB-002")
def test_freshness_and_alternatives_of_required_inputs() -> None:
    later = NOW + timedelta(days=31)  # the health declaration is re-confirmed every 30 days
    stale = assess_readiness(snapshot(), FULL, now=later)
    assert ReadinessInput.HEALTH_CONSTRAINTS in stale.missing_required
    no_level = full_profile().apply(ProfileUpdate(running_experience=None))
    profile = replace(no_level, running_experience=None)
    without = assess_readiness(
        snapshot(profile=profile), replace(FULL, goal_sport_activities=0), now=NOW
    )
    assert ReadinessInput.CURRENT_LEVEL in without.missing_required
    with_runs = assess_readiness(snapshot(profile=profile), FULL, now=NOW)
    assert ReadinessInput.CURRENT_LEVEL not in with_runs.missing_required  # recent activities
    unknown = profile.apply(ProfileUpdate(running_experience=FieldValue.unknown(at=NOW)))
    answered = assess_readiness(
        snapshot(profile=unknown), replace(FULL, goal_sport_activities=0), now=NOW
    )
    assert ReadinessInput.CURRENT_LEVEL not in answered.missing_required  # UNKNOWN is answered


@pytest.mark.req("FR-ONB-005", "FR-PRF-002")
@pytest.mark.acceptance("AT-ONB-004")
def test_baseline_needs_six_recent_sessions_and_a_reliable_anchor() -> None:
    bare = AthleteProfile.new(ATHLETE).apply(
        ProfileUpdate(
            birth_date=FieldValue.declared(date(1985, 3, 2), at=NOW),
            zone_models=(age_predicted_zones(),),  # an estimate: never an anchor
        )
    )
    found = history_sufficiency(bare, Sport.RUNNING, recent_activities=3, today=TODAY)
    assert (found.sufficient, found.gaps) == (
        False,
        (HistoryGap.FEW_ACTIVITIES, HistoryGap.NO_INTENSITY_ANCHOR),
    )
    assert not reliable_anchor(bare, Sport.RUNNING, today=TODAY)
    race = RaceResult(2940, TODAY - timedelta(days=200), ValueSource.ATHLETE_DECLARED)
    raced = bare.apply(
        ProfileUpdate(
            running_experience=FieldValue.declared(RunningExperience(recent_10k=race), at=NOW)
        )
    )
    assert reliable_anchor(raced, Sport.RUNNING, today=TODAY)
    old = RaceResult(2940, TODAY - timedelta(days=400), ValueSource.ATHLETE_DECLARED)
    aged = bare.apply(
        ProfileUpdate(
            running_experience=FieldValue.declared(RunningExperience(recent_10k=old), at=NOW)
        )
    )
    assert not reliable_anchor(aged, Sport.RUNNING, today=TODAY)  # older than 12 months
    measured = CyclingExperience(ftp=FtpValue(210, TODAY - timedelta(days=30), measured=True))
    rider = bare.apply(ProfileUpdate(cycling_experience=FieldValue.declared(measured, at=NOW)))
    assert reliable_anchor(rider, Sport.CYCLING, today=TODAY)
    assert not reliable_anchor(rider, Sport.RUNNING, today=TODAY)  # the FTP anchors cycling only
    assert history_sufficiency(
        full_profile(), Sport.RUNNING, recent_activities=6, today=TODAY
    ).sufficient


@pytest.mark.req("FR-ONB-006", "SEC-AUD-001")
def test_the_lifecycle_moves_only_where_the_state_machine_allows() -> None:
    clock = MutableClock()
    audit = InMemoryAuditLog()
    events = RecordingEventSink()
    lifecycle = CoachingLifecycle(
        InMemoryRepository[CoachingState](), audit=audit, events=events, clock=clock
    )
    scope = OwnerScope(ATHLETE)
    assert lifecycle.advance(scope, Trigger.PLAN_PROPOSED) is False  # not from NEW_USER
    assert lifecycle.current(scope).lifecycle is LifecycleState.NEW_USER
    assert lifecycle.advance(scope, Trigger.CONSENT_DECISIONS_RECORDED) is True
    assert lifecycle.advance(scope, Trigger.REQUIRED_INPUTS_WITH_HISTORY) is True
    assert lifecycle.current(scope).lifecycle is LifecycleState.GOAL_DEFINED
    assert (
        EventName.COACHING_STATE_CHANGED,
        {"from": "ONBOARDING", "to": "GOAL_DEFINED", "trigger": "REQUIRED_INPUTS_WITH_HISTORY"},
    ) in events.events
    documents = [record.document for record in audit.all_records()]
    assert [item["action"] for item in documents] == ["coaching.state_changed"] * 2
