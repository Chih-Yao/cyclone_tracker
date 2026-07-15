from collections import defaultdict
from math import atan2, cos, degrees, hypot, radians, sin, sqrt
from statistics import fmean

from cyclone_tracker.models import MeanPoint, MemberTrack, TrackPoint


def _mean_at_tau(tau_h: int, points: list[TrackPoint]) -> MeanPoint:
    x_values: list[float] = []
    y_values: list[float] = []
    z_values: list[float] = []
    for point in points:
        lat = radians(point.lat)
        lon = radians(point.lon)
        x_values.append(cos(lat) * cos(lon))
        y_values.append(cos(lat) * sin(lon))
        z_values.append(sin(lat))

    mean_x = fmean(x_values)
    mean_y = fmean(y_values)
    mean_z = fmean(z_values)
    if sqrt(mean_x**2 + mean_y**2 + mean_z**2) < 1e-12:
        raise ValueError("mean position vector magnitude is below 1e-12")

    pressure_values = [point.pressure_hpa for point in points if point.pressure_hpa is not None]
    return MeanPoint(
        tau_h=tau_h,
        valid_at=points[0].valid_at,
        lat=degrees(atan2(mean_z, hypot(mean_x, mean_y))),
        lon=degrees(atan2(mean_y, mean_x)),
        wind_kt=fmean(point.wind_kt for point in points),
        pressure_hpa=fmean(pressure_values) if pressure_values else None,
        member_count=len(points),
    )


def compute_mean_track(members: list[MemberTrack]) -> list[MeanPoint]:
    grouped: dict[int, list[TrackPoint]] = defaultdict(list)
    for member in members:
        if member.member_type == "source_mean":
            continue
        for point in member.points:
            grouped[point.tau_h].append(point)

    mean_points: list[MeanPoint] = []
    for tau_h in sorted(grouped):
        try:
            mean_points.append(_mean_at_tau(tau_h, grouped[tau_h]))
        except ValueError:
            continue
    return mean_points
