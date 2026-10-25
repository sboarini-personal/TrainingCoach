"""Domain aggregates <-> JSON contracts in spec/contracts (schema version 1.x)."""

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    UnsupportedSchemaVersionError,
)
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
from coach.application.contracts.codecs import (
    ACTIVITY_CODEC,
    ALL_CODECS,
    AVAILABILITY_CODEC,
    COACHING_STATE_CODEC,
    CONSENT_LEDGER_CODEC,
    CREDENTIAL_RECORD_CODEC,
    DATA_OVERRIDE_CODEC,
    DATA_QUALITY_ISSUE_CODEC,
    DERIVED_VALUE_CODEC,
    GOAL_CODEC,
    INGESTION_RECORD_CODEC,
    OBSERVATION_CODEC,
    PROFILE_CODEC,
    RAW_SOURCE_CODEC,
    SPORT_EVENT_CODEC,
    TIME_SERIES_CODEC,
    DocumentCodec,
)
from coach.application.contracts.consent import (
    consent_ledger_from_contract,
    consent_ledger_to_contract,
    consent_record_from_contract,
    consent_record_to_contract,
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
from coach.application.contracts.profile import profile_from_contract, profile_to_contract
from coach.application.contracts.raw import raw_source_from_contract, raw_source_to_contract

__all__ = [
    "ACTIVITY_CODEC",
    "ALL_CODECS",
    "AVAILABILITY_CODEC",
    "COACHING_STATE_CODEC",
    "CONSENT_LEDGER_CODEC",
    "CREDENTIAL_RECORD_CODEC",
    "DATA_OVERRIDE_CODEC",
    "DATA_QUALITY_ISSUE_CODEC",
    "DERIVED_VALUE_CODEC",
    "GOAL_CODEC",
    "INGESTION_RECORD_CODEC",
    "OBSERVATION_CODEC",
    "PROFILE_CODEC",
    "RAW_SOURCE_CODEC",
    "SCHEMA_VERSION",
    "SPORT_EVENT_CODEC",
    "TIME_SERIES_CODEC",
    "Document",
    "DocumentCodec",
    "Json",
    "UnsupportedSchemaVersionError",
    "activity_from_contract",
    "activity_to_contract",
    "availability_from_contract",
    "availability_to_contract",
    "coaching_state_from_contract",
    "coaching_state_to_contract",
    "consent_ledger_from_contract",
    "consent_ledger_to_contract",
    "consent_record_from_contract",
    "consent_record_to_contract",
    "derived_value_from_contract",
    "derived_value_to_contract",
    "goal_from_contract",
    "goal_to_contract",
    "issue_from_contract",
    "issue_to_contract",
    "observation_from_contract",
    "observation_to_contract",
    "override_from_contract",
    "override_to_contract",
    "profile_from_contract",
    "profile_to_contract",
    "raw_source_from_contract",
    "raw_source_to_contract",
    "sport_event_from_contract",
    "sport_event_to_contract",
]
