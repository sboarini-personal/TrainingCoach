"""Document codecs: one per persisted aggregate, bound to its published contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from coach.application.contracts._json import Document, Json, check_schema_version
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
from coach.application.contracts.profile import profile_from_contract, profile_to_contract
from coach.application.contracts.raw import raw_source_from_contract, raw_source_to_contract
from coach.application.credentials import CredentialRecord
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.availability.availability import Availability
from coach.domain.coaching.state import CoachingState
from coach.domain.consent.ledger import ConsentLedger
from coach.domain.goals.goal import Goal, SportEvent
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
)
