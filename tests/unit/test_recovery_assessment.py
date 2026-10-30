"""Recovery assessment and prescription, rules 1.0.0 (recovery spec 2 to 4; FR-REC-002 to
FR-REC-005). Synthetic values only."""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from activity_world import MutableClock
from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentGate, ConsentService
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.recovery import RecoveryService
from coach.application.recovery_assessment import RecoveryAssessmentService
from coach.application.screening import SecretScreen
from coach.domain.coaching.state import CoachingState, Condition
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.consent.ledger import ConsentLedger, ConsentType
from coach.domain.recovery.assessment import (
    PrescriptionType,
    Reason,
    RecoveryAssessment,
    RecoveryPrescription,
    RecoveryStatus,
    Signal,
    SignalState,
    assess,
    in_force_assessments,
    prescribe,
    prescriptions_in_force,
)
from coach.domain.recovery.checkin import CheckInPain, DailyCheckIn, PainOnset
from coach.domain.recovery.daily import RecoveryMetric, RecoveryObservation, observation_id
from coach.infrastructure.memory import InMemoryAuditLog, InMemoryRepository, RecordingEventSink
from samples import ATHLETE, BUENOS_AIRES, NOW

SCOPE = OwnerScope(ATHLETE)
DAY = date(2026, 10, 24)  # NOW in Buenos Aires
ALL = frozenset(RecoveryMetric)
COUNTER = itertools.count(1000)
RAW = UUID(int=7)


def reading(metric: RecoveryMetric, day: date, value: float) -> RecoveryObservation:
    return RecoveryObservation(
        observation_id=observation_id(ATHLETE, day, metric, RAW, NOW),
        athlete_id=ATHLETE,
        day=day,
        metric=metric,
        value=value,
        provider="garmin",
        raw_source_id=RAW,
        source_path="value",
        received_at=NOW,
    )


def series(
    metric: RecoveryMetric, base: float, today: float, *, spread: float = 1.0, days: int = 28
) -> dict[date, RecoveryObservation]:
    """A steady baseline alternating base +/- spread over the days before DAY, then today."""
    found = {
        DAY - timedelta(days=offset): reading(
            metric, DAY - timedelta(days=offset), base + (spread if offset % 2 else -spread)
        )
        for offset in range(1, days + 1)
    }
    found[DAY] = reading(metric, DAY, today)
    return found


def normal_readings() -> dict[RecoveryMetric, dict[date, RecoveryObservation]]:
    return {
        RecoveryMetric.RESTING_HR: series(RecoveryMetric.RESTING_HR, 50, 50),
        RecoveryMetric.HRV_NIGHTLY: series(RecoveryMetric.HRV_NIGHTLY, 60, 60, spread=3),
        RecoveryMetric.SLEEP_DURATION: series(
            RecoveryMetric.SLEEP_DURATION, 26_000, 26_000, spread=600
        ),
        RecoveryMetric.BODY_BATTERY_CHARGED: series(RecoveryMetric.BODY_BATTERY_CHARGED, 60, 60),
    }


def checkin(day: date = DAY, **answers: Any) -> DailyCheckIn:
    fields: dict[str, Any] = {
        "sleep_quality": 4,
        "fatigue": 2,
        "soreness": 2,
        "stress": 2,
        "motivation": 4,
        "pain_flag": False,
    }
    fields.update(answers)
    return DailyCheckIn(
        checkin_id=UUID(int=next(COUNTER)),
        athlete_id=ATHLETE,
        recorded_at=NOW,
        training_day=day,
        **fields,
    )


def run(
    readings: Mapping[RecoveryMetric, Mapping[date, RecoveryObservation]] | None = None,
    checkins: Mapping[date, DailyCheckIn] | None = None,
    *,
    consented: frozenset[RecoveryMetric] = ALL,
    safety_hold: bool = False,
) -> RecoveryAssessment:
    return assess(
        assessment_id=UUID(int=next(COUNTER)),
        athlete_id=ATHLETE,
        day=DAY,
        readings=normal_readings() if readings is None else readings,
        consented=consented,
        checkins={DAY: checkin()} if checkins is None else checkins,
        safety_hold=safety_hold,
        assessed_at=NOW,
    )


