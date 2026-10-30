"""Daily check-in and red flags (recovery spec 1; safety spec 2 to 4). Synthetic data only."""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from activity_world import MutableClock
from coach.application.coaching import CoachingConditions
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.recovery import CheckInAnswers, RecoveryService
from coach.application.safety import hold_notice
from coach.application.screening import SecretScreen
from coach.domain.coaching.state import Actor, CoachingState, Condition
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.recovery.checkin import (
    MAX_LOCATION,
    MAX_NOTES,
    CheckInPain,
    DailyCheckIn,
    PainOnset,
    RedFlag,
    latest_per_day,
)
from coach.domain.recovery.warning_phrases import (
    PHRASE_RULES_VERSION,
    PHRASES,
    detect,
    normalised,
)
from coach.infrastructure.memory import InMemoryAuditLog, InMemoryRepository, RecordingEventSink
from samples import ATHLETE, BUENOS_AIRES, NOW
from schemas import CONTRACTS_DIR, load_json

SCOPE = OwnerScope(ATHLETE)
PLANTED = "Zx81Qp0LmN4vB7cT2wYe9KdR"  # pragma: allowlist secret
PASTED = f"the app said api_key={PLANTED} now"


def checkin(**changes: Any) -> DailyCheckIn:
    fields: dict[str, Any] = {
        "checkin_id": UUID(int=1),
        "athlete_id": ATHLETE,
        "recorded_at": NOW,
        "training_day": NOW.date(),
        "fatigue": 3,
    }
    fields.update(changes)
    return DailyCheckIn(**fields)


