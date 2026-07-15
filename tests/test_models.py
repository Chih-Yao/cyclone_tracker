from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from cyclone_tracker.config import SOURCE_NAMES_ZH_TW
from cyclone_tracker.models import (
    CycleData,
    CycleSummary,
    Manifest,
    MeanPoint,
    MeanTrack,
    MemberTrack,
    SourceState,
    Storm,
    TrackPoint,
    cycle_to_json_bytes,
    is_supported_storm,
    manifest_to_json_bytes,
)


def track_point(tau_h: int = 0) -> TrackPoint:
    return TrackPoint(
        tau_h=tau_h,
        valid_at=datetime(2026, 7, 15, tau_h, tzinfo=UTC),
        lat=15.0,
        lon=130.0,
        wind_kt=50.0,
        wind_source_value=50.0,
        wind_source_unit="kt",
        pressure_hpa=980.0,
    )


def test_supported_wp_numbers_include_named_and_invest_ranges() -> None:
    assert is_supported_storm("WP", 1)
    assert is_supported_storm("WP", 49)
    assert is_supported_storm("WP", 90)
    assert is_supported_storm("WP", 99)
    assert not is_supported_storm("WP", 50)
    assert not is_supported_storm("EP", 9)


def test_track_point_preserves_source_wind_and_serializes_utc() -> None:
    point = TrackPoint(
        tau_h=6,
        valid_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
        lat=15.2,
        lon=181.0,
        wind_kt=100.0,
        wind_source_value=51.4444444444,
        wind_source_unit="m/s",
        pressure_hpa=965.0,
    )
    assert point.lon == -179.0
    assert point.model_dump(mode="json")["valid_at"] == "2026-07-15T06:00:00Z"


def test_track_point_rejects_non_finite_or_negative_values() -> None:
    with pytest.raises(ValidationError):
        TrackPoint(
            tau_h=-6,
            valid_at=datetime(2026, 7, 15, tzinfo=UTC),
            lat=float("nan"),
            lon=120.0,
            wind_kt=-1.0,
            wind_source_value=-1.0,
            wind_source_unit="kt",
        )


