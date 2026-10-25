"""DataQualityIssue: anomalies and ambiguous matches shown to the athlete (FR-ACT-009)."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from coach.domain.activity.issues import (
    DataQualityIssue,
    IssueKind,
    IssueResolution,
    IssueStatus,
)
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.common.quality import Anomaly
from samples import ACTIVITY, ATHLETE, NOW

ISSUE = UUID("0193b2a0-0000-7000-8000-000000000601")
PENDING = UUID("0193b2a0-0000-7000-8000-000000000102")


def _anomaly_issue() -> DataQualityIssue:
    return DataQualityIssue.for_anomaly(
        issue_id=ISSUE,
        athlete_id=ATHLETE,
        activity_id=ACTIVITY,
        anomaly=Anomaly("HR_OUT_OF_RANGE", "cardiovascular.maxHr", "245 above 230"),
        created_at=NOW,
    )


def _ambiguous_issue() -> DataQualityIssue:
    return DataQualityIssue.for_ambiguous_match(
        issue_id=ISSUE,
        athlete_id=ATHLETE,
        activity_id=PENDING,
        related_activity_id=ACTIVITY,
        match_score=0.72,
        dedup_rules_version="1.0.0",
        created_at=NOW,
    )


@pytest.mark.req("FR-ACT-009")
def test_anomaly_issue_opens_with_its_field() -> None:
    issue = _anomaly_issue()
    assert issue.is_open
    assert issue.kind is IssueKind.ANOMALY
    assert issue.anomaly_key() == (ACTIVITY, "HR_OUT_OF_RANGE", "cardiovascular.maxHr")
    with pytest.raises(DomainValidationError):
        replace(issue, field=None)
    with pytest.raises(DomainValidationError):
        replace(issue, field="cardiovascular.peakHr")
    with pytest.raises(DomainValidationError):
        replace(issue, related_activity_id=PENDING)


@pytest.mark.req("FR-ACT-007")
def test_ambiguous_issue_names_candidate_score_and_rules() -> None:
    issue = _ambiguous_issue()
    assert issue.code == "AMBIGUOUS_MATCH"
    assert issue.field is None
    for changes in (
        {"related_activity_id": None},
        {"match_score": None},
        {"match_score": 1.5},
        {"dedup_rules_version": None},
        {"related_activity_id": PENDING},
        {"code": "HR_OUT_OF_RANGE"},
    ):
        with pytest.raises(DomainValidationError):
            replace(issue, **changes)


@pytest.mark.req("FR-ACT-007", "FR-ACT-009")
def test_issues_close_once_with_a_resolution_of_their_kind() -> None:
    later = NOW + timedelta(hours=1)
    closed = _ambiguous_issue().close(IssueResolution.MERGED, at=later)
    assert (closed.status, closed.closed_at, closed.resolution) == (
        IssueStatus.CLOSED,
        later,
        IssueResolution.MERGED,
    )
    with pytest.raises(InvalidTransitionError):
        closed.close(IssueResolution.KEPT_SEPARATE, at=later)
    with pytest.raises(InvalidTransitionError):
        _ambiguous_issue().close(IssueResolution.ACKNOWLEDGED, at=later)
    with pytest.raises(InvalidTransitionError):
        _anomaly_issue().close(IssueResolution.MERGED, at=later)
    acknowledged = _anomaly_issue().close(IssueResolution.ACKNOWLEDGED, at=later)
    assert not acknowledged.is_open


@pytest.mark.req("FR-ACT-009")
def test_status_time_and_resolution_agree() -> None:
    issue = _anomaly_issue()
    with pytest.raises(DomainValidationError):
        replace(issue, status=IssueStatus.CLOSED)
    with pytest.raises(DomainValidationError):
        replace(issue, resolution=IssueResolution.ACKNOWLEDGED)
    with pytest.raises(DomainValidationError):
        replace(
            issue,
            status=IssueStatus.CLOSED,
            closed_at=NOW,
            resolution=IssueResolution.KEPT_SEPARATE,
        )
    assert issue.with_version(2).version == 2
    assert issue.entity_id == ISSUE