def signal(found: RecoveryAssessment, name: str) -> Signal:
    return next(item for item in found.signals if item.name == name)


def made(found: RecoveryAssessment) -> RecoveryPrescription | None:
    return prescribe(found, prescription_id=UUID(int=next(COUNTER)), created_at=NOW)


# --------------------------------------------------------------------------- acceptance


@pytest.mark.req("FR-REC-003")
@pytest.mark.acceptance("AT-REC-001")
def test_one_low_reading_is_an_observation_not_a_major_decision() -> None:
    readings = normal_readings()
    readings[RecoveryMetric.HRV_NIGHTLY] = series(RecoveryMetric.HRV_NIGHTLY, 60, 40, spread=3)
    found = run(readings)
    assert found.status is RecoveryStatus.MONITOR
    assert found.reasons == (Reason.ISOLATED_SIGNAL, Reason.WEARABLE_SUBJECTIVE_CONFLICT)
    assert not found.major
    assert made(found) is None  # no REDUCE, REST or RECOVERY_BLOCK
    hrv = signal(found, "HRV_NIGHTLY")
    assert (hrv.state, hrv.value, hrv.baseline_mean, hrv.baseline_readings) == (
        SignalState.DEVIATING,
        40.0,
        60.0,
        28,
    )
    assert hrv.baseline_sd == 3.0  # reported with its spread and the assessment's confidence
    assert found.confidence is Confidence.HIGH
    assert hrv.evidence == (EvidenceRef(EvidenceKind.DAILY_RECOVERY, hrv_id(readings)),)


def hrv_id(readings: Mapping[RecoveryMetric, Mapping[date, RecoveryObservation]]) -> UUID:
    return readings[RecoveryMetric.HRV_NIGHTLY][DAY].observation_id


@pytest.mark.req("FR-REC-002", "FR-REC-004")
@pytest.mark.acceptance("AT-REC-002")
def test_wearables_never_override_the_athlete() -> None:
    readings = normal_readings()
    readings[RecoveryMetric.BODY_BATTERY_CHARGED] = series(
        RecoveryMetric.BODY_BATTERY_CHARGED, 60, 95
    )
    found = run(readings, {DAY: checkin(fatigue=5, soreness=4)})
    assert signal(found, "BODY_BATTERY_CHARGED").state is SignalState.FAVOURABLE
    assert found.status is RecoveryStatus.REDUCE  # the more conservative signal
    assert found.reasons == (Reason.TWO_GROUPS, Reason.WEARABLE_SUBJECTIVE_CONFLICT)
    assert found.conflict  # shown to the athlete as an observation
    prescription = made(found)
    assert prescription is not None
    assert (prescription.type, prescription.duration_days) == (PrescriptionType.EASY_SESSION, 2)
    # and the reverse: devices deviate, the athlete feels fine
    readings = normal_readings()
    readings[RecoveryMetric.RESTING_HR] = series(RecoveryMetric.RESTING_HR, 50, 58)
    readings[RecoveryMetric.SLEEP_DURATION] = series(
        RecoveryMetric.SLEEP_DURATION, 26_000, 18_000, spread=600
    )
    reverse = run(readings)
    assert (reverse.status, reverse.conflict) == (RecoveryStatus.REDUCE, True)


@pytest.mark.req("FR-REC-005", "PRIV-CON-002")
@pytest.mark.acceptance("AT-CON-001")
def test_a_metric_without_consent_is_not_used_and_listed_as_missing_data() -> None:
    readings = normal_readings()
    readings[RecoveryMetric.HRV_NIGHTLY] = series(RecoveryMetric.HRV_NIGHTLY, 60, 20, spread=3)
    found = run(readings, consented=frozenset({RecoveryMetric.RESTING_HR}))
    hrv = signal(found, "HRV_NIGHTLY")
    assert (hrv.state, hrv.value, hrv.evidence) == (SignalState.NO_CONSENT, None, ())
    assert found.status is RecoveryStatus.NORMAL  # it continues with the remaining signals
    assert UncertaintyReason.MISSING_DATA in found.uncertainty_reasons


