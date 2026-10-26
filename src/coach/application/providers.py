"""FitnessProvider port and its payload types (INT-GAR-001; provider-capabilities spec 1.1).

Adapters return RAW payloads with metadata and nothing else: storage, normalisation, retries
and the sync cursor belong to the application. Failures are typed ``ProviderError``s that
carry codes only, never a response body, a header or a credential.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol

from coach.application.errors import ApplicationError
from coach.application.files import DecodedSession
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import DateRange
from coach.domain.integration.capabilities import ProviderCapabilities
from coach.domain.integration.status import IntegrationReason

_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_CATEGORY: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MEDIA_TYPE: Final = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")
_PROVIDER: Final = re.compile(r"^[a-z0-9_]+$")
_PROVIDER_ID: Final = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
JSON_MEDIA_TYPE: Final = "application/json"
ZIP_MEDIA_TYPE: Final = "application/x-zip-compressed"
TRANSIENT: Final = frozenset(
    {IntegrationReason.PROVIDER_UNAVAILABLE, IntegrationReason.PROVIDER_RATE_LIMITED}
)


class ProviderError(ApplicationError):
    """A provider call failed; ``detail`` is a code, ``retry_after`` the provider's wait."""

    def __init__(
        self,
        code: IntegrationReason,
        detail: str | None = None,
        *,
        retry_after: float | None = None,
    ) -> None:
        if detail is not None and not _CODE.match(detail):
            raise DomainValidationError("provider error details are upper-case codes")
        if retry_after is not None and (not math.isfinite(retry_after) or retry_after < 0):
            raise DomainValidationError("retry-after is a non-negative number of seconds")
        super().__init__(code.value if detail is None else f"{code.value} ({detail})")
        self.code = code
        self.detail = detail
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        return self.code in TRANSIENT


class ProviderNotConfiguredError(ApplicationError):
    """The provider has no credential: nothing to sync, and not an integration error."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"{provider} is not configured")
        self.provider = provider


def check_provider_id(value: str) -> str:
    if not _PROVIDER_ID.match(value):
        raise DomainValidationError("invalid provider object id")
    return value


@dataclass(frozen=True, slots=True)
class RawPayload:
    """One provider response, byte-exact, with what was asked for (INT-GAR-009)."""

    provider: str
    category: str
    media_type: str
    data: bytes = field(repr=False)
    provider_object_id: str | None = None
    window: DateRange | None = None

    def __post_init__(self) -> None:
        if not _PROVIDER.match(self.provider):
            raise DomainValidationError("invalid provider")
        if not _CATEGORY.match(self.category):
            raise DomainValidationError("invalid payload category")
        if not _MEDIA_TYPE.match(self.media_type):
            raise DomainValidationError("invalid media type")
        if self.provider_object_id is not None:
            check_provider_id(self.provider_object_id)


@dataclass(frozen=True, slots=True)
class ActivityRef:
    """A provider activity as listed; only its ID is trusted to address it again."""

    provider_activity_id: str

    def __post_init__(self) -> None:
        check_provider_id(self.provider_activity_id)


@dataclass(frozen=True, slots=True)
class ActivityPage:
    refs: tuple[ActivityRef, ...]
    payload: RawPayload


class FitnessProvider(Protocol):
    @property
    def provider(self) -> str: ...

    def get_capabilities(self) -> ProviderCapabilities: ...

    def get_activities(self, window: DateRange, offset: int, limit: int) -> ActivityPage: ...

    def get_activity_details(self, ref: ActivityRef) -> RawPayload | None:
        """The provider record; ``None`` when the activity no longer exists."""
        ...

    def get_activity_file(self, ref: ActivityRef) -> RawPayload | None:
        """The original file; ``None`` when the activity has none."""
        ...


class ProviderRecords(Protocol):
    """A provider's record normaliser (garmin spec section 10), run by the ingestion pipeline.

    Rejects a record with ``FileRejectedError``; never guesses a value the record lacks.
    """

    def decode(
        self, data: bytes, *, provider_object_id: str, received_at: datetime
    ) -> Sequence[DecodedSession]: ...
