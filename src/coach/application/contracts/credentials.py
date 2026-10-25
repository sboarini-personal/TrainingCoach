"""CredentialRecord <-> credential-record contract (urn:coach:schema:credential-record:1)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    instant,
    optional_date,
    optional_instant,
    parse_instant,
    parse_optional_instant,
)
from coach.application.credentials import CredentialKind, CredentialRecord


def credential_record_to_contract(record: CredentialRecord) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "credentialId": str(record.credential_id),
        "athleteId": str(record.athlete_id),
        "version": record.version,
        "kind": record.kind.value,
        "handle": record.handle,
        "createdAt": instant(record.created_at),
        "rotatedAt": optional_instant(record.rotated_at),
        "expiresOn": optional_date(record.expires_on),
    }


def credential_record_from_contract(document: Document) -> CredentialRecord:
    expires_on = document["expiresOn"]
    record = CredentialRecord(
        credential_id=UUID(document["credentialId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        kind=CredentialKind(document["kind"]),
        created_at=parse_instant(document["createdAt"]),
        rotated_at=parse_optional_instant(document["rotatedAt"]),
        expires_on=None if expires_on is None else date.fromisoformat(expires_on),
    )
    if record.handle != document["handle"]:
        raise ValueError("credential handle does not match its kind and athlete")
    return record
