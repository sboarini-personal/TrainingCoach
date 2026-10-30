"""Strength sessions: the athlete's set entries and the executed sets of an activity
(strength spec section 5; FR-STR-005).

The executed sets are reconciled in the domain (``reconcile``) from the activity's SETS
stream, the one its EFFECTIVE view resolved (STREAMS_PREFER_FIT), and the athlete's
``StrengthSession``. Entries name catalogue exercises only; unknown text is never guessed
(the caller resolves names through the catalogue). Changes are audited with identifiers
only.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from coach.application.activities import ActivityService
from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.consent import ConsentGate
from coach.application.errors import InvalidOperationError, NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import AuditLog, Batch, Clock, EventSink, OwnedRepository
from coach.domain.activity.activity import Activity, ActivityStatus
from coach.domain.activity.fields import STREAM_KEYS, StreamKind
from coach.domain.common.sport import Sport
from coach.domain.consent.ledger import ConsentType
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.exercise import ExerciseCatalogue
from coach.domain.strength.session import (
    SetEntry,
    StrengthSession,
    StrengthSetResult,
    reconcile,
    session_id_of,
)
from coach.domain.strength.sets import WatchSets

TARGET_TYPE: Final = "STRENGTH_SESSION"
AUDIT_ACTION: Final = "strength.session_changed"
# A stream by kind and blob digest; None when it cannot be read (the watch's sets are then
# unknown, never invented).
type StreamReader = Callable[[OwnerScope, StreamKind, str], object | None]


@dataclass(frozen=True, slots=True)
class SessionSets:
    """The executed sets of one strength activity and what they were reconciled from."""

    activity: Activity  # the EFFECTIVE view
    session: StrengthSession | None
    watch: WatchSets | None
    sets: tuple[StrengthSetResult, ...]

    def watch_exercises(self) -> list[str]:
        """What the watch named, in order of first appearance (catalogue IDs or codes)."""
        found: list[str] = []
        for item in () if self.watch is None else self.watch.sets:
            label = item.exercise_id or item.provider_exercise
            if item.active and label is not None and label not in found:
                found.append(label)
        return found


class StrengthService:
    def __init__(
        self,
        sessions: OwnedRepository[StrengthSession],
        activities: ActivityService,
        *,
        consents: ConsentGate,
        streams: StreamReader,
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
        catalogue: ExerciseCatalogue = CATALOGUE,
        batch: Batch = nullcontext,
    ) -> None:
        self._sessions = sessions
        self._activities = activities
        self._consents = consents
        self._streams = streams
        self._audit = audit
        self._events = events
        self._clock = clock
        self.catalogue = catalogue
        self._batch = batch

    # ------------------------------------------------------------------ reads

    def sets(self, scope: OwnerScope, activity_id: UUID) -> SessionSets:
        self._consents.require(scope, ConsentType.ACTIVITIES)
        view = self._activities.effective(scope, activity_id).activity
        session = self._session(scope, activity_id)
        watch = self._watch(scope, view)
        return SessionSets(view, session, watch, reconcile(watch, session))

    # ------------------------------------------------------------------ commands

    def record(self, scope: OwnerScope, activity_id: UUID, entry: SetEntry) -> SessionSets:
        """Add or replace the athlete's entry for one set (same exercise and set number)."""
        self._known(entry.exercise_id)
        return self._change(scope, activity_id, "RECORDED", lambda s: s.record(entry))

    def remove(
        self, scope: OwnerScope, activity_id: UUID, exercise_id: str, set_number: int
    ) -> SessionSets:
        return self._change(
            scope, activity_id, "REMOVED", lambda s: s.remove(exercise_id, set_number)
        )

    def relabel(
        self, scope: OwnerScope, activity_id: UUID, watch_exercise: str, exercise_id: str | None
    ) -> SessionSets:
        """Name an exercise the watch recorded; ``None`` forgets the relabel. Only names the
        watch used in this session can be relabelled."""
        if exercise_id is not None:
            self._known(exercise_id)
        current = self.sets(scope, activity_id)
        if exercise_id is not None and watch_exercise not in current.watch_exercises():
            raise InvalidOperationError(f"the watch recorded no {watch_exercise} here")
        return self._change(
            scope, activity_id, "RELABELLED", lambda s: s.relabel(watch_exercise, exercise_id)
        )

    def use_watch_sets(self, scope: OwnerScope, activity_id: UUID, use: bool) -> SessionSets:
        change = "WATCH_SETS_USED" if use else "WATCH_SETS_IGNORED"
        return self._change(scope, activity_id, change, lambda s: s.watch_sets(use))

    # ------------------------------------------------------------------ helpers

    def _known(self, exercise_id: str) -> None:
        if exercise_id not in self.catalogue:
            raise InvalidOperationError(f"unknown exercise: {exercise_id}")

    def _change(
        self,
        scope: OwnerScope,
        activity_id: UUID,
        change: str,
        apply: Callable[[StrengthSession], StrengthSession],
    ) -> SessionSets:
        with self._batch():
            self._consents.require(scope, ConsentType.ACTIVITIES)
            view = self._activities.effective(scope, activity_id).activity
            if view.status is not ActivityStatus.ACTIVE:
                raise InvalidOperationError("sets are recorded on active activities only")
            if view.sport is not Sport.STRENGTH:
                raise InvalidOperationError("sets are recorded on strength activities only")
            stored = self._session(scope, activity_id)
            current = stored or StrengthSession.new(scope.athlete_id, activity_id)
            updated = apply(current)
            if stored is None:
                saved = self._sessions.add(scope, updated)
            else:
                saved = self._sessions.update(scope, updated, expected_version=stored.version)
            self._audit.append(
                AuditEvent(
                    occurred_at=self._clock(),
                    actor=ActorKind.USER,
                    action=AUDIT_ACTION,
                    outcome=AuditOutcome.SUCCESS,
                    athlete_id=scope.athlete_id,
                    target_type=TARGET_TYPE,
                    target_id=str(saved.session_id),
                )
            )
            self._events.emit(EventName.STRENGTH_SESSION_CHANGED, {"change": change})
        watch = self._watch(scope, view)
        return SessionSets(view, saved, watch, reconcile(watch, saved))

    def _session(self, scope: OwnerScope, activity_id: UUID) -> StrengthSession | None:
        try:
            return self._sessions.get(scope, session_id_of(activity_id))
        except NotFoundError:
            return None

    def _watch(self, scope: OwnerScope, view: Activity) -> WatchSets | None:
        """The SETS stream the EFFECTIVE view resolved; ``None`` when there is none or it
        cannot be read."""
        stream = next((item for item in view.streams if item.kind is StreamKind.SETS), None)
        if stream is None or view.provenance.get(STREAM_KEYS[StreamKind.SETS]) is None:
            return None
        content = self._streams(scope, StreamKind.SETS, stream.blob_sha256)
        return content if isinstance(content, WatchSets) else None
