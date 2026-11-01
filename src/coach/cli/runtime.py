"""Composition root: wires infrastructure adapters into application services."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from coach.application.activities import ActivityService, ActivityStores
from coach.application.activity_metrics import ActivityMetricsEngine
from coach.application.adaptation import AdaptationService
from coach.application.analytics import AnalyticsService
from coach.application.athlete_inputs import AthleteInputsService
from coach.application.authentication import (
    AuthenticationService,
    FailedAuthLockout,
    LocalTokenAuthenticator,
)
from coach.application.authorization import AuthorizationService
from coach.application.coaching import CoachingConditions, CoachingLifecycle
from coach.application.consent import ConsentGate, ConsentRequiredError, ConsentService
from coach.application.contracts import (
    ADAPTATION_DECISION_CODEC,
    AVAILABILITY_CODEC,
    CHECKIN_CODEC,
    COACHING_STATE_CODEC,
    CONSENT_LEDGER_CODEC,
    CREDENTIAL_RECORD_CODEC,
    DATA_QUALITY_ISSUE_CODEC,
    EXECUTION_LINK_CODEC,
    GOAL_CODEC,
    INSIGHT_CODEC,
    INTEGRATION_STATUS_CODEC,
    LAPS_CODEC,
    LENGTHS_CODEC,
    PLAN_HEADER_CODEC,
    PLAN_VERSION_CODEC,
    PROFILE_CODEC,
    RECOVERY_ASSESSMENT_CODEC,
    RECOVERY_OBSERVATION_CODEC,
    RECOVERY_PRESCRIPTION_CODEC,
    SETS_CODEC,
    SPORT_EVENT_CODEC,
    STRENGTH_SESSION_CODEC,
    TIME_SERIES_CODEC,
    DocumentCodec,
)
from coach.application.credentials import CredentialKind, CredentialService
from coach.application.derived_metrics import DerivedMetricService
from coach.application.errors import NotFoundError, TimezoneRequiredError
from coach.application.executions import ExecutionService
from coach.application.ids import new_id
from coach.application.ingestion import IngestionService
from coach.application.insights import InsightService
from coach.application.ownership import OwnerScope
from coach.application.plan_generation import PlanGenerationService, weekly_minutes_from
from coach.application.planning_context import PlanningContextSource
from coach.application.plans import PlanService
from coach.application.policy_gate import PolicyGate
from coach.application.ports import Clock, CredentialProvider, EventSink
from coach.application.providers import FitnessProvider
from coach.application.recovery import RecoveryService
from coach.application.recovery_assessment import RecoveryAssessmentService
from coach.application.recovery_sync import RecoverySyncService
from coach.application.reports import ReportService
from coach.application.retry import Retrier, RetryPolicy
from coach.application.screening import SecretScreen
from coach.application.strength import StrengthService, session_reader
from coach.application.sync import SyncService
from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import StreamKind
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.athlete.settings import AnalysisSettings
from coach.domain.coaching.state import Condition
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange, parse_timezone
from coach.domain.consent.ledger import ConsentType
from coach.domain.goals.feasibility import FeasibilityAssessment
from coach.domain.integration.status import IntegrationStatus
from coach.domain.onboarding.readiness import (
    BASELINE_DAYS,
    HISTORY_WEEKS,
    RECOVERY_DAYS,
    HistoryFacts,
)
from coach.infrastructure.blobs import BlobIntegrityError, FileBlobStore
from coach.infrastructure.datadir import DataLayout
from coach.infrastructure.events import LoggingEventSink
from coach.infrastructure.quarantine import FileQuarantine, UnavailableMalwareScanner
from coach.infrastructure.sqlite.activities import (
    SqliteActivityRepository,
    SqliteObservationRepository,
    SqliteOverrideRepository,
)
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from coach.infrastructure.sqlite.audit import SqliteAuditLog
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.derived_values import SqliteDerivedValueRepository
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory
from coach.infrastructure.sqlite.ingestion import SqliteIngestionRecordRepository
from coach.infrastructure.sqlite.raw_sources import SqliteRawSourceRepository
from coach.integrations.files.formats import LocalFileFormats
from coach.integrations.files.sandbox import ParserSandbox
from coach.integrations.garmin.normalise import GarminActivityRecords
from coach.integrations.garmin.provider import (
    SERVERS,
    GarminPersonalProvider,
    credential_key,
)
from coach.integrations.garmin.recovery import GarminDailyRecords
from coach.integrations.garmin.throttle import RequestBudget
from coach.integrations.garmin.token import TokenBroker
from coach.integrations.garmin.transport import HttpTransport, UrllibTransport
from coach.metrics.catalog import CATALOG

PRIVACY_POLICY_VERSION: Final = "1.0"


def analysis_settings(
    profiles: SqliteAggregateRepository[AthleteProfile], scope: OwnerScope
) -> AnalysisSettings:
    """Zones, reliable power sensors and FTP from the profile (metrics spec 10); none without
    a profile."""
    try:
        return profiles.get(scope, scope.athlete_id).analysis_settings
    except NotFoundError:
        return AnalysisSettings()


def athlete_timezone(
    profiles: SqliteAggregateRepository[AthleteProfile], scope: OwnerScope
) -> ZoneInfo:
    """The profile timezone (activities spec section 2); never guessed."""
    try:
        profile = profiles.get(scope, scope.athlete_id)
    except NotFoundError:
        raise TimezoneRequiredError from None
    if profile.timezone is None or profile.timezone.value is None:
        raise TimezoneRequiredError
    return parse_timezone(profile.timezone.value)


@dataclass(frozen=True, slots=True)
class Runtime:
    layout: DataLayout
    database: SqliteDatabase
    provider: CredentialProvider
    identities: SqliteIdentityDirectory
    audit: SqliteAuditLog
    credentials: CredentialService
    consents: ConsentService
    authentication: AuthenticationService
    authorization: AuthorizationService
    profiles: SqliteAggregateRepository[AthleteProfile]
    activity_store: SqliteActivityRepository
    issue_store: SqliteAggregateRepository[DataQualityIssue]
    ingestion_records: SqliteIngestionRecordRepository
    activities: ActivityService
    ingestion: IngestionService
    analytics: AnalyticsService
    strength: StrengthService
    recovery: RecoveryService
    recovery_sync: RecoverySyncService
    recovery_assessment: RecoveryAssessmentService
    insights: InsightService
    inputs: AthleteInputsService
    plans: PlanService
    generation: PlanGenerationService
    executions: ExecutionService
    adaptation: AdaptationService
    reports: ReportService
    screen: SecretScreen
    integrations: SqliteAggregateRepository[IntegrationStatus]
    conditions: CoachingConditions
    sync: SyncService
    events: EventSink
    clock: Clock


def garmin_providers(
    credentials: CredentialService, transport: HttpTransport, clock: Clock
) -> Callable[[OwnerScope], FitnessProvider]:
    """One Garmin adapter per athlete for the run (the activity and the recovery syncs share
    it): its token lives in memory for that run only and one request budget covers the run."""
    budget = RequestBudget()
    adapters: dict[UUID, FitnessProvider] = {}

    def build(scope: OwnerScope) -> FitnessProvider:
        adapter = adapters.get(scope.athlete_id)
        if adapter is None:
            broker = TokenBroker(
                base_url=SERVERS["global"],
                key_source=credential_key(credentials, scope),
                transport=transport,
                clock=clock,
                budget=budget,
            )
            adapter = GarminPersonalProvider(
                broker=broker, transport=transport, budget=budget, clock=clock
            )
            adapters[scope.athlete_id] = adapter
        return adapter

    return build


def goal_feasibility(
    inputs: AthleteInputsService,
) -> Callable[[OwnerScope], FeasibilityAssessment | None]:
    """The primary goal's feasibility now; none without a goal or a timezone."""

    def assess(scope: OwnerScope) -> FeasibilityAssessment | None:
        try:
            return inputs.feasibility(scope)
        except (NotFoundError, TimezoneRequiredError):
            return None

    return assess