# --------------------------------------------------------------------------- rules


@pytest.mark.req("FR-REC-003")
def test_signals_of_one_physiology_count_once() -> None:
    readings = normal_readings()
    readings[RecoveryMetric.RESTING_HR] = series(RecoveryMetric.RESTING_HR, 50, 58)
    readings[RecoveryMetric.HRV_NIGHTLY] = series(RecoveryMetric.HRV_NIGHTLY, 60, 40, spread=3)
    readings[RecoveryMetric.BODY_BATTERY_CHARGED] = series(
        RecoveryMetric.BODY_BATTERY_CHARGED, 60, 30
    )
    found = run(readings)  # three autonomic readings: one group
    assert (found.status, found.reasons[0]) == (RecoveryStatus.MONITOR, Reason.ISOLATED_SIGNAL)


@pytest.mark.req("FR-REC-003", "FR-REC-004")
def test_three_deviating_groups_call_for_rest_and_block_intensity() -> None:
    readings = normal_readings()
    readings[RecoveryMetric.RESTING_HR] = series(RecoveryMetric.RESTING_HR, 50, 58)
    found = run(readings, {DAY: checkin(fatigue=4, sleep_quality=2)})
    assert (found.status, found.reasons[0]) == (RecoveryStatus.REST, Reason.THREE_GROUPS)
    prescription = made(found)
    assert prescription is not None
    assert (prescription.type, prescription.reevaluate_after) == (
        PrescriptionType.REST,
        DAY + timedelta(days=1),
    )
    assert prescription.blocks_intensity
    assert prescription.evidence[0] == EvidenceRef(
        EvidenceKind.RECOVERY_ASSESSMENT, found.assessment_id
    )
    assert len(prescription.evidence) == 3  # the assessment, the reading and the check-in


@pytest.mark.req("FR-REC-003")
def test_one_signal_deviating_three_days_in_a_row_is_a_major_decision() -> None:
    readings = normal_readings()
    sleep = series(RecoveryMetric.SLEEP_DURATION, 26_000, 18_000, spread=600)
    for offset in (1, 2):
        earlier = DAY - timedelta(days=offset)
        sleep[earlier] = reading(RecoveryMetric.SLEEP_DURATION, earlier, 18_000)
    readings[RecoveryMetric.SLEEP_DURATION] = sleep
    found = run(readings)
    assert signal(found, "SLEEP_DURATION").consecutive_days == 3
    assert (found.status, found.reasons[0]) == (RecoveryStatus.REDUCE, Reason.PERSISTENT_SIGNAL)
    prescription = made(found)
    assert prescription is not None
    assert (prescription.type, prescription.blocks_intensity) == (
        PrescriptionType.REDUCED_VOLUME,
        False,
    )
    del sleep[DAY - timedelta(days=1)]  # a day without a reading breaks the run
    assert run(readings).status is RecoveryStatus.MONITOR


@pytest.mark.req("FR-REC-003", "SAFE-MED-003")
def test_pain_and_red_flag_rules_apply_alone() -> None:
    severe = CheckInPain(intensity=7, location="rodilla", onset=PainOnset.GRADUAL)
    found = run(checkins={DAY: checkin(pain_flag=True, pain=severe)})
    assert (found.status, found.reasons[0], found.pain) == (
        RecoveryStatus.REDUCE,
        Reason.SEVERE_PAIN,
        7,
    )
    prescription = made(found)
    assert prescription is not None
    assert (prescription.type, prescription.blocks_intensity) == (
        PrescriptionType.ACTIVE_RECOVERY,
        True,
    )
    held = run(safety_hold=True)
    assert (held.status, held.reasons[0]) == (RecoveryStatus.REST, Reason.SAFETY_HOLD)
    assert made(held) is None  # the hold's own rules apply