@pytest.mark.req("FR-REC-001")
@pytest.mark.parametrize(
    "changes",
    [
        {"fatigue": 0},
        {"fatigue": 6},
        {"sleep_quality": 2.5},
        {"motivation": True},
        {"fatigue": None},  # states nothing
        {"pain": CheckInPain(intensity=3)},  # pain without the flag
        {"red_flags": (RedFlag.RF_NEURO, RedFlag.RF_NEURO)},
    ],
)
def test_a_checkin_follows_the_documented_scale(changes: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        checkin(**changes)


@pytest.mark.req("FR-REC-001", "SAFE-MED-002")
def test_severe_pain_not_known_to_be_gradual_is_a_red_flag_by_itself() -> None:
    sudden = CheckInPain(intensity=8, location="calf", onset=PainOnset.SUDDEN)
    assert checkin(pain_flag=True, pain=sudden).red_flags_raised() == (
        RedFlag.RF_SEVERE_ACUTE_PAIN,
    )
    unknown = CheckInPain(intensity=7)  # onset not stated: fail-safe
    assert checkin(pain_flag=True, pain=unknown).red_flags_raised() == (
        RedFlag.RF_SEVERE_ACUTE_PAIN,
    )
    gradual = CheckInPain(intensity=8, onset=PainOnset.GRADUAL)
    assert checkin(pain_flag=True, pain=gradual).red_flags_raised() == ()
    assert checkin(pain_flag=True, pain=CheckInPain(intensity=6)).red_flags_raised() == ()
    with pytest.raises(DomainValidationError):
        CheckInPain(intensity=11)


@pytest.mark.req("FR-REC-001")
def test_the_latest_checkin_of_a_day_is_in_force() -> None:
    first = checkin()
    later = checkin(checkin_id=UUID(int=2), recorded_at=NOW + timedelta(hours=2), fatigue=4)
    other_day = checkin(checkin_id=UUID(int=3), training_day=NOW.date() - timedelta(days=1))
    found = latest_per_day(iter([later, first, other_day]))  # any iterable, read once
    assert found[NOW.date()] == later
    assert set(found) == {NOW.date(), NOW.date() - timedelta(days=1)}


class RecoveryWorld:
    def __init__(self) -> None:
        self.clock = MutableClock()
        counter = itertools.count(1)
        self.events = RecordingEventSink()
        self.audit = InMemoryAuditLog()
        self.conditions = CoachingConditions(
            InMemoryRepository[CoachingState](),
            audit=self.audit,
            events=self.events,
            clock=self.clock,
        )
        self.checkins = InMemoryRepository[DailyCheckIn]()
        self.service = RecoveryService(
            self.checkins,
            conditions=self.conditions,
            screen=SecretScreen(self.events),
            timezone_of=lambda _scope: BUENOS_AIRES,
            events=self.events,
            clock=self.clock,
            ids=lambda: UUID(int=next(counter)),
        )


@pytest.mark.req("FR-REC-001", "SEC-SEC-005")
def test_recording_supersedes_the_days_earlier_checkin_and_screens_free_text() -> None:
    world = RecoveryWorld()
    today = world.service.today(SCOPE)
    assert today == date(2026, 10, 24)  # 15:00 in Buenos Aires
    first = world.service.record_checkin(SCOPE, CheckInAnswers(fatigue=4, sleep_quality=2))
    world.clock.advance(hours=1)
    second = world.service.record_checkin(
        SCOPE, CheckInAnswers(fatigue=3, sleep_quality=2, notes=PASTED)
    )
    assert second.checkin.supersedes == first.checkin.checkin_id
    assert second.checkin.notes is not None
    assert PLANTED not in second.checkin.notes
    assert second.secret_notice is not None
    (current,) = world.service.checkins(SCOPE, until=today, days=7)
    assert current.fatigue == 3
    assert (EventName.CHECKIN_RECORDED, {"red_flags": "0"}) in world.events.events
    assert Condition.SAFETY_HOLD not in world.conditions.current(SCOPE).conditions


@pytest.mark.req("SAFE-MED-002", "SAFE-MED-004", "SEC-AUD-001")
@pytest.mark.acceptance("AT-SAFE-001")
def test_red_flags_activate_the_safety_hold_with_a_fixed_notice() -> None:
    world = RecoveryWorld()
    result = world.service.record_checkin(
        SCOPE,
        CheckInAnswers(pain_intensity=8, pain_location="gemelo", pain_onset=PainOnset.SUDDEN),
    )
    assert result.red_flags == (RedFlag.RF_SEVERE_ACUTE_PAIN,)
    assert Condition.SAFETY_HOLD in world.conditions.current(SCOPE).conditions
    assert result.safety_notice is not None
    assert "emergencia" in result.safety_notice
    assert (EventName.SAFETY_HOLD_ACTIVATED, {"reason": "RF_SEVERE_ACUTE_PAIN"}) in (
        world.events.events
    )
    documents = [record.document for record in world.audit.all_records()]
    assert ("coaching.condition_activated", ["RF_SEVERE_ACUTE_PAIN"]) in [
        (item["action"], item.get("reasonCodes")) for item in documents
    ]
    with pytest.raises(InvalidTransitionError):  # only the athlete releases it
        world.conditions.clear(SCOPE, Condition.SAFETY_HOLD, reason="AUTO", actor=Actor.SYSTEM)
    world.conditions.clear(
        SCOPE, Condition.SAFETY_HOLD, reason="CLEARED_BY_PROFESSIONAL", actor=Actor.ATHLETE
    )
    assert (EventName.SAFETY_HOLD_RELEASED, {"reason": "CLEARED_BY_PROFESSIONAL"}) in (
        world.events.events
    )


@pytest.mark.req("SAFE-MED-001", "SAFE-MED-004")
def test_the_safety_notice_states_the_sign_and_never_names_a_cause() -> None:
    plain = hold_notice([RedFlag.RF_PALPITATIONS, RedFlag.RF_PALPITATIONS])
    assert "(palpitaciones o latidos irregulares)" in plain  # the reason, once
    assert "emergencia" in plain  # palpitations with dizziness may be acute
    assert "profesional de la salud" in plain
    english = hold_notice([RedFlag.RF_CHEST_PAIN, RedFlag.RF_BREATHING], language="en-GB")
    assert english.startswith("If the symptom is severe")
    assert "(chest pain or pressure; unusual breathing difficulty)" in english
    for text in (plain, english, hold_notice([], "xx")):
        lowered = text.lower()
        assert not any(word in lowered for word in ("normal", "nothing to worry", "diagn"))


@pytest.mark.req("FR-REC-001", "NFR-REL-004")
def test_the_vocabularies_match_the_contract() -> None:
    schema = load_json(CONTRACTS_DIR / "daily-checkin.schema.json")
    flags = schema["$defs"]["redFlags"]["items"]["enum"]
    assert set(flags) == {flag.value for flag in RedFlag}
    assert schema["properties"]["detectedFlags"] == {"$ref": "#/$defs/redFlags"}
    notes = schema["properties"]["notes"]["allOf"][1]["maxLength"]
    location = schema["properties"]["pain"]["oneOf"][0]["properties"]["location"]
    assert (notes, location["allOf"][1]["maxLength"]) == (MAX_NOTES, MAX_LOCATION)
    onset = schema["properties"]["pain"]["oneOf"][0]["properties"]["onset"]["enum"]
    assert set(onset) == {item.value for item in PainOnset} | {None}


@pytest.mark.req("SAFE-MED-002", "SAFE-MED-003")
def test_red_flags_are_kept_and_the_hold_activated_whatever_else_is_invalid() -> None:
    world = RecoveryWorld()
    answers = CheckInAnswers(
        sleep_quality=6,  # off the scale
        red_flags=(RedFlag.RF_FAINTING, RedFlag.RF_FAINTING),  # repeated: listed once
        notes="x" * 2000,  # too long
    )
    result = world.service.record_checkin(SCOPE, answers)
    assert Condition.SAFETY_HOLD in world.conditions.current(SCOPE).conditions
    assert result.not_recorded is not None
    assert result.red_flags == (RedFlag.RF_FAINTING,)
    assert (result.checkin.sleep_quality, result.checkin.notes) == (None, None)
    assert result.safety_notice is not None
    (stored,) = world.checkins.list_all(SCOPE)
    assert stored.red_flags_raised() == (RedFlag.RF_FAINTING,)
    with pytest.raises(DomainValidationError):  # without red flags, invalid answers fail
        world.service.record_checkin(SCOPE, CheckInAnswers(sleep_quality=6))


class FailingCheckins(InMemoryRepository[DailyCheckIn]):
    def add(self, scope: OwnerScope, entity: DailyCheckIn) -> DailyCheckIn:
        raise RuntimeError("storage down")


@pytest.mark.req("SAFE-MED-002", "SAFE-MED-003")
def test_the_hold_is_activated_before_the_checkin_is_stored() -> None:
    world = RecoveryWorld()
    service = RecoveryService(
        FailingCheckins(),
        conditions=world.conditions,
        screen=SecretScreen(world.events),
        timezone_of=lambda _scope: BUENOS_AIRES,
        events=world.events,
        clock=world.clock,
        ids=lambda: UUID(int=99),
    )
    with pytest.raises(RuntimeError):
        service.record_checkin(SCOPE, CheckInAnswers(red_flags=(RedFlag.RF_NEURO,)))
    assert Condition.SAFETY_HOLD in world.conditions.current(SCOPE).conditions


@pytest.mark.req("SAFE-MED-002", "SEC-INJ-001")
@pytest.mark.parametrize(
    ("answers", "flags"),
    [
        (CheckInAnswers(notes="Ayer sentí DOLOR en el pecho subiendo"), {RedFlag.RF_CHEST_PAIN}),
        (CheckInAnswers(notes="I couldn't breathe on the hill"), {RedFlag.RF_BREATHING}),
        (CheckInAnswers(notes="no tuve palpitaciones"), {RedFlag.RF_PALPITATIONS}),  # fail-safe
        (
            CheckInAnswers(notes="me desmayé y perdí fuerza"),
            {RedFlag.RF_FAINTING, RedFlag.RF_STRENGTH_LOSS},
        ),
        (
            CheckInAnswers(pain_intensity=3, pain_location="Pecho, lado izquierdo"),
            {RedFlag.RF_CHEST_PAIN},
        ),
        (CheckInAnswers(pain_intensity=3, pain_location="gemelo"), set()),
        (CheckInAnswers(notes="piernas pesadas, pechuga de pollo en la cena"), set()),
    ],
)
def test_warning_phrases_and_a_chest_pain_activate_the_hold(
    answers: CheckInAnswers, flags: set[RedFlag]
) -> None:
    world = RecoveryWorld()
    result = world.service.record_checkin(SCOPE, answers)
    assert set(result.red_flags) == flags
    assert (Condition.SAFETY_HOLD in world.conditions.current(SCOPE).conditions) == bool(flags)
    assert set(result.checkin.detected_flags) == flags


@pytest.mark.req("SAFE-MED-002")
def test_phrase_lists_are_normalised_and_versioned() -> None:
    assert normalised("¡Dolor   TORÁCICO!") == " dolor toracico "
    assert detect("Perdi la vision un rato") == (RedFlag.RF_NEURO,)
    assert detect(None) == ()
    assert PHRASE_RULES_VERSION == "1.0.0"
    for flag, phrases in PHRASES.items():
        for phrase in phrases:
            assert normalised(phrase).strip() == phrase, (flag, phrase)


@pytest.mark.req("FR-REC-001")
@pytest.mark.parametrize(
    "answers",
    [
        CheckInAnswers(pain_location="rodilla"),  # a location without a pain
        CheckInAnswers(pain_onset=PainOnset.GRADUAL, fatigue=3),
        CheckInAnswers(pain_intensity=0, pain_location="rodilla"),
        CheckInAnswers(pain_intensity=4, pain_flag=False),
    ],
)
def test_pain_answers_are_consistent(answers: CheckInAnswers) -> None:
    with pytest.raises(DomainValidationError):
        RecoveryWorld().service.record_checkin(SCOPE, answers)


@pytest.mark.req("FR-REC-001")
def test_pain_intensity_zero_is_no_pain() -> None:
    result = RecoveryWorld().service.record_checkin(SCOPE, CheckInAnswers(pain_intensity=0))
    assert (result.checkin.pain_flag, result.checkin.pain) == (False, None)
