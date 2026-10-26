"""Document codecs: one per persisted aggregate, bound to its published contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from coach.application.contracts._json import Document, Json, check_schema_version
from coach.application.contracts.activities import (
    activity_from_contract,
    activity_to_contract,
    issue_from_contract,
    issue_to_contract,
    observation_from_contract,
    observation_to_contract,
    override_from_contract,
    override_to_contract,
)
from coach.application.contracts.availability import (
    availability_from_contract,
    availability_to_contract,
)
from coach.application.contracts.coaching import (
    coaching_state_from_contract,
    coaching_state_to_contract,
)
from coach.application.contracts.consent import (
    consent_ledger_from_contract,
    consent_ledger_to_contract,
)
from coach.application.contracts.credentials import (
    credential_record_from_contract,
    credential_record_to_contract,
)
from coach.application.contracts.derived import (
    derived_value_from_contract,
    derived_value_to_contract,
)
from coach.application.contracts.goals import (
    goal_from_contract,
    goal_to_contract,
    sport_event_from_contract,
    sport_event_to_contract,
)
from coach.application.contracts.ingestion import (
    ingestion_record_from_contract,
    ingestion_record_to_contract,
    time_series_from_contract,
    time_series_to_contract,
)
from coach.application.contracts.integration import (
    integration_status_from_contract,
    integration_status_to_contract,
)
from coach.application.contracts.profile import profile_from_contract, profile_to_contract
from coach.application.contracts.raw import raw_source_from_contract, raw_source_to_contract
from coach.application.credentials import CredentialRecord
from coach.domain.activity.activity import Activity
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.activity.observation import Observation
from coach.domain.activity.overrides import DataOverride
from coach.domain.activity.streams import TimeSeries
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.availability.availability import Availability
from coach.domain.coaching.state import CoachingState
from coach.domain.consent.ledger import ConsentLedger
from coach.domain.goals.goal import Goal, SportEvent
from coach.domain.ingestion.record import IngestionRecord
from coach.domain.integration.status import IntegrationStatus
from coach.domain.raw.source import RawSource
from coach.metrics.derived import DerivedMetricValue


@dataclass(frozen=True, slots=True)
class DocumentCodec[T]:
    """Encodes an aggregate as its contract document and decodes it back.

    ``kind`` names the aggregate in storage; ``schema`` names its contract in spec/contracts.
    ``load`` refuses documents of an unsupported schema major version (NFR-REL-004).
    """

    kind: str
    schema: str
    encode: Callable[[T], Json]
    decode: Callable[[Document], T]

    def dump(self, entity: T) -> Json:
        return self.encode(entity)

    def load(self, document: Document) -> T:
        check_schema_version(document)
        return self.decode(document)


PROFILE_CODEC: Final[DocumentCodec[AthleteProfile]] = DocumentCodec(
    "athlete_profile", "athlete-profile", profile_to_contract, profile_from_contract
)
AVAILABILITY_CODEC: Final[DocumentCodec[Availability]] = DocumentCodec(
    "availability", "availability", availability_to_contract, availability_from_contract
)
GOAL_CODEC: Final[DocumentCodec[Goal]] = DocumentCodec(
    "goal", "goal", goal_to_contract, goal_from_contract
)
SPORT_EVENT_CODEC: Final[DocumentCodec[SportEvent]] = DocumentCodec(
    "sport_event", "sport-event", sport_event_to_contract, sport_event_from_contract
)
CONSENT_LEDGER_CODEC: Final[DocumentCodec[ConsentLedger]] = DocumentCodec(
    "consent_ledger", "consent-ledger", consent_ledger_to_contract, consent_ledger_from_contract
)
COACHING_STATE_CODEC: Final[DocumentCodec[CoachingState]] = DocumentCodec(
    "coaching_state", "coaching-state", coaching_state_to_contract, coaching_state_from_contract
)
RAW_SOURCE_CODEC: Final[DocumentCodec[RawSource]] = DocumentCodec(
    "raw_source", "raw-source", raw_source_to_contract, raw_source_from_contract
)
DERIVED_VALUE_CODEC: Final[DocumentCodec[DerivedMetricValue]] = DocumentCodec(
    "derived_metric_value",
    "derived-metric-value",
    derived_value_to_contract,
    derived_value_from_contract,
)
CREDENTIAL_RECORD_CODEC: Final[DocumentCodec[CredentialRecord]] = DocumentCodec(
    "credential_record",
    "credential-record",
    credential_record_to_contract,
    credential_record_from_contract,
)
ACTIVITY_CODEC: Final[DocumentCodec[Activity]] = DocumentCodec(
    "activity", "activity", activity_to_contract, activity_from_contract
)
OBSERVATION_CODEC: Final[DocumentCodec[Observation]] = DocumentCodec(
    "source_observation", "source-observation", observation_to_contract, observation_from_contract
)
DATA_OVERRIDE_CODEC: Final[DocumentCodec[DataOverride]] = DocumentCodec(
    "data_override", "data-override", override_to_contract, override_from_contract
)
DATA_QUALITY_ISSUE_CODEC: Final[DocumentCodec[DataQualityIssue]] = DocumentCodec(
    "data_quality_issue", "data-quality-issue", issue_to_contract, issue_from_contract
)
INGESTION_RECORD_CODEC: Final[DocumentCodec[IngestionRecord]] = DocumentCodec(
    "ingestion_record",
    "ingestion-record",
    ingestion_record_to_contract,
    ingestion_record_from_contract,
)
TIME_SERIES_CODEC: Final[DocumentCodec[TimeSeries]] = DocumentCodec(
    "time_series", "time-series", time_series_to_contract, time_series_from_contract
)
INTEGRATION_STATUS_CODEC: Final[DocumentCodec[IntegrationStatus]] = DocumentCodec(
    "integration_status",
    "integration-status",
    integration_status_to_contract,
    integration_status_from_contract,
)
ALL_CODECS: Final[tuple[DocumentCodec[Any], ...]] = (
    PROFILE_CODEC,
    AVAILABILITY_CODEC,
    GOAL_CODEC,
    SPORT_EVENT_CODEC,
    CONSENT_LEDGER_CODEC,
    COACHING_STATE_CODEC,
    RAW_SOURCE_CODEC,
    DERIVED_VALUE_CODEC,
    CREDENTIAL_RECORD_CODEC,
    ACTIVITY_CODEC,
    OBSERVATION_CODEC,
    DATA_OVERRIDE_CODEC,
    DATA_QUALITY_ISSUE_CODEC,
    INGESTION_RECORD_CODEC,
    TIME_SERIES_CODEC,
    INTEGRATION_STATUS_CODEC,
)