@pytest.mark.req("FR-REC-003", "SAFE-MED-001")
def test_pain_lasting_more_than_seven_days_or_growing_is_reported() -> None:
    checkins = {DAY - timedelta(days=9): checkin(DAY - timedelta(days=9))}  # no pain: before
    for offset in range(8, -1, -1):
        day = DAY - timedelta(days=offset)
        if offset == 4:
            checkins[day] = checkin(day, pain_flag=None)  # not answered: the episode goes on
            continue
        intensity = 2 if offset > 3 else 4
        checkins[day] = checkin(day, pain_flag=True, pain=CheckInPain(intensity=intensity))
    found = run(checkins=checkins)
    assert found.reasons[-3:] == (
        Reason.PAIN_REPORTED,
        Reason.PAIN_PERSISTENT,
        Reason.PAIN_WORSENING,
    )
    assert signal(found, "pain").consecutive_days == 3
    short = {day: item for day, item in checkins.items() if day >= DAY - timedelta(days=5)}
    assert Reason.PAIN_PERSISTENT not in run(checkins=short).reasons


@pytest.mark.req("FR-REC-003")
def test_a_steady_baseline_does_not_turn_noise_into_a_deviation() -> None:
    readings = normal_readings()
    readings[RecoveryMetric.RESTING_HR] = series(RecoveryMetric.RESTING_HR, 50, 51, spread=0)
    assert signal(run(readings), "RESTING_HR").state is SignalState.NORMAL  # floor 2 bpm
    readings[RecoveryMetric.RESTING_HR] = series(RecoveryMetric.RESTING_HR, 50, 53, spread=0)
    assert signal(run(readings), "RESTING_HR").state is SignalState.DEVIATING
    readings[RecoveryMetric.RESTING_HR] = series(RecoveryMetric.RESTING_HR, 50, 46, spread=0)
    assert signal(run(readings), "RESTING_HR").state is SignalState.FAVOURABLE


@pytest.mark.req("FR-REC-003")
def test_without_history_or_signals_there_is_no_decision() -> None:
    short = {RecoveryMetric.RESTING_HR: series(RecoveryMetric.RESTING_HR, 50, 70, days=6)}
    found = run(short, {})
    assert signal(found, "RESTING_HR").state is SignalState.NO_BASELINE
    assert found.status is RecoveryStatus.INSUFFICIENT_DATA
    assert found.uncertainty_reasons == {
        UncertaintyReason.INSUFFICIENT_HISTORY,
        UncertaintyReason.MISSING_DATA,
    }
    assert found.confidence is Confidence.LOW


@pytest.mark.req("FR-REC-004")
def test_a_reassessment_of_the_day_withdraws_its_prescription() -> None:
    first = run(checkins={DAY: checkin(fatigue=5, soreness=5)})
    prescription = made(first)
    assert prescription is not None
    in_force = in_force_assessments([first])
    assert prescriptions_in_force([prescription], in_force, DAY) == (prescription,)
    assert prescriptions_in_force([prescription], in_force, DAY + timedelta(days=2)) == ()
    later = run(checkins={DAY: checkin()})
    later = RecoveryAssessment(**{**_fields(later), "assessed_at": NOW + timedelta(hours=1)})
    assert prescriptions_in_force([prescription], in_force_assessments([first, later]), DAY) == ()


def _fields(found: RecoveryAssessment) -> dict[str, Any]:
    return {name: getattr(found, name) for name in RecoveryAssessment.__dataclass_fields__}


@pytest.mark.req("FR-REC-004")
@pytest.mark.parametrize(
    "changes",
    [
        {"evidence": ()},
        {"duration_days": 0},
        {"duration_days": 15, "reevaluate_after": DAY + timedelta(days=15)},
        {"reevaluate_after": DAY + timedelta(days=3)},
    ],
)
def test_a_prescription_states_evidence_and_a_consistent_reevaluation(
    changes: dict[str, Any],
) -> None:
    fields: dict[str, Any] = {
        "prescription_id": UUID(int=1),
        "athlete_id": ATHLETE,
        "assessment_id": UUID(int=2),
        "day": DAY,
        "type": PrescriptionType.REST,
        "reason": Reason.THREE_GROUPS,
        "evidence": (EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, UUID(int=2)),),
        "duration_days": 1,
        "reevaluate_after": DAY + timedelta(days=1),
        "created_at": NOW,
    }
    RecoveryPrescription(**fields)
    with pytest.raises(DomainValidationError):
        RecoveryPrescription(**{**fields, **changes})


