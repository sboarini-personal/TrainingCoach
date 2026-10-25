"""Structured observability events carry only codes and identifiers (NFR-OBS-001/002)."""

import logging

import pytest

from coach.application.events import EventName, check_fields
from coach.infrastructure.events import LoggingEventSink

pytestmark = pytest.mark.req("NFR-OBS-001", "NFR-OBS-002")


def test_event_names_follow_the_nfr_catalogue() -> None:
    assert {name.value for name in EventName} >= {
        "security.authn.failed",
        "security.authz.denied",
        "security.secret_detected",
        "security.rate_limited",
        "security.lookup.not_found",
        "consent.granted",
        "consent.revoked",
        "consent.declined",
        "credential.stored",
        "credential.rotated",
        "activity.created",
        "activity.corrected",
        "ingestion.duplicate",
        "dedup.linked",
        "dedup.ambiguous",
        "dedup.merged",
        "dedup.kept_separate",
        "dedup.split",
        "quality.issue.raised",
        "quality.issue.resolved",
        "ingestion.file.received",
        "ingestion.file.parsed",
        "ingestion.file.rejected",
        "ingestion.file.failed",
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"client": "has spaces"},
        {"Client": "cli"},
        {"note": "x" * 129},
        {"detail": "password=hunter2;"},
    ],
)
def test_free_text_fields_are_rejected(fields: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="event field"):
        check_fields(fields)


def test_logging_sink_writes_one_structured_line(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="coach.events"):
        LoggingEventSink().emit(EventName.CONSENT_GRANTED, {"consent_type": "HRV", "client": "cli"})
    assert caplog.messages == ["consent.granted client=cli consent_type=HRV"]