def planning_history(
    analytics: AnalyticsService, recovery: RecoveryService, credentials: CredentialService
) -> Callable[[OwnerScope, Sport | None], HistoryFacts]:
    """What readiness counts from the athlete's data (onboarding spec 2 and 3)."""

    def facts(scope: OwnerScope, sport: Sport | None) -> HistoryFacts:
        kind = CredentialKind.GARMIN_PERSONAL_API_KEY
        connected = any(item.kind is kind for item in credentials.status(scope))
        try:
            today = recovery.today(scope)
        except TimezoneRequiredError:  # training days cannot be counted yet
            return HistoryFacts(0, 0, 0, 0, connected)
        try:
            weeks = DateRange(today - timedelta(weeks=HISTORY_WEEKS) + timedelta(days=1), today)
            views = analytics.activities(scope, weeks)
        except ConsentRequiredError:
            views = []
        recent = today - timedelta(days=BASELINE_DAYS - 1)
        of_sport = [view for view in views if sport is not None and view.sport is sport]
        checkins = recovery.checkins(scope, until=today, days=RECOVERY_DAYS)
        days = {item.training_day for item in checkins}
        days |= set(recovery.daily(scope, until=today, days=RECOVERY_DAYS))
        return HistoryFacts(
            activities=len(views),
            goal_sport_activities=len(of_sport),
            goal_sport_recent=sum(1 for view in of_sport if view.training_day >= recent),
            recovery_days=len(days),
            garmin_connected=connected,
        )

    return facts


