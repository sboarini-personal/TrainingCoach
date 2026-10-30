"""Structured observability events (NFR-OBS-001; spec/nfr.spec.md).

Events carry only codes and identifiers: field values are restricted to a short code-like
alphabet, so free text (and with it secrets or health values) cannot be emitted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Final

_FIELD_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_FIELD_VALUE: Final = re.compile(r"^[A-Za-z0-9._:/-]{0,128}$")


class EventName(StrEnum):
    AUTHN_FAILED = "security.authn.failed"
    AUTHZ_DENIED = "security.authz.denied"
    SECRET_DETECTED = "security.secret_detected"  # noqa: S105  # pragma: allowlist secret
    RATE_LIMITED = "security.rate_limited"
    LOOKUP_NOT_FOUND = "security.lookup.not_found"
    CONSENT_GRANTED = "consent.granted"
    CONSENT_REVOKED = "consent.revoked"
    CONSENT_DECLINED = "consent.declined"
    CREDENTIAL_STORED = "credential.stored"
    CREDENTIAL_ROTATED = "credential.rotated"
    ACTIVITY_CREATED = "activity.created"
    ACTIVITY_CORRECTED = "activity.corrected"
    ACTIVITY_REPROCESSED = "activity.reprocessed"
    STRENGTH_SESSION_CHANGED = "strength.session.changed"
    INGESTION_RECEIVED = "ingestion.file.received"
    INGESTION_PARSED = "ingestion.file.parsed"
    INGESTION_REJECTED = "ingestion.file.rejected"
    INGESTION_FAILED = "ingestion.file.failed"
    INGESTION_DUPLICATE = "ingestion.duplicate"
    DEDUP_LINKED = "dedup.linked"
    DEDUP_AMBIGUOUS = "dedup.ambiguous"
    DEDUP_MERGED = "dedup.merged"
    DEDUP_KEPT_SEPARATE = "dedup.kept_separate"
    DEDUP_SPLIT = "dedup.split"
    QUALITY_ISSUE_RAISED = "quality.issue.raised"
    QUALITY_ISSUE_RESOLVED = "quality.issue.resolved"
    INTEGRATION_RETRY = "integration.request.retried"
    INTEGRATION_SYNC_STARTED = "integration.sync.started"
    INTEGRATION_SYNC_SUCCEEDED = "integration.sync.succeeded"
    INTEGRATION_SYNC_FAILED = "integration.sync.failed"
    INTEGRATION_CAPABILITY_OBSERVED = "integration.capability.observed"
    EXTERNAL_WRITE_PROPOSED = "external_write.proposed"
    EXTERNAL_WRITE_REFUSED = "external_write.refused"
    CONDITION_ACTIVATED = "coaching.condition.activated"
    CONDITION_CLEARED = "coaching.condition.cleared"


def check_fields(fields: Mapping[str, str]) -> dict[str, str]:
    """Return the fields if every name and value is a code; raise otherwise."""
    for name, value in fields.items():
        if not _FIELD_NAME.match(name) or not _FIELD_VALUE.match(value):
            raise ValueError(f"invalid event field {name!r}")
    return dict(fields)