# --------------------------------------------------------------------------- service


class AssessmentWorld:
    def __init__(self) -> None:
        self.clock = MutableClock()
        counter = itertools.count(1)
        self.ids = lambda: UUID(int=next(counter))
        self.events = RecordingEventSink()
        audit = InMemoryAuditLog()
        self.conditions = CoachingConditions(
            InMemoryRepository[CoachingState](), audit=audit, events=self.events, clock=self.clock
        )
        ledgers = InMemoryRepository[ConsentLedger]()
        self.consents = ConsentService(
            ledgers,
            policy_version="1.0",
            audit=audit,
            events=self.events,
            clock=self.clock,
            ids=self.ids,
        )
        gate = ConsentGate(ledgers, policy_version="1.0")
        self.observations = InMemoryRepository[RecoveryObservation]()
        self.checkins = InMemoryRepository[DailyCheckIn]()
        self.recovery = RecoveryService(
            self.checkins,
            conditions=self.conditions,
            screen=SecretScreen(self.events),
            timezone_of=lambda _scope: BUENOS_AIRES,
            events=self.events,
            clock=self.clock,
            ids=self.ids,
            observations=self.observations,
            consents=gate,
        )
        self.assessments = InMemoryRepository[RecoveryAssessment]()
        self.prescriptions = InMemoryRepository[RecoveryPrescription]()
        self.service = RecoveryAssessmentService(
            self.recovery,
            self.assessments,
            self.prescriptions,
            consents=gate,
            conditions=self.conditions,
            events=self.events,
            clock=self.clock,
            ids=self.ids,
        )

    def block(self) -> bool:
        return Condition.RECOVERY_BLOCK in self.conditions.current(SCOPE).conditions


@pytest.mark.req("FR-REC-003", "FR-REC-004", "SEC-AUD-001")
def test_the_service_stores_changes_only_and_follows_the_recovery_block() -> None:
    world = AssessmentWorld()
    world.consents.grant(SCOPE, ConsentType.RECOVERY, purpose="Assess my recovery.")
    for item in series(RecoveryMetric.RESTING_HR, 50, 58).values():
        world.observations.add(SCOPE, item)
    world.checkins.add(SCOPE, checkin(fatigue=4, sleep_quality=2))
    result = world.service.assess(SCOPE)
    assert (result.assessment.status, result.stored) == (RecoveryStatus.REST, True)
    assert [item.type for item in result.prescriptions] == [PrescriptionType.REST]
    assert result.recovery_block
    assert world.block()
    assert (EventName.RECOVERY_ASSESSED, {"status": "REST", "confidence": "HIGH"}) in (
        world.events.events
    )
    assert (EventName.RECOVERY_PRESCRIBED, {"type": "REST"}) in world.events.events
    again = world.service.assess(SCOPE)  # unchanged: nothing new is stored
    assert (again.stored, len(world.assessments.list_all(SCOPE))) == (False, 1)
    assert len(world.prescriptions.list_all(SCOPE)) == 1
    world.clock.advance(days=1)  # the re-evaluation day: no new reading, no check-in
    later = world.service.assess(SCOPE)
    assert later.assessment.status is RecoveryStatus.INSUFFICIENT_DATA
    assert (later.prescriptions, later.recovery_block, world.block()) == ((), False, False)
    assert world.service.prescriptions(SCOPE, DAY) == tuple(world.prescriptions.list_all(SCOPE))
    history = world.service.history(SCOPE, until=DAY + timedelta(days=1), days=7)
    assert [item.day for item in history] == [DAY, DAY + timedelta(days=1)]
