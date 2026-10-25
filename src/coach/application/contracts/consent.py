"""ConsentRecord and ConsentLedger <-> consent and consent-ledger contracts."""

from __future__ import annotations

from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    instant,
    optional_instant,
    parse_instant,
    parse_optional_instant,
)
from coach.domain.consent.ledger import ConsentLedger, ConsentRecord, ConsentType


def consent_record_to_contract(record: ConsentRecord) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "consentId": str(record.consent_id),
        "athleteId": str(record.athlete_id),
        "consentType": record.consent_type.value,
        "granted": record.granted,
        "grantedAt": optional_instant(record.granted_at),
        "revokedAt": optional_instant(record.revoked_at),
        "policyVersion": record.policy_version,
        "purpose": record.purpose,
        "recordedAt": instant(record.recorded_at),
    }


def consent_record_from_contract(document: Document) -> ConsentRecord:
    return ConsentRecord(
        consent_id=UUID(document["consentId"]),
        athlete_id=UUID(document["athleteId"]),
        consent_type=ConsentType(document["consentType"]),
        granted=document["granted"],
        granted_at=parse_optional_instant(document["grantedAt"]),
        revoked_at=parse_optional_instant(document["revokedAt"]),
        policy_version=document["policyVersion"],
        purpose=document["purpose"],
        recorded_at=parse_instant(document["recordedAt"]),
    )


def consent_ledger_to_contract(ledger: ConsentLedger) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "athleteId": str(ledger.athlete_id),
        "version": ledger.version,
        "records": [consent_record_to_contract(record) for record in ledger.records],
    }


def consent_ledger_from_contract(document: Document) -> ConsentLedger:
    athlete_id = UUID(document["athleteId"])
    records = tuple(consent_record_from_contract(item) for item in document["records"])
    if any(record.athlete_id != athlete_id for record in records):
        raise ValueError("consent records belong to another athlete")
    return ConsentLedger(athlete_id, records, document["version"])
