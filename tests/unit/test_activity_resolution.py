"""Field-level resolution with provenance (FR-ACT-005, FR-ACT-008)."""

import itertools
from datetime import timedelta
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coach.domain.activity.fields import (
    PowerSource,
    PowerSummary,
    ProviderMetric,
    StreamKind,
    StreamRef,
    Surface,
)
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.activity.resolution import current_versions, resolve
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.quality import Anomaly
from coach.domain.common.sport import Sport
from samples import (
    BUENOS_AIRES,
    FIT_SOURCE,
    GARMIN_SOURCE,
    MANUAL_SOURCE,
    NOW,
    OTHER_ATHLETE,
    SHA_A,
    SHA_B,
    SHA_C,
    SHA_D,
    START,
    sample_observation,
    tennis_values,
)

SECOND_FIT = UUID("0193b2a0-0000-7000-8000-000000000204")
GARMIN_EFFECT = ProviderMetric(
    3.4, "garmin", "aerobic training effect", MetricOrigin.PROVIDER_DERIVED
)


def _garmin(**updates: object) -> Observation:
    return sample_observation(
        SourceKind.PROVIDER_RECORD,
        updates={
            "distance.meters": 10000.0,
            "cardiovascular.averageHr": 150,
            "metadata.name": "Morning run",
            **updates,
        },
    )


def _fit(**updates: object) -> Observation:
    return sample_observation(updates={"distance.meters": 10050.5, **updates})


def _resolve(*sources: Observation) -> dict[str, object]:
    return dict(resolve(sources, athlete_timezone=BUENOS_AIRES).values)


@pytest.mark.req("FR-ACT-005", "FR-ACT-008")
@pytest.mark.acceptance("AT-ACT-001")
def test_totals_and_heart_rate_prefer_fit_and_are_never_summed() -> None:
    resolution = resolve((_garmin(), _fit()), athlete_timezone=BUENOS_AIRES)
    assert resolution.values["distance.meters"] == 10050.5
    assert resolution.values["cardiovascular.averageHr"] == 148
    distance = resolution.provenance["distance.meters"]
    assert (distance.source_id, distance.rule) == (FIT_SOURCE, "TOTALS_PREFER_FIT")
    assert resolution.provenance["cardiovascular.averageHr"].rule == "HR_PREFER_FIT"


@pytest.mark.req("FR-ACT-005")
@pytest.mark.acceptance("AT-ACT-001")
def test_provider_record_wins_provider_metrics_and_metadata() -> None:
    garmin = _garmin(**{"training.aerobicEffect": GARMIN_EFFECT, "metadata.name": "Garmin name"})
    fit = _fit(
        **{
            "training.aerobicEffect": ProviderMetric(
                3.1, "garmin", "aerobic training effect", MetricOrigin.PROVIDER_DERIVED
            ),
            "metadata.name": "FIT name",
        }
    )
    resolution = resolve((fit, garmin), athlete_timezone=BUENOS_AIRES)
    assert resolution.values["training.aerobicEffect"] == GARMIN_EFFECT
    assert resolution.values["metadata.name"] == "Garmin name"
    assert resolution.provenance["training.aerobicEffect"].source_id == GARMIN_SOURCE
    assert resolution.provenance["metadata.name"].rule == "METADATA_PREFER_PROVIDER"


@pytest.mark.req("FR-ACT-005")
def test_preferred_source_falls_back_when_its_value_is_unknown() -> None:
    values = _resolve(_garmin(), _fit(**{"cardiovascular.averageHr": None}))
    assert values["cardiovascular.averageHr"] == 150
    all_unknown = resolve(
        (_garmin(**{"elevation.gainMeters": None}), _fit(**{"elevation.gainMeters": None})),
        athlete_timezone=BUENOS_AIRES,
    )
    assert all_unknown.values["elevation.gainMeters"] is None
    assert all_unknown.provenance["elevation.gainMeters"].source_id == FIT_SOURCE


@pytest.mark.req("FR-ACT-005", "FR-PRF-002")
def test_fields_no_source_provides_stay_absent_or_unknown_when_required() -> None:
    values = _resolve(sample_observation(values=tennis_values()))
    assert "performance.cadence" not in values
    assert "elevation.gainMeters" not in values
    assert values["distance.meters"] is None
    assert values["timing.movingSeconds"] is None


