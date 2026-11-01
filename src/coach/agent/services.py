"""The application use cases the AI coach's tools may call (ADR-001, SEC-AUTHZ-002).

The composition root (the CLI, later the API) builds this from its runtime. Only
application services appear here: no repository of raw data, no credential, no provider
adapter. Every service enforces ownership through the owner scope it is given.
"""

from __future__ import annotations

from dataclasses import dataclass

from coach.application.activities import ActivityService
from coach.application.adaptation import AdaptationService
from coach.application.analytics import AnalyticsService
from coach.application.athlete_inputs import AthleteInputsService
from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentGate
from coach.application.executions import ExecutionService
from coach.application.insights import InsightService
from coach.application.plan_generation import PlanGenerationService
from coach.application.plans import PlanService
from coach.application.ports import Clock, OwnedRepository
from coach.application.recovery import RecoveryService
from coach.application.recovery_assessment import RecoveryAssessmentService
from coach.application.recovery_sync import RecoverySyncService
from coach.application.reports import ReportService
from coach.application.strength import StrengthService
from coach.application.sync import SyncService
from coach.domain.integration.status import IntegrationStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentServices:
    consents: ConsentGate
    conditions: CoachingConditions
    inputs: AthleteInputsService
    activities: ActivityService
    analytics: AnalyticsService
    strength: StrengthService
    recovery: RecoveryService
    assessments: RecoveryAssessmentService
    plans: PlanService
    generation: PlanGenerationService
    executions: ExecutionService
    adaptation: AdaptationService
    insights: InsightService
    reports: ReportService
    integrations: OwnedRepository[IntegrationStatus]
    sync: SyncService
    recovery_sync: RecoverySyncService
    clock: Clock
