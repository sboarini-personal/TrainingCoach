"""Activity ingestion, deduplication, corrections and the EFFECTIVE view (P5).

Ingestion links each source observation to one logical Activity through the matching tiers
(spec/integration/deduplication.spec.md); corrections are append-only overrides applied at
read time (spec/functional/activities.spec.md section 4). After every change the activity's
bound metrics are recomputed and its quality issues synchronised. Commands are safe to retry:
a retry completes follow-up work an interrupted run left undone (NFR-REL-001).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.consent import ConsentGate
from coach.application.derived_metrics import DerivedMetricService
from coach.application.errors import InvalidOperationError, NotFoundError
from coach.application.events import EventName
from coach.application.identity import Principal
from coach.application.ownership import OwnerScope, check_owner
from coach.application.ports import (
    ActivityChange,
    ActivityRepository,
    AuditLog,
    Clock,
    EventSink,
    IdFactory,
    ObservationRepository,
    OverrideRepository,
    OwnedRepository,
    RawSourceRepository,
)
from coach.application.screening import SecretScreen
from coach.domain.activity.activity import Activity, ActivityStatus, LinkKind, SourceLink
from coach.domain.activity.fields import FieldKind, check_subtype, field_spec, sport_of
from coach.domain.activity.issues import (
    AMBIGUOUS_MATCH_CODE,
    DataQualityIssue,
    IssueKind,
    IssueResolution,
)
from coach.domain.activity.matching import (
    DEDUP_RULES_VERSION,
    START_ZERO_SECONDS,
    Match,
    MatchDecision,
    MatchFacts,
    best_match,
)
from coach.domain.activity.observation import Observation, SourceKey, source_kind_of
from coach.domain.activity.overrides import (
    SUBTYPE_FIELD,
    DataOverride,
    EntityType,
    apply_overrides,
    current_overrides,
)
from coach.domain.activity.resolution import RESOLUTION_RULES_VERSION, resolve
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import UncertaintyReason
from coach.domain.consent.ledger import ConsentType
from coach.domain.raw.source import RawSource
from coach.metrics.catalog import ACTIVITY_BINDINGS, ActivityBinding
from coach.metrics.derived import MetricInput, MetricSubject
from coach.metrics.quality import QUALITY_RULES_VERSION, assess, detect_anomalies

_CANDIDATE_WINDOW = timedelta(seconds=START_ZERO_SECONDS)


class IngestOutcome(StrEnum):
    CREATED = "CREATED"
    DUPLICATE = "DUPLICATE"
    NEW_VERSION = "NEW_VERSION"
    LINKED = "LINKED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class IngestResult:
    activity: Activity
    outcome: IngestOutcome
    match_score: float | None = None


@dataclass(frozen=True, slots=True)
class EffectiveActivity:
    """The EFFECTIVE view (``activity``) next to the stored NORMALIZED record."""

    normalized: Activity
    activity: Activity
    overrides: tuple[DataOverride, ...]


@dataclass(frozen=True, slots=True)
class Correction:
    """A stored correction; ``secret_notice`` is the fixed notice when a secret was removed."""

    override: DataOverride
    secret_notice: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityStores:
    activities: ActivityRepository
    observations: ObservationRepository
    overrides: OverrideRepository
    issues: OwnedRepository[DataQualityIssue]
    raw_sources: RawSourceRepository


def build_activity(
    *,
    activity_id: UUID,
    status: ActivityStatus,
    links: Sequence[SourceLink],
    observations: Sequence[Observation],
    imported_at: datetime,
    athlete_timezone: ZoneInfo,
    merged_into: UUID | None = None,
    version: int = 1,
) -> Activity:
    """Resolve an activity from its sources' observations and assess its quality."""
    resolution = resolve(observations, athlete_timezone=athlete_timezone)
    return Activity(
        activity_id=activity_id,
        athlete_id=observations[0].athlete_id,
        status=status,
        sources=tuple(links),
        imported_at=imported_at,
        resolution_rules_version=RESOLUTION_RULES_VERSION,
        quality_rules_version=QUALITY_RULES_VERSION,
        values=resolution.values,
        provenance=resolution.provenance,
        quality=assess(
            resolution.values, imported_at=imported_at, source_anomalies=resolution.anomalies
        ),
        streams=resolution.streams,
        merged_into=merged_into,
        version=version,
    )