def test_track_point_rejects_negative_tau() -> None:
    with pytest.raises(ValidationError):
        TrackPoint(
            tau_h=-1,
            valid_at=datetime(2026, 7, 15, tzinfo=UTC),
            lat=15.0,
            lon=130.0,
            wind_kt=50.0,
            wind_source_value=50.0,
            wind_source_unit="kt",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lat", float("nan")),
        ("lat", float("inf")),
        ("lat", -90.1),
        ("lat", 90.1),
        ("lon", float("nan")),
        ("lon", float("inf")),
        ("wind_kt", float("nan")),
        ("wind_kt", -0.1),
        ("wind_source_value", float("inf")),
        ("wind_source_value", -0.1),
        ("pressure_hpa", float("nan")),
        ("pressure_hpa", 0.0),
        ("pressure_hpa", 1200.1),
    ],
)
def test_track_point_rejects_each_invalid_numeric_value(field: str, value: float) -> None:
    values = {
        "tau_h": 0,
        "valid_at": datetime(2026, 7, 15, tzinfo=UTC),
        "lat": 15.0,
        "lon": 130.0,
        "wind_kt": 50.0,
        "wind_source_value": 50.0,
        "wind_source_unit": "kt",
        "pressure_hpa": 980.0,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        TrackPoint(**values)


def test_track_point_requires_strict_types() -> None:
    with pytest.raises(ValidationError):
        TrackPoint(
            tau_h="6",
            valid_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
            lat=15.2,
            lon=130.0,
            wind_kt=100.0,
            wind_source_value=100.0,
            wind_source_unit="kt",
        )


@pytest.mark.parametrize(
    "valid_at",
    [
        datetime(2026, 7, 15),
        datetime(2026, 7, 15, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_track_point_requires_utc_datetime(valid_at: datetime) -> None:
    with pytest.raises(ValidationError):
        TrackPoint(
            tau_h=0,
            valid_at=valid_at,
            lat=15.0,
            lon=130.0,
            wind_kt=50.0,
            wind_source_value=50.0,
            wind_source_unit="kt",
        )


def test_member_track_requires_sorted_unique_tau_values() -> None:
    MemberTrack(id="p01", member_type="perturbed", points=[track_point(0), track_point(6)])

    with pytest.raises(ValidationError):
        MemberTrack(id="p01", member_type="perturbed", points=[track_point(6), track_point(0)])

    with pytest.raises(ValidationError):
        MemberTrack(id="p01", member_type="perturbed", points=[track_point(0), track_point(0)])


def test_mean_point_applies_numeric_longitude_and_datetime_validation() -> None:
    point = MeanPoint(
        tau_h=0,
        valid_at=datetime(2026, 7, 15, tzinfo=UTC),
        lat=15.0,
        lon=-181.0,
        wind_kt=None,
        pressure_hpa=None,
        member_count=0,
    )
    assert point.lon == 179.0

    with pytest.raises(ValidationError):
        MeanPoint(
            tau_h=0,
            valid_at=datetime(2026, 7, 15),
            lat=float("inf"),
            lon=130.0,
            wind_kt=-1.0,
            pressure_hpa=0.0,
            member_count=0,
        )


def test_mean_point_rejects_negative_tau() -> None:
    with pytest.raises(ValidationError):
        MeanPoint(
            tau_h=-1,
            valid_at=datetime(2026, 7, 15, tzinfo=UTC),
            lat=15.0,
            lon=130.0,
            wind_kt=None,
            pressure_hpa=None,
            member_count=0,
        )


@pytest.mark.parametrize("storm_id", ["01W", "49W", "90W", "99W"])
def test_storm_accepts_supported_wp_identifiers(storm_id: str) -> None:
    Storm(
        id=storm_id,
        name=None,
        basin="WP",
        invest=storm_id.startswith("9"),
        members=[],
        mean=MeanTrack(points=[]),
    )


@pytest.mark.parametrize("storm_id", ["00W", "50W", "89W", "100W", "01E", "1W"])
def test_storm_rejects_unsupported_identifiers(storm_id: str) -> None:
    with pytest.raises(ValidationError):
        Storm(
            id=storm_id,
            name=None,
            basin="WP",
            invest=False,
            members=[],
            mean=MeanTrack(points=[]),
        )


@pytest.mark.parametrize(
    ("model", "datetime_field"),
    [
        (
            CycleData(
                source_id="gefs",
                initialized_at=datetime(2026, 7, 15, tzinfo=UTC),
                storms=[],
            ),
            "initialized_at",
        ),
        (
            CycleSummary(
                id="2026071500",
                initialized_at=datetime(2026, 7, 15, tzinfo=UTC),
                href="gefs/2026071500.json",
                storms=[],
                empty=True,
            ),
            "initialized_at",
        ),
        (
            Manifest(generated_at=datetime(2026, 7, 15, tzinfo=UTC), sources=[]),
            "generated_at",
        ),
    ],
)
def test_top_level_models_reject_naive_datetimes(model: object, datetime_field: str) -> None:
    values = model.model_dump()
    values[datetime_field] = datetime(2026, 7, 15)

    with pytest.raises(ValidationError):
        type(model)(**values)


def test_source_state_has_required_relationships_and_defaults() -> None:
    source = SourceState(
        id="gefs",
        name_zh_tw="NCEP GEFS",
        attribution_url="https://www.nco.ncep.noaa.gov/",
        status="empty",
        last_success_cycle=None,
        cycles=[],
    )

    assert source.stale_after_hours == 12
    assert source.error_kind is None


def test_json_serializers_are_sorted_deterministic_utf8_with_final_newline() -> None:
    cycle = CycleData(
        source_id="gefs",
        initialized_at=datetime(2026, 7, 15, tzinfo=UTC),
        storms=[],
    )
    manifest = Manifest(generated_at=datetime(2026, 7, 15, tzinfo=UTC), sources=[])

    assert cycle_to_json_bytes(cycle) == (
        b'{"initialized_at":"2026-07-15T00:00:00Z","schema_version":1,'
        b'"source_id":"gefs","storms":[]}\n'
    )
    assert cycle_to_json_bytes(cycle) == cycle_to_json_bytes(cycle)
    assert manifest_to_json_bytes(manifest) == (
        b'{"generated_at":"2026-07-15T00:00:00Z","schema_version":1,"sources":[]}\n'
    )


def test_source_catalog_uses_exact_ids_and_labels() -> None:
    assert SOURCE_NAMES_ZH_TW == {
        "gefs": "NCEP GEFS",
        "aigefs": "NCEP AIGEFS",
        "aigfs": "NCEP AIGFS",
        "ifs-ens": "ECMWF IFS ENS",
        "aifs-ens": "ECMWF AIFS ENS",
    }
