"""Deduplication properties (FR-ACT-007, FR-ACT-008, NFR-REL-001; deduplication spec section 6).

Sessions are generated as clusters of sources that agree within the full-score tolerances
(start within 60 s, duration and distance within 2 %), so every decision is decisive; distinct
sessions start 20 minutes apart.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from activity_world import SCOPE, World
from coach.domain.activity.activity import Activity
from coach.domain.activity.observation import Observation, SourceKind
from samples import START, sample_observation

_KINDS = (SourceKind.FIT_FILE, SourceKind.PROVIDER_RECORD, SourceKind.OTHER_FILE)
_CLOSE = st.floats(0.995, 1.005)


@st.composite
def _sessions(draw: st.DrawFn, *, max_sessions: int = 3) -> list[list[Observation]]:
    """Clusters of observations, one cluster per logical session."""
    sessions = []
    serial = 0
    for index in range(draw(st.integers(1, max_sessions))):
        start = START + timedelta(minutes=20 * index)
        duration = draw(st.floats(600, 14_400))
        distance = duration * draw(st.floats(1.0, 4.0))
        cluster = []
        for kind in draw(st.lists(st.sampled_from(_KINDS), min_size=1, max_size=3)):
            serial += 1
            cluster.append(
                sample_observation(
                    kind,
                    raw_source_id=UUID(int=serial),
                    sha256=f"{serial:064x}",
                    provider_activity_id=f"record-{serial}",
                    updates={
                        "timing.startTimeUtc": start + timedelta(seconds=draw(st.integers(0, 50))),
                        "distance.meters": distance * draw(_CLOSE),
                        "timing.elapsedSeconds": duration * draw(_CLOSE),
                        "timing.movingSeconds": None,
                    },
                )
            )
        sessions.append(cluster)
    return sessions


def _summary(world: World) -> list[tuple[object, ...]]:
    """Logical activities without arrival-dependent identifiers, ordered by start."""
    activities: list[Activity] = sorted(world.live(), key=lambda item: item.start_time_utc)
    return [
        (
            dict(activity.values),
            dict(activity.provenance),
            frozenset(link.key for link in activity.sources),
            activity.quality,
        )
        for activity in activities
    ]


@pytest.mark.req("FR-ACT-007", "NFR-REL-001")
@pytest.mark.acceptance("AT-ACT-001")
@settings(max_examples=40, deadline=None)
@given(sessions=_sessions(), data=st.data())
def test_importing_twice_equals_importing_once(
    sessions: list[list[Observation]], data: st.DataObject
) -> None:
    observations = [item for cluster in sessions for item in cluster]
    once, twice = World(), World()
    for observation in observations:
        once.ingest(observation)
        twice.ingest(observation)
    for observation in data.draw(st.permutations(observations)):
        twice.service.ingest(SCOPE, observation)
    assert _summary(once) == _summary(twice)
    assert once.volume() == twice.volume()


@pytest.mark.req("FR-ACT-005", "FR-ACT-007", "FR-ACT-008")
@pytest.mark.acceptance("AT-ACT-001")
@settings(max_examples=40, deadline=None)
@given(sessions=_sessions(), data=st.data())
def test_arrival_order_does_not_change_activities_or_totals(
    sessions: list[list[Observation]], data: st.DataObject
) -> None:
    observations = [item for cluster in sessions for item in cluster]
    first, second = World(), World()
    for observation in observations:
        first.ingest(observation)
    for observation in data.draw(st.permutations(observations)):
        second.ingest(observation)
    assert len(first.live()) == len(sessions)
    assert _summary(first) == _summary(second)
    assert first.volume() == second.volume()


@pytest.mark.req("FR-ACT-008")
@pytest.mark.acceptance("AT-ACT-001")
@settings(max_examples=40, deadline=None)
@given(sessions=_sessions(max_sessions=1))
def test_enrichment_never_adds_a_sources_totals(sessions: list[list[Observation]]) -> None:
    (cluster,) = sessions
    world = World()
    for observation in cluster:
        world.ingest(observation)
    (week,) = world.volume().values()
    assert week.activities == 1
    assert week.distance_meters in {source.values["distance.meters"] for source in cluster}
    assert week.duration_seconds in {source.values["timing.elapsedSeconds"] for source in cluster}


@pytest.mark.req("FR-ACT-008")
@pytest.mark.acceptance("AT-ACT-001")
@settings(max_examples=20, deadline=None)
@given(sessions=_sessions(max_sessions=1), copies=st.integers(1, 4))
def test_agreeing_sources_leave_weekly_volume_unchanged(
    sessions: list[list[Observation]], copies: int
) -> None:
    template = sessions[0][0]
    world = World()
    volumes = []
    for serial in range(copies + 1):
        world.ingest(
            sample_observation(
                _KINDS[serial % len(_KINDS)],
                raw_source_id=UUID(int=100 + serial),
                sha256=f"{100 + serial:064x}",
                provider_activity_id=f"copy-{serial}",
                values=dict(template.values),
            )
        )
        volumes.append(world.volume())
    assert all(volume == volumes[0] for volume in volumes)
    (week,) = volumes[0].values()
    assert (week.activities, week.distance_meters) == (1, template.values["distance.meters"])