def _check_observation_source(observation: Observation, raw: RawSource) -> None:
    expected = (raw.sha256, raw.origin, raw.provider, raw.received_at)
    actual = (
        observation.raw_sha256,
        observation.origin,
        observation.provider,
        observation.received_at,
    )
    if actual != expected or observation.kind is not source_kind_of(raw.origin, raw.detected_type):
        raise DomainValidationError("observation does not describe its raw source")


class ActivityService:
    def __init__(
        self,
        stores: ActivityStores,
        *,
        consents: ConsentGate,
        metrics: DerivedMetricService,
        timezone_of: Callable[[OwnerScope], ZoneInfo],
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
        screen: SecretScreen,
        bindings: Sequence[ActivityBinding] = ACTIVITY_BINDINGS,
    ) -> None:
        self._stores = stores
        self._consents = consents
        self._metrics = metrics
        self._timezone_of = timezone_of
        self._audit = audit
        self._events = events
        self._clock = clock
        self._ids = ids
        self._screen = screen
        self._bindings = tuple(bindings)

    # ------------------------------------------------------------------ ingestion

    def ingest(self, scope: OwnerScope, observation: Observation) -> IngestResult:
        """Link an observation to its logical activity (FR-ACT-007, NFR-REL-001)."""
        check_owner(scope, observation.athlete_id)
        self._consents.require(scope, ConsentType.ACTIVITIES)
        raw = self._stores.raw_sources.get(scope, observation.raw_source_id)
        _check_observation_source(observation, raw)
        self._stores.observations.add(scope, observation)
        existing = self._stores.activities.find_by_source(scope, observation.key)
        if existing is not None:
            self._events.emit(EventName.INGESTION_DUPLICATE, {"tier": "1"})
            self._ensure_review_issue(scope, existing)
            self._after_change(scope, existing)
            current = self._stores.activities.get(scope, existing.activity_id)
            return IngestResult(current, IngestOutcome.DUPLICATE)
        now = self._clock()
        record = observation.provider_record
        same_record = (
            None
            if record is None
            else self._stores.activities.find_by_provider_record(scope, *record)
        )
        if same_record is not None:
            linked = self._link(scope, same_record, observation, linked_at=now, score=None)
            self._events.emit(EventName.DEDUP_LINKED, {"tier": "2"})
            return IngestResult(linked, IngestOutcome.NEW_VERSION)
        match = self._best_match(scope, MatchFacts.of(observation.values))
        if match is not None and match.decision is MatchDecision.LINK:
            target = self._stores.activities.get(scope, match.activity_id)
            linked = self._link(scope, target, observation, linked_at=now, score=match.score)
            self._events.emit(EventName.DEDUP_LINKED, {"tier": "3", "score": _score(match)})
            return IngestResult(linked, IngestOutcome.LINKED, match.score)
        if match is not None and match.decision is MatchDecision.AMBIGUOUS:
            pending = self._create(scope, observation, ActivityStatus.PENDING_REVIEW, now)
            self._open_review_issue(scope, pending, match, now)
            self._events.emit(EventName.DEDUP_AMBIGUOUS, {"score": _score(match)})
            return IngestResult(pending, IngestOutcome.AMBIGUOUS, match.score)
        created = self._create(scope, observation, ActivityStatus.ACTIVE, now)
        self._events.emit(EventName.ACTIVITY_CREATED, {"sport": created.sport.value})
        return IngestResult(created, IngestOutcome.CREATED)

    def _create(
        self, scope: OwnerScope, observation: Observation, status: ActivityStatus, now: datetime
    ) -> Activity:
        link = SourceLink.of(observation, link_kind=LinkKind.PRIMARY, linked_at=now)
        activity = build_activity(
            activity_id=self._ids(),
            status=status,
            links=(link,),
            observations=(observation,),
            imported_at=now,
            athlete_timezone=self._timezone_of(scope),
        )
        (stored,) = self._stores.activities.save(scope, [ActivityChange(activity)])
        self._after_change(scope, stored)
        return stored

    def _link(
        self,
        scope: OwnerScope,
        target: Activity,
        observation: Observation,
        *,
        linked_at: datetime,
        score: float | None,
    ) -> Activity:
        link = SourceLink.of(
            observation,
            link_kind=LinkKind.ENRICHMENT,
            linked_at=linked_at,
            match_score=None if score is None else round(score, 4),
            dedup_rules_version=DEDUP_RULES_VERSION,
        )
        rebuilt = self._rebuild(scope, target, (*target.sources, link))
        (stored,) = self._stores.activities.save(scope, [ActivityChange(rebuilt, target.version)])
        self._after_change(scope, stored)
        return stored

    def _rebuild(
        self,
        scope: OwnerScope,
        activity: Activity,
        links: Sequence[SourceLink],
        *,
        status: ActivityStatus | None = None,
    ) -> Activity:
        return build_activity(
            activity_id=activity.activity_id,
            status=activity.status if status is None else status,
            links=links,
            observations=[self._stores.observations.get(scope, link.key) for link in links],
            imported_at=activity.imported_at,
            athlete_timezone=self._timezone_of(scope),
            merged_into=activity.merged_into,
            version=activity.version,
        )

    def _best_match(
        self, scope: OwnerScope, facts: MatchFacts, *, exclude: UUID | None = None
    ) -> Match | None:
        nearby = self._stores.activities.starting_between(
            scope,
            facts.start_time_utc - _CANDIDATE_WINDOW,
            facts.start_time_utc + _CANDIDATE_WINDOW,
        )
        candidates = [
            (activity.activity_id, MatchFacts.of(self._effective_values(scope, activity)))
            for activity in nearby
            if activity.is_live and activity.activity_id != exclude
        ]
        return best_match(facts, candidates)

    def _open_review_issue(
        self, scope: OwnerScope, pending: Activity, match: Match, now: datetime
    ) -> None:
        issue = DataQualityIssue.for_ambiguous_match(
            issue_id=self._ids(),
            athlete_id=scope.athlete_id,
            activity_id=pending.activity_id,
            related_activity_id=match.activity_id,
            match_score=round(match.score, 4),
            dedup_rules_version=DEDUP_RULES_VERSION,
            created_at=now,
        )
        self._stores.issues.add(scope, issue)
        self._events.emit(EventName.QUALITY_ISSUE_RAISED, {"code": AMBIGUOUS_MATCH_CODE})

    def _ensure_review_issue(self, scope: OwnerScope, activity: Activity) -> None:
        """A PENDING_REVIEW activity always has an open ambiguous-match issue (retry repair)."""
        if activity.status is not ActivityStatus.PENDING_REVIEW:
            return
        if any(
            issue.is_open and issue.kind is IssueKind.AMBIGUOUS_MATCH
            for issue in self._issues_of(scope, activity.activity_id)
        ):
            return
        facts = MatchFacts.of(self._effective_values(scope, activity))
        match = self._best_match(scope, facts, exclude=activity.activity_id)
        if match is not None and match.decision is not MatchDecision.DISTINCT:
            self._open_review_issue(scope, activity, match, self._clock())
            return
        self._stores.activities.save(
            scope,
            [ActivityChange(replace(activity, status=ActivityStatus.ACTIVE), activity.version)],
        )

    # ------------------------------------------------------------------ reads

    def effective(self, scope: OwnerScope, activity_id: UUID) -> EffectiveActivity:
        return self._effective(scope, self._stores.activities.get(scope, activity_id))

    def issues(self, scope: OwnerScope, *, open_only: bool = True) -> list[DataQualityIssue]:
        return [
            issue for issue in self._stores.issues.list_all(scope) if issue.is_open or not open_only
        ]

    def _effective_values(self, scope: OwnerScope, activity: Activity) -> dict[str, object]:
        overrides = self._stores.overrides.for_entity(
            scope, EntityType.ACTIVITY, activity.activity_id
        )
        return dict(apply_overrides(activity, overrides).values)

    def _effective(self, scope: OwnerScope, activity: Activity) -> EffectiveActivity:
        overrides = tuple(
            self._stores.overrides.for_entity(scope, EntityType.ACTIVITY, activity.activity_id)
        )
        applied = apply_overrides(activity, overrides)
        values = dict(applied.values)
        subject = MetricSubject.activity(activity.activity_id)
        for binding in self._bindings:
            if binding.target is None:
                continue
            current = self._metrics.current(scope, binding.definition.metric_id, subject)
            values[binding.target] = None if current is None else current.value
        normalized_detected = set(
            detect_anomalies(activity.values, imported_at=activity.imported_at)
        )
        from_sources = [
            anomaly for anomaly in activity.quality.anomalies if anomaly not in normalized_detected
        ]
        quality = assess(values, imported_at=activity.imported_at, source_anomalies=from_sources)
        view = replace(activity, values=values, provenance=applied.provenance, quality=quality)
        return EffectiveActivity(activity, view, overrides)

    # ------------------------------------------------------------------ corrections

    def correct(
        self,
        scope: OwnerScope,
        principal: Principal,
        activity_id: UUID,
        field: str,
        value: object,
        reason: str,
    ) -> Correction:
        """Append a correction of one field (FR-ACT-006, AT-RAW-001).

        Athlete text (the reason, corrected names and descriptions) is screened for disclosed
        secrets before it is stored (SEC-SEC-005).
        """
        self._consents.require(scope, ConsentType.ACTIVITIES)
        activity = self._live(scope, activity_id)
        screened_reason = self._screen.screen(reason, source="activity_correction")
        reason = screened_reason.text
        notice = screened_reason.notice
        if isinstance(value, str) and field_spec(field).kind is FieldKind.TEXT:
            screened_value = self._screen.screen(value, source="activity_correction")
            value = screened_value.text
            notice = notice or screened_value.notice
        existing = self._stores.overrides.for_entity(scope, EntityType.ACTIVITY, activity_id)
        current = current_overrides(existing).get(field)
        if field == SUBTYPE_FIELD:
            effective_sport = sport_of(apply_overrides(activity, existing).values)
            check_subtype(effective_sport, None if value is None else str(value))
        override = DataOverride(
            override_id=self._ids(),
            athlete_id=scope.athlete_id,
            entity_type=EntityType.ACTIVITY,
            entity_id=activity_id,
            field=field,
            original_value=activity.values.get(field),
            corrected_value=value,
            reason=reason,
            corrected_at=self._clock(),
            corrected_by=principal.user_id,
            supersedes_override_id=None if current is None else current.override_id,
        )
        stored = self._stores.overrides.add(scope, override)
        self._record(scope, "activity.corrected", "DATA_OVERRIDE", stored.override_id)
        self._events.emit(EventName.ACTIVITY_CORRECTED, {"field": field})
        self._after_change(scope, activity)
        return Correction(stored, notice)

    def confirm_match(self, scope: OwnerScope, issue_id: UUID) -> Activity:
        """Merge a pending activity into its candidate (deduplication spec section 5)."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        issue = self._ambiguous_issue(scope, issue_id)
        related = issue.related_activity_id
        if related is None:
            raise InvalidOperationError("issue names no candidate")
        if not issue.is_open:
            if issue.resolution is IssueResolution.MERGED:
                return self._stores.activities.get(scope, related)
            raise InvalidOperationError("the match was already resolved")
        pending = self._stores.activities.get(scope, _activity_of(issue))
        target = self._live(scope, related)
        now = self._clock()
        if not (pending.status is ActivityStatus.MERGED and pending.merged_into == related):
            if pending.status is not ActivityStatus.PENDING_REVIEW:
                raise InvalidOperationError("only pending activities can be merged")
            moved = tuple(
                replace(
                    link,
                    link_kind=LinkKind.ENRICHMENT,
                    linked_at=now,
                    match_score=issue.match_score,
                    dedup_rules_version=issue.dedup_rules_version,
                )
                for link in pending.sources
            )
            tombstone = replace(pending, status=ActivityStatus.MERGED, merged_into=related)
            merged = self._rebuild(scope, target, (*target.sources, *moved))
            pending, target = self._stores.activities.save(
                scope,
                [
                    ActivityChange(tombstone, pending.version),
                    ActivityChange(merged, target.version),
                ],
            )
        self._close(scope, issue, IssueResolution.MERGED, now)
        self._record(scope, "dedup.merged", "ACTIVITY", related)
        self._events.emit(EventName.DEDUP_MERGED, {"sources": str(len(target.sources))})
        self._after_change(scope, pending)
        self._after_change(scope, target)
        return target

    def keep_separate(self, scope: OwnerScope, issue_id: UUID) -> Activity:
        """The athlete rejects an ambiguous match: the pending activity becomes ACTIVE."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        issue = self._ambiguous_issue(scope, issue_id)
        pending = self._stores.activities.get(scope, _activity_of(issue))
        if not issue.is_open:
            if issue.resolution is IssueResolution.KEPT_SEPARATE:
                return pending
            raise InvalidOperationError("the match was already resolved")
        if pending.status is ActivityStatus.PENDING_REVIEW:
            (pending,) = self._stores.activities.save(
                scope,
                [ActivityChange(replace(pending, status=ActivityStatus.ACTIVE), pending.version)],
            )
        elif pending.status is not ActivityStatus.ACTIVE:
            raise InvalidOperationError("only pending activities can be kept separate")
        self._close(scope, issue, IssueResolution.KEPT_SEPARATE, self._clock())
        self._record(scope, "dedup.kept_separate", "ACTIVITY", pending.activity_id)
        self._events.emit(EventName.DEDUP_KEPT_SEPARATE, {"sport": pending.sport.value})
        self._after_change(scope, pending)
        return pending

    def split(self, scope: OwnerScope, activity_id: UUID, key: SourceKey) -> Activity:
        """Move one source of an activity into its own ACTIVE activity."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        activity = self._live(scope, activity_id)
        link = next((item for item in activity.sources if item.key == key), None)
        if link is None:
            raise NotFoundError
        if len(activity.sources) < 2:
            raise InvalidOperationError("an activity with one source cannot be split")
        remaining = [item for item in activity.sources if item.key != key]
        record = (link.provider, link.provider_activity_id)
        if link.provider_activity_id is not None and any(
            (item.provider, item.provider_activity_id) == record for item in remaining
        ):
            raise InvalidOperationError("payloads of one provider record stay together")
        if link.link_kind is LinkKind.PRIMARY:
            heir = min(remaining, key=lambda item: (item.linked_at, str(item.source_id)))
            remaining = [
                replace(item, link_kind=LinkKind.PRIMARY) if item is heir else item
                for item in remaining
            ]
        now = self._clock()
        alone = replace(
            link,
            link_kind=LinkKind.PRIMARY,
            linked_at=now,
            match_score=None,
            dedup_rules_version=None,
        )
        separated = build_activity(
            activity_id=self._ids(),
            status=ActivityStatus.ACTIVE,
            links=(alone,),
            observations=(self._stores.observations.get(scope, key),),
            imported_at=now,
            athlete_timezone=self._timezone_of(scope),
        )
        rebuilt = self._rebuild(scope, activity, remaining)
        activity, separated = self._stores.activities.save(
            scope, [ActivityChange(rebuilt, activity.version), ActivityChange(separated)]
        )
        self._record(scope, "dedup.split", "ACTIVITY", separated.activity_id)
        self._events.emit(EventName.DEDUP_SPLIT, {"sport": separated.sport.value})
        self._after_change(scope, activity)
        self._after_change(scope, separated)
        return separated

    def acknowledge(self, scope: OwnerScope, issue_id: UUID) -> DataQualityIssue:
        """The athlete accepts flagged data as it is, or has seen a failed import."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        issue = self._stores.issues.get(scope, issue_id)
        if issue.kind is IssueKind.AMBIGUOUS_MATCH:
            raise InvalidOperationError("ambiguous matches are merged or kept separate")
        if not issue.is_open:
            return issue
        return self._close(scope, issue, IssueResolution.ACKNOWLEDGED, self._clock())

    # ------------------------------------------------------------------ follow-up work

    def _after_change(self, scope: OwnerScope, activity: Activity) -> None:
        current = self._stores.activities.get(scope, activity.activity_id)
        view = self._effective(scope, current).activity
        if current.is_live:
            self._recompute_metrics(scope, view)
        self._sync_anomaly_issues(scope, view)

    def _recompute_metrics(self, scope: OwnerScope, view: Activity) -> None:
        anomalous_fields = {anomaly.field for anomaly in view.quality.anomalies}
        for binding in self._bindings:
            inputs = {}
            for name, path in binding.inputs:
                spec = next(
                    item for item in binding.definition.required_inputs if item.name == name
                )
                value = view.number(path)
                reasons: set[UncertaintyReason] = set()
                if path in anomalous_fields:
                    reasons.add(UncertaintyReason.SENSOR_QUALITY)
                if value is not None:
                    try:
                        spec.check(value)
                    except DomainValidationError:
                        value = None
                        reasons.add(UncertaintyReason.SENSOR_QUALITY)
                inputs[name] = MetricInput(
                    value,
                    EvidenceRef(EvidenceKind.ACTIVITY, view.activity_id, field=path),
                    view.quality.confidence,
                    frozenset(reasons),
                )
            self._metrics.compute(
                scope,
                binding.definition.metric_id,
                MetricSubject.activity(view.activity_id),
                inputs,
            )

    def _sync_anomaly_issues(self, scope: OwnerScope, view: Activity) -> None:
        detected = (
            {(anomaly.code, anomaly.field): anomaly for anomaly in view.quality.anomalies}
            if view.is_live
            else {}
        )
        anomaly_issues = [
            issue
            for issue in self._issues_of(scope, view.activity_id)
            if issue.kind is IssueKind.ANOMALY
        ]
        known = {
            (issue.code, issue.field)
            for issue in anomaly_issues
            if issue.is_open or issue.resolution is IssueResolution.ACKNOWLEDGED
        }
        now = self._clock()
        for key, anomaly in detected.items():
            if key in known:
                continue
            self._stores.issues.add(
                scope,
                DataQualityIssue.for_anomaly(
                    issue_id=self._ids(),
                    athlete_id=scope.athlete_id,
                    activity_id=view.activity_id,
                    anomaly=anomaly,
                    created_at=now,
                ),
            )
            self._events.emit(EventName.QUALITY_ISSUE_RAISED, {"code": str(anomaly.code)})
        for issue in anomaly_issues:
            if issue.is_open and (issue.code, issue.field) not in detected:
                self._close(scope, issue, IssueResolution.NO_LONGER_DETECTED, now)

    # ------------------------------------------------------------------ helpers

    def _live(self, scope: OwnerScope, activity_id: UUID) -> Activity:
        activity = self._stores.activities.get(scope, activity_id)
        if not activity.is_live:
            raise InvalidOperationError("merged activities change through their target")
        return activity

    def _issues_of(self, scope: OwnerScope, activity_id: UUID) -> Iterable[DataQualityIssue]:
        return [
            issue
            for issue in self._stores.issues.list_all(scope)
            if issue.activity_id == activity_id
        ]

    def _ambiguous_issue(self, scope: OwnerScope, issue_id: UUID) -> DataQualityIssue:
        issue = self._stores.issues.get(scope, issue_id)
        if issue.kind is not IssueKind.AMBIGUOUS_MATCH:
            raise InvalidOperationError("not an ambiguous match")
        return issue

    def _close(
        self,
        scope: OwnerScope,
        issue: DataQualityIssue,
        resolution: IssueResolution,
        now: datetime,
    ) -> DataQualityIssue:
        closed = self._stores.issues.update(
            scope, issue.close(resolution, at=now), expected_version=issue.version
        )
        self._events.emit(
            EventName.QUALITY_ISSUE_RESOLVED,
            {"code": issue.code, "resolution": resolution.value},
        )
        return closed

    def _record(self, scope: OwnerScope, action: str, target_type: str, target_id: UUID) -> None:
        self._audit.append(
            AuditEvent(
                occurred_at=self._clock(),
                actor=ActorKind.USER,
                action=action,
                outcome=AuditOutcome.SUCCESS,
                athlete_id=scope.athlete_id,
                target_type=target_type,
                target_id=str(target_id),
            )
        )


def _activity_of(issue: DataQualityIssue) -> UUID:
    if issue.activity_id is None:
        raise InvalidOperationError("issue names no activity")
    return issue.activity_id


def _score(match: Match) -> str:
    return f"{match.score:.4f}"