def build_runtime(
    layout: DataLayout,
    provider: CredentialProvider,
    clock: Clock,
    *,
    transport: HttpTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Runtime:
    database = SqliteDatabase.open(layout.database)
    identities = SqliteIdentityDirectory(database)
    audit = SqliteAuditLog(database)
    events = LoggingEventSink()
    credentials = CredentialService(
        provider,
        SqliteAggregateRepository(database, CREDENTIAL_RECORD_CODEC),
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
    )
    consents = ConsentService(
        SqliteAggregateRepository(database, CONSENT_LEDGER_CODEC),
        policy_version=PRIVACY_POLICY_VERSION,
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
    )
    authentication = AuthenticationService(
        LocalTokenAuthenticator(provider, identities),
        FailedAuthLockout(),
        audit=audit,
        events=events,
        clock=clock,
    )
    authorization = AuthorizationService(identities, audit=audit, events=events, clock=clock)
    profiles = SqliteAggregateRepository(database, PROFILE_CODEC)
    activity_store = SqliteActivityRepository(database)
    issue_store = SqliteAggregateRepository(database, DATA_QUALITY_ISSUE_CODEC)
    raw_sources = SqliteRawSourceRepository(database)
    blobs = FileBlobStore(layout.raw)
    gate = ConsentGate(consents.ledgers, policy_version=PRIVACY_POLICY_VERSION)
    screen = SecretScreen(events)
    derived = DerivedMetricService(
        CATALOG, SqliteDerivedValueRepository(database), clock=clock, ids=new_id
    )

    codecs: dict[StreamKind, DocumentCodec[Any]] = {
        StreamKind.TIME_SERIES: TIME_SERIES_CODEC,
        StreamKind.LAPS: LAPS_CODEC,
        StreamKind.LENGTHS: LENGTHS_CODEC,
        StreamKind.SETS: SETS_CODEC,
    }

    def read_stream(scope: OwnerScope, kind: StreamKind, digest: str) -> object | None:
        """A stored stream; ``None`` when its blob is missing or damaged (analyses UNKNOWN)."""
        codec = codecs.get(kind)
        if codec is None:
            return None
        try:
            content: object = codec.load(json.loads(blobs.get(scope, digest)))
        except (NotFoundError, BlobIntegrityError, ValueError, KeyError, TypeError):
            return None
        return content

    strength_sessions = SqliteAggregateRepository(database, STRENGTH_SESSION_CODEC)
    engine = ActivityMetricsEngine(
        derived,
        athlete=lambda scope: analysis_settings(profiles, scope),
        streams=read_stream,
        strength_sessions=session_reader(strength_sessions),
    )
    overrides = SqliteOverrideRepository(database)
    activities = ActivityService(
        ActivityStores(
            activity_store,
            SqliteObservationRepository(database),
            overrides,
            issue_store,
            raw_sources,
            strength_sessions,
        ),
        consents=gate,
        metrics=derived,
        timezone_of=lambda scope: athlete_timezone(profiles, scope),
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
        screen=screen,
        engine=engine,
        batch=database.batch,
    )
    strength = StrengthService(
        strength_sessions,
        activities,
        consents=gate,
        streams=read_stream,
        audit=audit,
        events=events,
        clock=clock,
        batch=database.batch,
    )
    conditions = CoachingConditions(
        SqliteAggregateRepository(database, COACHING_STATE_CODEC),
        audit=audit,
        events=events,
        clock=clock,
    )
    recovery_observations = SqliteAggregateRepository(database, RECOVERY_OBSERVATION_CODEC)
    recovery = RecoveryService(
        SqliteAggregateRepository(database, CHECKIN_CODEC),
        observations=recovery_observations,
        consents=gate,
        conditions=conditions,
        screen=screen,
        timezone_of=lambda scope: athlete_timezone(profiles, scope),
        events=events,
        clock=clock,
        ids=new_id,
    )
    recovery_assessment = RecoveryAssessmentService(
        recovery,
        SqliteAggregateRepository(database, RECOVERY_ASSESSMENT_CODEC),
        SqliteAggregateRepository(database, RECOVERY_PRESCRIPTION_CODEC),
        consents=gate,
        conditions=conditions,
        events=events,
        clock=clock,
        ids=new_id,
    )
    analytics = AnalyticsService(
        activities=activities,
        store=activity_store,
        overrides=overrides,
        metrics=derived,
        consents=gate,
        strength=strength,
        streams=read_stream,
        safety_hold=lambda scope: Condition.SAFETY_HOLD in conditions.current(scope).conditions,
        batch=database.batch,
    )
    lifecycle = CoachingLifecycle(
        SqliteAggregateRepository(database, COACHING_STATE_CODEC),
        audit=audit,
        events=events,
        clock=clock,
    )
    inputs = AthleteInputsService(
        profiles,
        SqliteAggregateRepository(database, AVAILABILITY_CODEC),
        SqliteAggregateRepository(database, GOAL_CODEC),
        SqliteAggregateRepository(database, SPORT_EVENT_CODEC),
        consents=consents,
        lifecycle=lifecycle,
        screen=screen,
        history=planning_history(analytics, recovery, credentials),
        today=recovery.today,
        clock=clock,
        ids=new_id,
        conditions=conditions,
    )

    def done_today(scope: OwnerScope, day: date) -> frozenset[UUID]:
        return executions.done_workouts(scope, day)  # bound below; read only when checking

    planning_context = PlanningContextSource(
        inputs,
        recovery_assessment.prescriptions,
        today=recovery.today,
        checkins=lambda scope, until, days: recovery.checkins(scope, until=until, days=days),
        done=done_today,
    )
    plans = PlanService(
        SqliteAggregateRepository(database, PLAN_HEADER_CODEC),
        SqliteAggregateRepository(database, PLAN_VERSION_CODEC),
        goals=SqliteAggregateRepository(database, GOAL_CODEC),
        readiness=lambda scope: inputs.readiness(scope).readiness,
        lifecycle=lifecycle,
        gate=PolicyGate(
            gate,
            SqliteAggregateRepository(database, COACHING_STATE_CODEC),
            audit=audit,
            events=events,
            clock=clock,
            integrations=SqliteAggregateRepository(database, INTEGRATION_STATUS_CODEC),
            planning=planning_context,
        ),
        screen=screen,
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
        batch=database.batch,
    )

    def consented_activity(scope: OwnerScope, activity_id: UUID) -> Activity:
        gate.require(scope, ConsentType.ACTIVITIES)  # as analytics.activities does
        return activities.effective(scope, activity_id).activity

    executions = ExecutionService(
        plans,
        SqliteAggregateRepository(database, EXECUTION_LINK_CODEC),
        activities=analytics.activities,
        activity=consented_activity,
        today=recovery.today,
        audit=audit,
        clock=clock,
    )
    generation = PlanGenerationService(
        inputs,
        plans,
        planning=planning_context,
        conditions=conditions,
        weekly_minutes=weekly_minutes_from(analytics),
    )
    adaptation = AdaptationService(
        plans,
        executions,
        generation,
        SqliteAggregateRepository(database, ADAPTATION_DECISION_CODEC),
        assessments=lambda scope, until, days: recovery_assessment.history(
            scope, until=until, days=days
        ),
        prescriptions=recovery_assessment.prescriptions,
        checkins=lambda scope, until, days: recovery.checkins(scope, until=until, days=days),
        availability=inputs.availability,
        readiness=lambda scope: inputs.readiness(scope).readiness,
        goal_date=lambda scope, goal_id: next(
            (item.target_date for item in inputs.goals(scope) if item.goal_id == goal_id), None
        ),
        conditions=conditions,
        lifecycle=lifecycle,
        today=recovery.today,
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
        consents=gate,
    )
    insights = InsightService(
        analytics,
        recovery,
        recovery_assessment,
        SqliteAggregateRepository(database, INSIGHT_CODEC),
        consents=gate,
        events=events,
        clock=clock,
        ids=new_id,
        plan_sessions=executions.planned_sessions,
        goal_sport=lambda scope: (
            None if (goal := inputs.primary_goal(scope)) is None else goal.sport
        ),
    )
    reports = ReportService(
        activities=activities,
        analytics=analytics,
        assessments=recovery_assessment,
        insights=insights,
        conditions=conditions,
        consents=gate,
        today=recovery.today,
        clock=clock,
        executions=executions.executions,
        active_plan=plans.active,
        decisions=adaptation.decisions,
        feasibility=goal_feasibility(inputs),
    )
    ingestion_records = SqliteIngestionRecordRepository(database)
    ingestion = IngestionService(
        formats=LocalFileFormats(ParserSandbox(layout.quarantine)),
        quarantine=FileQuarantine(layout.quarantine),
        scanner=UnavailableMalwareScanner(),
        blobs=blobs,
        raw_sources=raw_sources,
        records=ingestion_records,
        activities=activities,
        issues=issue_store,
        consents=gate,
        screen=screen,
        events=events,
        clock=clock,
        ids=new_id,
        provider_records={"garmin": GarminActivityRecords()},
    )
    integrations = SqliteAggregateRepository(database, INTEGRATION_STATUS_CODEC)
    garmin = garmin_providers(credentials, transport or UrllibTransport(), clock)
    retrier = Retrier(RetryPolicy(), events=events, sleep=sleep)
    sync = SyncService(
        providers=garmin,
        statuses=integrations,
        blobs=blobs,
        raw_sources=raw_sources,
        ingestion=ingestion,
        activities=activity_store,
        records=ingestion_records,
        consents=gate,
        conditions=conditions,
        retrier=retrier,
        events=events,
        clock=clock,
        ids=new_id,
    )
    recovery_sync = RecoverySyncService(
        providers=garmin,
        records={"garmin": GarminDailyRecords()},
        observations=recovery_observations,
        statuses=integrations,
        blobs=blobs,
        raw_sources=raw_sources,
        consents=gate,
        conditions=conditions,
        retrier=retrier,
        timezone_of=lambda scope: athlete_timezone(profiles, scope),
        events=events,
        clock=clock,
        ids=new_id,
    )
    return Runtime(
        layout=layout,
        database=database,
        provider=provider,
        identities=identities,
        audit=audit,
        credentials=credentials,
        consents=consents,
        authentication=authentication,
        authorization=authorization,
        profiles=profiles,
        activity_store=activity_store,
        issue_store=issue_store,
        ingestion_records=ingestion_records,
        activities=activities,
        ingestion=ingestion,
        analytics=analytics,
        strength=strength,
        recovery=recovery,
        recovery_sync=recovery_sync,
        recovery_assessment=recovery_assessment,
        insights=insights,
        reports=reports,
        inputs=inputs,
        plans=plans,
        generation=generation,
        executions=executions,
        adaptation=adaptation,
        screen=screen,
        integrations=integrations,
        conditions=conditions,
        sync=sync,
        events=events,
        clock=clock,
    )
