"""What planning needs from the athlete (onboarding spec 2 and 4, profile spec 3 to 5, goals spec
1 and 2; FR-PRF-004 to FR-PRF-006, FR-GOL-001, FR-GOL-002, FR-ONB-003 to FR-ONB-006).

Every change is the athlete's declaration, recorded with its time. Free text (a constraint's
description, availability restrictions, an event's name, place or conditions) is untrusted and
screened for disclosed secrets before it is stored (SEC-SEC-005). A constraint names body
regions only by the exact names of the strength spec (section 2), never by interpretation.
At most one ACTIVE priority-A goal exists (A-19). Readiness is the Definition of Ready; asking
for it also brings the coaching lifecycle forward where the onboarding transitions allow.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import partial
from uuid import UUID

from coach.application.coaching import CoachingLifecycle
from coach.application.consent import ConsentService
from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, IdFactory, OwnedRepository
from coach.application.screening import SecretScreen
from coach.domain.athlete.experience import (
    CyclingExperience,
    RunningExperience,
    SportExperience,
    StrengthExperience,
    SwimmingExperience,
    TennisExperience,
)
from coach.domain.athlete.health import (
    ConstraintSource,
    HealthConstraint,
    HealthDeclaration,
    Restriction,
)
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.athlete.resources import Resources
from coach.domain.availability.availability import (
    Availability,
    AvailabilityException,
    DayDefaults,
    WeeklyAvailability,
)
from coach.domain.coaching.state import LifecycleState, Trigger
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import FieldValue
from coach.domain.common.sport import Sport
from coach.domain.consent.ledger import ConsentType
from coach.domain.goals.feasibility import (
    CurrentState,
    FeasibilityAssessment,
    assess_feasibility,
    current_state,
)
from coach.domain.goals.goal import (
    Goal,
    GoalBaseline,
    GoalPriority,
    GoalStatus,
    SportEvent,
    ensure_single_active_primary,
)
from coach.domain.onboarding.planner import OnboardingSnapshot
from coach.domain.onboarding.readiness import (
    HistoryFacts,
    HistorySufficiency,
    Readiness,
    ReadinessStatus,
    assess_readiness,
    history_sufficiency,
)
from coach.domain.strength.regions import region_named


@dataclass(frozen=True, slots=True, kw_only=True)
class ConstraintAnswers:
    description: str
    regions: tuple[str, ...] = ()  # body region names (strength spec 2)
    sports: frozenset[Sport] = frozenset()
    restrictions: tuple[Restriction, ...] = ()
    valid_until: date | None = None
    professional_advice: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    readiness: Readiness
    sufficiency: HistorySufficiency | None  # None without a primary goal
    lifecycle: LifecycleState


class AthleteInputsService:
    def __init__(
        self,
        profiles: OwnedRepository[AthleteProfile],
        availability: OwnedRepository[Availability],
        goals: OwnedRepository[Goal],
        sport_events: OwnedRepository[SportEvent],
        *,
        consents: ConsentService,
        lifecycle: CoachingLifecycle,
        screen: SecretScreen,
        history: Callable[[OwnerScope, Sport | None], HistoryFacts],
        today: Callable[[OwnerScope], date],
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._profiles = profiles
        self._availability = availability
        self._goals = goals
        self._events = sport_events
        self._consents = consents
        self._lifecycle = lifecycle
        self._screen = screen
        self._history = history
        self._today = today
        self._clock = clock
        self._ids = ids

    # ------------------------------------------------------------------ profile

    def profile(self, scope: OwnerScope) -> AthleteProfile:
        try:
            return self._profiles.get(scope, scope.athlete_id)
        except NotFoundError:
            return AthleteProfile.new(scope.athlete_id)

    def update_profile(self, scope: OwnerScope, update: ProfileUpdate) -> AthleteProfile:
        try:
            stored = self._profiles.get(scope, scope.athlete_id)
        except NotFoundError:
            return self._profiles.add(scope, AthleteProfile.new(scope.athlete_id).apply(update))
        return self._profiles.update(scope, stored.apply(update), expected_version=stored.version)

    def _text(self, text: str | None, notices: list[str]) -> str | None:
        if text is None:
            return None
        screened = self._screen.screen(text, source="profile")
        if screened.notice is not None:
            notices.append(screened.notice)
        return screened.text

    def set_display_name(self, scope: OwnerScope, name: str) -> tuple[AthleteProfile, str | None]:
        notices: list[str] = []
        cleaned = self._text(name, notices)
        update = ProfileUpdate(display_name=FieldValue.declared(cleaned or "", at=self._clock()))
        return self.update_profile(scope, update), next(iter(notices), None)

    def _constraint(self, answers: ConstraintAnswers, notices: list[str]) -> HealthConstraint:
        regions = []
        for name in answers.regions:
            region = region_named(name)
            if region is None:
                raise DomainValidationError(f"{name!r} is not a body region name (strength spec 2)")
            regions.append(region.value)
        return HealthConstraint(
            constraint_id=self._ids(),
            description=self._text(answers.description, notices) or "",
            affected_regions=tuple(dict.fromkeys(regions)),
            affected_sports=answers.sports,
            restrictions=answers.restrictions,
            declared_at=self._clock(),
            valid_until=answers.valid_until,
            source=ConstraintSource.PROFESSIONAL_ADVICE_DECLARED
            if answers.professional_advice
            else ConstraintSource.ATHLETE,
        )

    def declare_health(
        self, scope: OwnerScope, constraints: Sequence[ConstraintAnswers]
    ) -> tuple[AthleteProfile, str | None]:
        """Record the whole declaration now; no constraints is an explicit "none"."""
        notices: list[str] = []
        made = tuple(self._constraint(answers, notices) for answers in constraints)
        return self._record_health(scope, made), next(iter(notices), None)

    def add_constraint(
        self, scope: OwnerScope, answers: ConstraintAnswers
    ) -> tuple[AthleteProfile, str | None]:
        notices: list[str] = []
        added = self._constraint(answers, notices)
        profile = self._record_health(scope, (*self.constraints(scope), added))
        return profile, next(iter(notices), None)

    def remove_constraint(self, scope: OwnerScope, constraint_id: UUID) -> AthleteProfile:
        current = self.constraints(scope)
        kept = tuple(item for item in current if item.constraint_id != constraint_id)
        if len(kept) == len(current):
            raise NotFoundError
        return self._record_health(scope, kept)

    def confirm_health(self, scope: OwnerScope) -> AthleteProfile:
        """Re-confirm the declaration as it is (its freshness is 30 days)."""
        if self.profile(scope).health is None:
            raise DomainValidationError("declare your health constraints first (or none)")
        return self._record_health(scope, self.constraints(scope))

    def constraints(self, scope: OwnerScope) -> tuple[HealthConstraint, ...]:
        health = self.profile(scope).health
        return () if health is None or health.value is None else health.value.constraints

    def _record_health(
        self, scope: OwnerScope, constraints: tuple[HealthConstraint, ...]
    ) -> AthleteProfile:
        now = self._clock()
        declaration = HealthDeclaration(declared_at=now, constraints=constraints)
        return self.update_profile(
            scope, ProfileUpdate(health=FieldValue.declared(declaration, at=now))
        )

    def set_resources(self, scope: OwnerScope, resources: Resources) -> AthleteProfile:
        now = self._clock()
        return self.update_profile(
            scope, ProfileUpdate(resources=FieldValue.declared(resources, at=now))
        )

    def set_experience(self, scope: OwnerScope, experience: SportExperience) -> AthleteProfile:
        declared = partial(FieldValue.declared, at=self._clock())
        match experience:
            case RunningExperience():
                update = ProfileUpdate(running_experience=declared(experience))
            case CyclingExperience():
                update = ProfileUpdate(cycling_experience=declared(experience))
            case SwimmingExperience():
                update = ProfileUpdate(swimming_experience=declared(experience))
            case StrengthExperience():
                update = ProfileUpdate(strength_experience=declared(experience))
            case TennisExperience():
                update = ProfileUpdate(tennis_experience=declared(experience))
        return self.update_profile(scope, update)

    def record_unknown_experience(self, scope: OwnerScope, sport: Sport) -> AthleteProfile:
        """The athlete does not know: UNKNOWN, not asked again within its window (FR-ONB-002)."""
        now = self._clock()
        match sport:
            case Sport.RUNNING:
                update = ProfileUpdate(running_experience=FieldValue.unknown(at=now))
            case Sport.CYCLING:
                update = ProfileUpdate(cycling_experience=FieldValue.unknown(at=now))
            case Sport.SWIMMING:
                update = ProfileUpdate(swimming_experience=FieldValue.unknown(at=now))
            case Sport.STRENGTH:
                update = ProfileUpdate(strength_experience=FieldValue.unknown(at=now))
            case Sport.TENNIS:
                update = ProfileUpdate(tennis_experience=FieldValue.unknown(at=now))
            case _:
                raise DomainValidationError(f"no experience is recorded for {sport.value}")
        return self.update_profile(scope, update)

    # ------------------------------------------------------------------ availability

    def availability(self, scope: OwnerScope) -> Availability | None:
        try:
            return self._availability.get(scope, scope.athlete_id)
        except NotFoundError:
            return None

    def _save_availability(
        self, scope: OwnerScope, change: Callable[[Availability], Availability]
    ) -> Availability:
        stored = self.availability(scope)
        if stored is None:
            return self._availability.add(scope, change(Availability.new(scope.athlete_id)))
        return self._availability.update(scope, change(stored), expected_version=stored.version)

    def set_weekly(
        self, scope: OwnerScope, weekly: WeeklyAvailability
    ) -> tuple[Availability, str | None]:
        notices: list[str] = []
        cleaned = replace(weekly, restrictions=self._text(weekly.restrictions, notices))
        saved = self._save_availability(scope, lambda item: replace(item, recurring=cleaned))
        return saved, next(iter(notices), None)

    def set_defaults(self, scope: OwnerScope, defaults: DayDefaults) -> Availability:
        return self._save_availability(scope, lambda item: replace(item, defaults=defaults))

    def set_exception(
        self, scope: OwnerScope, exception: AvailabilityException
    ) -> tuple[Availability, str | None]:
        notices: list[str] = []
        cleaned = replace(exception, reason=self._text(exception.reason, notices))
        saved = self._save_availability(scope, lambda item: item.with_exception(cleaned))
        return saved, next(iter(notices), None)

    def remove_exception(self, scope: OwnerScope, day: date) -> Availability:
        return self._save_availability(scope, lambda item: item.without_exception(day))

    # ------------------------------------------------------------------ goals and events

    def add_event(
        self,
        scope: OwnerScope,
        *,
        name: str,
        sport: Sport,
        event_date: date,
        priority: GoalPriority,
        distance_meters: float | None = None,
        location: str | None = None,
        expected_conditions: str | None = None,
    ) -> tuple[SportEvent, str | None]:
        notices: list[str] = []
        event = SportEvent(
            event_id=self._ids(),
            athlete_id=scope.athlete_id,
            name=self._text(name, notices) or "",
            sport=sport,
            event_date=event_date,
            priority=priority,
            distance_meters=distance_meters,
            location=self._text(location, notices),
            expected_conditions=self._text(expected_conditions, notices),
        )
        return self._events.add(scope, event), next(iter(notices), None)

    def events(self, scope: OwnerScope) -> list[SportEvent]:
        return sorted(self._events.list_all(scope), key=lambda item: item.event_date)

    def add_goal(
        self,
        scope: OwnerScope,
        *,
        sport: Sport,
        target_date: date,
        priority: GoalPriority,
        distance_meters: float | None = None,
        target_time_seconds: float | None = None,
        event_id: UUID | None = None,
    ) -> Goal:
        if event_id is not None:
            self._events.get(scope, event_id)  # owner-scoped: a foreign event is not found
        today = self._today(scope)
        current = None
        if distance_meters is not None and sport is Sport.RUNNING:
            current = current_state(self.profile(scope), distance_meters, today=today)
        goal = Goal.new(
            goal_id=self._ids(),
            athlete_id=scope.athlete_id,
            sport=sport,
            target_date=target_date,
            priority=priority,
            created_at=self._clock(),
            distance_meters=distance_meters,
            target_time_seconds=target_time_seconds,
            event_id=event_id,
            baseline=_baseline(current, self._clock()),
        )
        if target_date < today:
            raise DomainValidationError("a goal's target date is today or later")
        return self._goals.add(scope, goal)

    def goals(self, scope: OwnerScope) -> list[Goal]:
        return sorted(
            self._goals.list_all(scope), key=lambda item: (item.target_date, item.priority)
        )

    def change_goal(self, scope: OwnerScope, goal_id: UUID, status: GoalStatus) -> Goal:
        stored = self._goals.get(scope, goal_id)
        changed = stored.transition(status)
        ensure_single_active_primary(self._goals.list_all(scope), changed)
        return self._goals.update(scope, changed, expected_version=stored.version)

    def primary_goal(self, scope: OwnerScope) -> Goal | None:
        return self._snapshot(scope).primary_goal

    def feasibility(self, scope: OwnerScope, goal_id: UUID | None = None) -> FeasibilityAssessment:
        """Feasibility rules 1.0.0 for a goal (the primary one by default), computed now."""
        goal = self.primary_goal(scope) if goal_id is None else self._goals.get(scope, goal_id)
        if goal is None:
            raise NotFoundError
        return assess_feasibility(
            goal, self.profile(scope), self.availability(scope), today=self._today(scope)
        )

    # ------------------------------------------------------------------ readiness

    def _snapshot(self, scope: OwnerScope) -> OnboardingSnapshot:
        return OnboardingSnapshot(
            profile=self.profile(scope),
            consents=self._consents.ledger(scope),
            availability=self.availability(scope),
            goals=tuple(self._goals.list_all(scope)),
        )

    def readiness(self, scope: OwnerScope) -> ReadinessReport:
        """The Definition of Ready now, bringing the onboarding lifecycle forward."""
        snapshot = self._snapshot(scope)
        goal = snapshot.primary_goal
        facts = self._history(scope, None if goal is None else goal.sport)
        readiness = assess_readiness(snapshot, facts, now=self._clock())
        sufficiency = None
        if goal is not None:
            sufficiency = history_sufficiency(
                snapshot.profile,
                goal.sport,
                recent_activities=facts.goal_sport_recent,
                today=self._today(scope),
            )
        if snapshot.consents.has_decision(ConsentType.AI_PROCESSING):
            self._lifecycle.advance(scope, Trigger.CONSENT_DECISIONS_RECORDED)
        if readiness.status is not ReadinessStatus.NOT_READY and sufficiency is not None:
            if sufficiency.sufficient:
                self._lifecycle.advance(scope, Trigger.REQUIRED_INPUTS_WITH_HISTORY)
                self._lifecycle.advance(scope, Trigger.BASELINE_COMPLETED)
            else:
                self._lifecycle.advance(scope, Trigger.REQUIRED_INPUTS_WITHOUT_HISTORY)
        return ReadinessReport(readiness, sufficiency, self._lifecycle.current(scope).lifecycle)


def _baseline(current: CurrentState | None, now: datetime) -> GoalBaseline:
    """The immutable snapshot of current-level evidence at goal creation (goals spec 3)."""
    if current is None:
        return GoalBaseline(captured_at=now, summary="no comparable evidence (feasibility 1.0.0)")
    total = round(current.seconds)
    summary = (
        f"predicted {total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d} from a "
        f"{current.source_distance_m / 1000:g} km result of {current.source_date} "
        f"(Riegel 1981), confidence {current.confidence.value}"
    )
    return GoalBaseline(captured_at=now, evidence=(current.evidence,), summary=summary)
