from datetime import UTC, datetime, timedelta

from cyclone_tracker.mean import compute_mean_track
from cyclone_tracker.models import MemberTrack, TrackPoint

BASE_TIME = datetime(2026, 7, 15, tzinfo=UTC)


def point(
    tau_h: int,
    lat: float,
    lon: float,
    wind_kt: float,
    pressure_hpa: float | None,
) -> TrackPoint:
    return TrackPoint(
        tau_h=tau_h,
        valid_at=BASE_TIME + timedelta(hours=tau_h),
        lat=lat,
        lon=lon,
        wind_kt=wind_kt,
        wind_source_value=wind_kt,
        wind_source_unit="kt",
        pressure_hpa=pressure_hpa,
    )


def member(
    member_id: str,
    points: list[TrackPoint],
    member_type: str = "perturbed",
) -> MemberTrack:
    return MemberTrack(id=member_id, member_type=member_type, points=points)


def test_spherical_mean_crosses_dateline_without_jumping_to_greenwich() -> None:
    members = [
        member("AP01", [point(0, 15.0, 179.0, 80.0, 980.0)]),
        member("AP02", [point(0, 15.0, -179.0, 100.0, 960.0)]),
    ]

    mean = compute_mean_track(members)

    assert abs(abs(mean[0].lon) - 180.0) < 0.01
    assert mean[0].wind_kt == 90.0
    assert mean[0].pressure_hpa == 970.0
    assert mean[0].member_count == 2


def test_mean_excludes_source_mean_and_uses_available_tau_members() -> None:
    members = [
        member(
            "AC00",
            [point(0, 10.0, 130.0, 40.0, None), point(6, 11.0, 131.0, 45.0, 995.0)],
            "control",
        ),
        member("AP01", [point(0, 12.0, 132.0, 50.0, 990.0)], "perturbed"),
        member("AEMN", [point(0, 50.0, 10.0, 200.0, 800.0)], "source_mean"),
    ]

    mean = compute_mean_track(members)

    assert [item.tau_h for item in mean] == [0, 6]
    assert mean[0].member_count == 2
    assert mean[0].wind_kt == 45.0
    assert mean[0].pressure_hpa == 990.0
    assert mean[1].member_count == 1


def test_mean_omits_tau_with_near_zero_average_position_vector() -> None:
    members = [
        member(
            "AP01",
            [point(0, 0.0, 0.0, 40.0, 1000.0), point(6, 10.0, 130.0, 50.0, None)],
        ),
        member("AP02", [point(0, 0.0, 180.0, 60.0, 980.0)]),
    ]

    mean = compute_mean_track(members)

    assert [item.tau_h for item in mean] == [6]
    assert mean[0].valid_at == BASE_TIME + timedelta(hours=6)
    assert mean[0].pressure_hpa is None