@pytest.mark.req("FR-ACT-005", "FR-ACT-011")
def test_subjective_fields_come_only_from_the_athlete() -> None:
    entry = sample_observation(
        SourceKind.ATHLETE_ENTRY,
        values=tennis_values(**{"subjective.rpe": 7.0, "environment.surface": Surface.CLAY}),
    )
    garmin = sample_observation(
        SourceKind.PROVIDER_RECORD,
        values=tennis_values(
            **{"environment.surface": Surface.HARD, "timing.elapsedSeconds": 5300.0}
        ),
    )
    resolution = resolve((garmin, entry), athlete_timezone=BUENOS_AIRES)
    assert resolution.values["subjective.rpe"] == 7.0
    assert resolution.provenance["subjective.rpe"].source_id == MANUAL_SOURCE
    assert resolution.provenance["subjective.rpe"].rule == "SUBJECTIVE_ATHLETE_ONLY"
    assert resolution.values["environment.surface"] is Surface.CLAY
    assert resolution.values["timing.elapsedSeconds"] == 5300.0


@pytest.mark.req("FR-ACT-005", "FR-ACT-007")
def test_specific_sport_wins_over_other_with_its_subtype() -> None:
    generic = sample_observation(
        SourceKind.PROVIDER_RECORD,
        values={
            **tennis_values(),
            "classification.sport": Sport.OTHER,
            "classification.subtype": None,
        },
    )
    tennis = sample_observation(values=tennis_values(**{"classification.subtype": "PRACTICE"}))
    resolution = resolve((generic, tennis), athlete_timezone=BUENOS_AIRES)
    assert resolution.values["classification.sport"] is Sport.TENNIS
    assert resolution.values["classification.subtype"] == "PRACTICE"
    assert resolution.provenance["classification.sport"].rule == "SPORT_MOST_SPECIFIC"


@pytest.mark.req("FR-ACT-005")
def test_ties_within_a_source_kind_go_to_the_lowest_raw_digest() -> None:
    low = sample_observation(sha256=SHA_A, updates={"distance.meters": 10010.0})
    high = sample_observation(
        raw_source_id=SECOND_FIT, sha256=SHA_D, updates={"distance.meters": 10090.0}
    )
    assert _resolve(low, high)["distance.meters"] == 10010.0
    assert _resolve(high, low)["distance.meters"] == 10010.0


@pytest.mark.req("FR-ACT-005", "FR-ACT-007")
def test_latest_payload_of_a_provider_record_replaces_the_older_one() -> None:
    old = _garmin(**{"metadata.name": "Run"})
    new = sample_observation(
        SourceKind.PROVIDER_RECORD,
        raw_source_id=UUID("0193b2a0-0000-7000-8000-000000000205"),
        sha256=SHA_D,
        received_at=NOW + timedelta(days=1),
        updates={"metadata.name": "Renamed run"},
    )
    assert current_versions((old, new)) == [new]
    assert _resolve(new, old)["metadata.name"] == "Renamed run"


@pytest.mark.req("FR-ACT-003", "FR-ACT-004")
def test_local_time_and_training_day_have_provenance() -> None:
    resolution = resolve((_fit(),), athlete_timezone=BUENOS_AIRES)
    values, provenance = resolution.values, resolution.provenance
    assert values["timing.originalLocalTime"] == START.replace(tzinfo=None) - timedelta(hours=3)
    assert provenance["timing.originalLocalTime"].rule == "LOCAL_TIME_FROM_OFFSET"
    assert provenance["timing.trainingDay"].rule == "TRAINING_DAY_ACTIVITY_TIME"
    zone_only = resolve(
        (_fit(**{"timing.originalTimezone": "Europe/Madrid"}),),
        athlete_timezone=BUENOS_AIRES,
    )
    assert zone_only.values["timing.utcOffset"] == "-03:00"
    from_zone = resolve(
        (
            sample_observation(
                updates={"timing.originalTimezone": "Europe/Madrid"}, without=("timing.utcOffset",)
            ),
        ),
        athlete_timezone=BUENOS_AIRES,
    )
    assert from_zone.values["timing.utcOffset"] == "+02:00"
    assert from_zone.provenance["timing.utcOffset"].rule == "OFFSET_FROM_TIMEZONE"


@pytest.mark.req("FR-ACT-005")
def test_pace_is_derived_from_speed_for_running_and_swimming_only() -> None:
    run = resolve((_fit(),), athlete_timezone=BUENOS_AIRES)
    assert run.values["performance.averagePaceSecondsPerKm"] == pytest.approx(1000 / 2.753)
    assert run.provenance["performance.averagePaceSecondsPerKm"].rule == "PACE_FROM_SPEED"
    assert "performance.averagePaceSecondsPer100m" not in run.values
    swim = _resolve(
        _fit(
            **{
                "classification.sport": Sport.SWIMMING,
                "classification.subtype": "POOL",
                "performance.averageSpeedMps": 0.8,
            }
        )
    )
    assert swim["performance.averagePaceSecondsPer100m"] == pytest.approx(125.0)
    ride = _resolve(
        _fit(**{"classification.sport": Sport.CYCLING, "classification.subtype": "ROAD"})
    )
    assert "performance.averagePaceSecondsPerKm" not in ride
    stopped = _resolve(_fit(**{"performance.averageSpeedMps": 0.0}))
    assert stopped["performance.averagePaceSecondsPerKm"] is None


