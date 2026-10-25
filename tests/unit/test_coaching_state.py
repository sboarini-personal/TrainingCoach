from datetime import UTC, datetime
from uuid import UUID

import pytest

from coach.domain.coaching.state import (
    Actor,
    CoachingState,
    Condition,
    LifecycleState,
    Trigger,
)
from coach.domain.common.errors import InvalidTransitionError

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
L = LifecycleState

# Transition table of spec/functional/onboarding.spec.md section 4.1, copied independently of
# the implementation so the test checks the code against the spec.
SPEC_TRANSITIONS = {
    (L.NEW_USER, Trigger.CONSENT_DECISIONS_RECORDED): L.ONBOARDING,
    (L.ONBOARDING, Trigger.REQUIRED_INPUTS_WITHOUT_HISTORY): L.BASELINE_COLLECTION,
    (L.ONBOARDING, Trigger.REQUIRED_INPUTS_WITH_HISTORY): L.GOAL_DEFINED,
    (L.BASELINE_COLLECTION, Trigger.BASELINE_COMPLETED): L.GOAL_DEFINED,
    (L.GOAL_DEFINED, Trigger.PLAN_PROPOSED): L.PLAN_READY,
    (L.PLAN_READY, Trigger.PLAN_ACTIVATED): L.TRAINING,
    (L.TRAINING, Trigger.ANALYSIS_STARTED): L.ANALYSIS,
    (L.ANALYSIS, Trigger.DECISION_MAINTAIN): L.TRAINING,
    (L.ANALYSIS, Trigger.DECISION_ADAPT): L.ADAPTATION,
    (L.ADAPTATION, Trigger.ADAPTED_VERSION_ACTIVATED): L.TRAINING,
    (L.ADAPTATION, Trigger.ADAPTATION_REJECTED): L.TRAINING,
    (L.TRAINING, Trigger.GOAL_CLOSED): L.GOAL_DEFINED,
}


def state(lifecycle: LifecycleState, *conditions: Condition) -> CoachingState:
    return CoachingState(athlete_id=ATHLETE, lifecycle=lifecycle, conditions=frozenset(conditions))


@pytest.mark.req("FR-ONB-006")
def test_new_athlete_starts_as_new_user_without_conditions() -> None:
    initial = CoachingState.new(ATHLETE)
    assert initial.lifecycle is L.NEW_USER
    assert initial.conditions == frozenset()


@pytest.mark.req("FR-ONB-006")
@pytest.mark.parametrize(("start", "trigger"), sorted(SPEC_TRANSITIONS))
def test_specified_transitions(start: LifecycleState, trigger: Trigger) -> None:
    expected = SPEC_TRANSITIONS[start, trigger]
    new_state, event = state(start).apply(trigger, actor=Actor.SYSTEM, at=NOW)
    assert new_state.lifecycle is expected
    assert (event.from_state, event.to_state, event.trigger) == (start, expected, trigger)
    assert event.at == NOW


@pytest.mark.req("FR-ONB-006")
def test_every_unspecified_transition_is_rejected() -> None:
    for lifecycle in LifecycleState:
        for trigger in Trigger:
            if (lifecycle, trigger) in SPEC_TRANSITIONS:
                continue
            with pytest.raises(InvalidTransitionError):
                state(lifecycle).apply(trigger, actor=Actor.SYSTEM, at=NOW)


@pytest.mark.req("FR-ONB-006")
def test_conditions_coexist_with_the_lifecycle_state() -> None:
    training, _ = state(L.TRAINING).activate(
        Condition.INTEGRATION_ERROR, actor=Actor.SYSTEM, at=NOW
    )
    training, _ = training.activate(Condition.RECOVERY_BLOCK, actor=Actor.SYSTEM, at=NOW)
    assert training.lifecycle is L.TRAINING
    assert training.conditions == {Condition.INTEGRATION_ERROR, Condition.RECOVERY_BLOCK}
    analysing, _ = training.apply(Trigger.ANALYSIS_STARTED, actor=Actor.SYSTEM, at=NOW)
    assert analysing.conditions == training.conditions


@pytest.mark.req("FR-ONB-006")
@pytest.mark.parametrize(
    ("lifecycle", "trigger"),
    [(L.PLAN_READY, Trigger.PLAN_ACTIVATED), (L.ADAPTATION, Trigger.ADAPTED_VERSION_ACTIVATED)],
)
def test_safety_hold_blocks_plan_activation(lifecycle: LifecycleState, trigger: Trigger) -> None:
    with pytest.raises(InvalidTransitionError, match="SAFETY_HOLD"):
        state(lifecycle, Condition.SAFETY_HOLD).apply(trigger, actor=Actor.ATHLETE, at=NOW)


@pytest.mark.req("FR-ONB-006")
def test_rejecting_an_adaptation_is_allowed_during_safety_hold() -> None:
    held = state(L.ADAPTATION, Condition.SAFETY_HOLD)
    new_state, _ = held.apply(Trigger.ADAPTATION_REJECTED, actor=Actor.ATHLETE, at=NOW)
    assert new_state.lifecycle is L.TRAINING
    assert Condition.SAFETY_HOLD in new_state.conditions


@pytest.mark.req("FR-ONB-006")
@pytest.mark.parametrize("actor", list(Actor))
def test_anyone_can_activate_a_safety_hold(actor: Actor) -> None:
    held, event = state(L.TRAINING).activate(
        Condition.SAFETY_HOLD, actor=actor, at=NOW, reason="RF_CHEST_PAIN"
    )
    assert Condition.SAFETY_HOLD in held.conditions
    assert event is not None
    assert (event.condition, event.activated, event.actor, event.reason) == (
        Condition.SAFETY_HOLD,
        True,
        actor,
        "RF_CHEST_PAIN",
    )


@pytest.mark.req("SAFE-MED-005")
@pytest.mark.parametrize("actor", [Actor.AGENT, Actor.SYSTEM])
def test_only_the_athlete_can_release_a_safety_hold(actor: Actor) -> None:
    held = state(L.TRAINING, Condition.SAFETY_HOLD)
    with pytest.raises(InvalidTransitionError):
        held.clear(Condition.SAFETY_HOLD, actor=actor, at=NOW)
    released, event = held.clear(
        Condition.SAFETY_HOLD, actor=Actor.ATHLETE, at=NOW, reason="cleared by physician"
    )
    assert Condition.SAFETY_HOLD not in released.conditions
    assert event is not None
    assert (event.activated, event.actor) == (False, Actor.ATHLETE)


@pytest.mark.req("FR-ONB-006")
def test_other_conditions_can_be_cleared_by_the_system() -> None:
    cleared, event = state(L.TRAINING, Condition.INTEGRATION_ERROR).clear(
        Condition.INTEGRATION_ERROR, actor=Actor.SYSTEM, at=NOW
    )
    assert cleared.conditions == frozenset()
    assert event is not None


@pytest.mark.req("FR-ONB-006")
def test_repeated_condition_changes_are_no_ops_without_events() -> None:
    held = state(L.TRAINING, Condition.SAFETY_HOLD)
    same, event = held.activate(Condition.SAFETY_HOLD, actor=Actor.SYSTEM, at=NOW)
    assert same == held
    assert event is None
    unchanged, event = state(L.TRAINING).clear(Condition.RECOVERY_BLOCK, actor=Actor.SYSTEM, at=NOW)
    assert event is None
    assert unchanged.conditions == frozenset()