@pytest.mark.req("FR-ACT-005")
def test_streams_prefer_fit_per_kind_and_anomalies_are_collected() -> None:
    jump = Anomaly("DISTANCE_GPS_JUMP", "distance.meters", "2 segments above 12 m/s")
    fit = sample_observation(streams=(StreamRef(StreamKind.TIME_SERIES, SHA_C),), anomalies=(jump,))
    garmin = sample_observation(
        SourceKind.PROVIDER_RECORD,
        streams=(StreamRef(StreamKind.TIME_SERIES, SHA_D), StreamRef(StreamKind.LAPS, SHA_A)),
    )
    resolution = resolve((garmin, fit), athlete_timezone=BUENOS_AIRES)
    assert resolution.streams == (
        StreamRef(StreamKind.TIME_SERIES, SHA_C),
        StreamRef(StreamKind.LAPS, SHA_A),
    )
    assert resolution.provenance["streams.timeSeries"].source_id == FIT_SOURCE
    assert resolution.provenance["streams.laps"].source_id == GARMIN_SOURCE
    assert resolution.anomalies == (jump,)


@pytest.mark.req("FR-ACT-005", "SEC-IDOR-001")
def test_resolution_refuses_mixed_athletes_duplicates_and_nothing() -> None:
    with pytest.raises(DomainValidationError):
        resolve((), athlete_timezone=BUENOS_AIRES)
    with pytest.raises(DomainValidationError):
        resolve(
            (_fit(), sample_observation(SourceKind.PROVIDER_RECORD, athlete_id=OTHER_ATHLETE)),
            athlete_timezone=BUENOS_AIRES,
        )
    with pytest.raises(DomainValidationError):
        resolve((_fit(), sample_observation(part=1, sha256=SHA_B)), athlete_timezone=BUENOS_AIRES)


@pytest.mark.req("FR-ACT-005")
def test_power_summary_is_one_field_from_one_source() -> None:
    fit = _fit(**{"performance.power": PowerSummary(PowerSource.MEASURED, 210.0, 480.0)})
    garmin = _garmin(**{"performance.power": PowerSummary(PowerSource.ESTIMATED, 205.0, None)})
    assert _resolve(garmin, fit)["performance.power"] == PowerSummary(
        PowerSource.MEASURED, 210.0, 480.0
    )


_DISTANCES = st.floats(min_value=0, max_value=50_000, allow_nan=False)
_KINDS = st.sampled_from(list(SourceKind))


@st.composite
def _observation_sets(draw: st.DrawFn) -> list[Observation]:
    kinds = draw(st.lists(_KINDS, min_size=1, max_size=4))
    observations = []
    for index, kind in enumerate(kinds):
        observations.append(
            sample_observation(
                kind,
                raw_source_id=UUID(int=index + 1),
                sha256=f"{index:x}" * 64 if index < 10 else SHA_A,
                provider_activity_id=f"record-{index}",
                updates={
                    "distance.meters": draw(st.one_of(st.none(), _DISTANCES)),
                    "cardiovascular.averageHr": draw(st.one_of(st.none(), st.integers(60, 200))),
                    "timing.startTimeUtc": START + timedelta(seconds=draw(st.integers(0, 120))),
                },
            )
        )
    return observations


@pytest.mark.req("FR-ACT-005", "FR-ACT-007", "FR-ACT-008")
@settings(max_examples=60, deadline=None)
@given(observations=_observation_sets(), data=st.data())
def test_resolution_is_independent_of_arrival_order(
    observations: list[Observation], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(observations))
    first = resolve(observations, athlete_timezone=BUENOS_AIRES)
    second = resolve(shuffled, athlete_timezone=BUENOS_AIRES)
    assert first == second
    distances = {source.values["distance.meters"] for source in observations}
    assert first.values["distance.meters"] in distances


@pytest.mark.req("FR-ACT-008")
def test_totals_are_one_of_the_sources_values_for_every_pairing() -> None:
    sources = (
        _garmin(),
        _fit(),
        sample_observation(
            SourceKind.OTHER_FILE,
            raw_source_id=SECOND_FIT,
            sha256=SHA_C,
            updates={"distance.meters": 9990.0},
        ),
    )
    for pair in itertools.permutations(sources, 2):
        value = _resolve(*pair)["distance.meters"]
        assert value in {source.values["distance.meters"] for source in pair}
