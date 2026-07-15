from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from numbers import Real
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx

from cyclone_tracker.adapters.atcf import candidate_cycles
from cyclone_tracker.adapters.base import AdapterOutcome
from cyclone_tracker.mean import compute_mean_track
from cyclone_tracker.models import CycleData, MeanTrack, MemberTrack, Storm, TrackPoint

_ECMWF_ROOT = "https://data.ecmwf.int/forecasts"
_SOURCE_MODELS = {"ifs-ens": "ifs", "aifs-ens": "aifs-ens"}
_PERTURBED_MEMBERS = {(member, 4): "perturbed" for member in range(1, 51)}
_SOURCE_MEMBER_TYPES = {
    "ifs-ens": {**_PERTURBED_MEMBERS, (51, 0): "control"},
    "aifs-ens": {
        **_PERTURBED_MEMBERS,
        (51, 1): "control",
        (52, 0): "deterministic",
    },
}
_MEMBER_TYPE_ORDER = {"control": 0, "perturbed": 1, "deterministic": 2}
_STORM_IDENTIFIER = re.compile(r"^(\d{2})([A-Z])$")


@dataclass(frozen=True)
class BufrTrackRecord:
    storm_identifier: str
    ensemble_member_number: int
    ensemble_forecast_type: int
    tau_h: int
    lat: float
    lon: float
    wind_m_s: float
    pressure_pa: float | None
    storm_name: str | None


class EcmwfRecordValidationError(ValueError):
    pass


def _terminal_horizon(source_id: str, cycle: datetime) -> int:
    if source_id not in _SOURCE_MODELS:
        raise ValueError(f"unsupported ECMWF source: {source_id}")
    cycle_utc = cycle.astimezone(UTC)
    if cycle_utc.hour not in {0, 6, 12, 18}:
        raise ValueError("ECMWF cycle hour must be 00, 06, 12, or 18 UTC")
    return 144 if source_id == "ifs-ens" and cycle_utc.hour in {6, 18} else 360


def build_tf_url(source_id: str, cycle: datetime) -> str:
    try:
        model = _SOURCE_MODELS[source_id]
    except KeyError as error:
        raise ValueError(f"unsupported ECMWF source: {source_id}") from error

    cycle_utc = cycle.astimezone(UTC)
    terminal_step = _terminal_horizon(source_id, cycle_utc)
    return (
        f"{_ECMWF_ROOT}/{cycle_utc:%Y%m%d}/{cycle_utc:%H}z/{model}/0p25/enfo/"
        f"{cycle_utc:%Y%m%d%H}0000-{terminal_step}h-enfo-tf.bufr"
    )


def _parse_storm_identifier(value: str) -> tuple[str, int] | None:
    match = _STORM_IDENTIFIER.fullmatch(value.strip().upper())
    if match is None:
        return None
    return match.group(2), int(match.group(1))


def normalize_bufr_records(
    source_id: str,
    initialized_at: datetime,
    records: list[BufrTrackRecord],
) -> CycleData:
    if source_id not in _SOURCE_MODELS:
        raise ValueError(f"unsupported ECMWF source: {source_id}")
    initialized_at_utc = initialized_at.astimezone(UTC)
    terminal_horizon = _terminal_horizon(source_id, initialized_at_utc)
    grouped: dict[str, dict[tuple[int, str], dict[int, TrackPoint]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    names: dict[str, str | None] = {}

    for record in records:
        member_key = (record.ensemble_member_number, record.ensemble_forecast_type)
        member_type = _SOURCE_MEMBER_TYPES[source_id].get(member_key)
        if member_type is None:
            raise EcmwfRecordValidationError(
                "invalid ECMWF record: unsupported member/type combination "
                f"for {source_id}: {record.ensemble_member_number}/"
                f"{record.ensemble_forecast_type}"
            )
        if (
            not isinstance(record.tau_h, int)
            or record.tau_h < 0
            or record.tau_h % 6 != 0
            or record.tau_h > terminal_horizon
        ):
            raise EcmwfRecordValidationError(
                "invalid ECMWF record: tau_h must be 6-hour aligned between 0 and "
                f"{terminal_horizon}: {record.tau_h}"
            )

        parsed_identifier = _parse_storm_identifier(record.storm_identifier)
        if parsed_identifier is None:
            continue
        basin, number = parsed_identifier
        if basin != "W" or not (1 <= number <= 49 or 90 <= number <= 99):
            continue

        storm_id = f"{number:02d}W"
        grouped_member_key = (record.ensemble_member_number, member_type)
        pressure_hpa = record.pressure_pa / 100.0 if record.pressure_pa is not None else None
        point = TrackPoint(
            tau_h=record.tau_h,
            valid_at=initialized_at_utc + timedelta(hours=record.tau_h),
            lat=record.lat,
            lon=record.lon,
            wind_kt=record.wind_m_s * 3600.0 / 1852.0,
            wind_source_value=record.wind_m_s,
            wind_source_unit="m/s",
            pressure_hpa=pressure_hpa,
        )
        grouped[storm_id][grouped_member_key][record.tau_h] = point
        if storm_id not in names or record.storm_name:
            names[storm_id] = record.storm_name.strip() if record.storm_name else None

    storms: list[Storm] = []
    for storm_id in sorted(grouped):
        members: list[MemberTrack] = []
        for (member_number, member_type), points_by_tau in grouped[storm_id].items():
            members.append(
                MemberTrack(
                    id=str(member_number),
                    member_type=member_type,
                    points=[points_by_tau[tau_h] for tau_h in sorted(points_by_tau)],
                )
            )
        members.sort(key=lambda member: (_MEMBER_TYPE_ORDER[member.member_type], int(member.id)))
        number = int(storm_id[:2])
        storms.append(
            Storm(
                id=storm_id,
                name=names.get(storm_id),
                basin="WP",
                invest=90 <= number <= 99,
                members=members,
                mean=MeanTrack(points=compute_mean_track(members)),
            )
        )

    return CycleData(
        source_id=source_id,
        initialized_at=initialized_at_utc,
        storms=storms,
    )


def _broadcast_array(values: Any, subset_count: int, key: str) -> list[Any]:
    items = list(values)
    if len(items) == 1:
        return items * subset_count
    if len(items) != subset_count:
        raise ValueError(
            f"BUFR array {key!r} has length {len(items)}, expected 1 or {subset_count}"
        )
    return items


def _is_missing(value: Any, missing_double: float, missing_long: int) -> bool:
    return value is None or value in (missing_double, missing_long)


def _is_integer(value: Any) -> bool:
    return isinstance(value, Real) and isfinite(value) and int(value) == value


def _append_subset_records(
    records: list[BufrTrackRecord],
    *,
    storm_identifier: str,
    storm_name: str | None,
    member_numbers: list[Any],
    forecast_types: list[Any],
    taus: list[Any],
    latitudes: list[Any],
    longitudes: list[Any],
    winds: list[Any],
    pressures: list[Any],
    missing_double: float,
    missing_long: int,
) -> None:
    for member, forecast_type, tau, lat, lon, wind, pressure in zip(
        member_numbers,
        forecast_types,
        taus,
        latitudes,
        longitudes,
        winds,
        pressures,
        strict=True,
    ):
        required = (member, forecast_type, tau, lat, lon, wind)
        if any(_is_missing(value, missing_double, missing_long) for value in required):
            continue
        if not all(_is_integer(value) for value in (member, forecast_type, tau)):
            continue
        if not all(isinstance(value, Real) and isfinite(value) for value in (lat, lon, wind)):
            continue

        member_number = int(member)
        forecast_type_number = int(forecast_type)
        tau_h = int(tau)
        if not -90.0 <= float(lat) <= 90.0 or not -360.0 <= float(lon) <= 360.0:
            continue
        if not 0.0 <= float(wind) <= 200.0:
            continue

        pressure_pa: float | None
        if _is_missing(pressure, missing_double, missing_long):
            pressure_pa = None
        elif (
            not isinstance(pressure, Real)
            or not isfinite(pressure)
            or not 0.0 < float(pressure) <= 120000.0
        ):
            continue
        else:
            pressure_pa = float(pressure)

        records.append(
            BufrTrackRecord(
                storm_identifier=storm_identifier,
                ensemble_member_number=member_number,
                ensemble_forecast_type=forecast_type_number,
                tau_h=tau_h,
                lat=float(lat),
                lon=float(lon),
                wind_m_s=float(wind),
                pressure_pa=pressure_pa,
                storm_name=storm_name,
            )
        )


def decode_bufr_file(path: Path) -> list[BufrTrackRecord]:
    from eccodes import (  # type: ignore[import-untyped]
        CODES_MISSING_DOUBLE,
        CODES_MISSING_LONG,
        CodesInternalError,
        codes_bufr_new_from_file,
        codes_get,
        codes_get_array,
        codes_release,
        codes_set,
    )

    records: list[BufrTrackRecord] = []
    message_count = 0
    with Path(path).open("rb") as bufr_file:
        while True:
            handle = codes_bufr_new_from_file(bufr_file)
            if handle is None:
                break
            message_count += 1
            try:
                codes_set(handle, "unpack", 1)
                subset_count = int(codes_get(handle, "numberOfSubsets"))
                if subset_count <= 0:
                    raise ValueError("BUFR numberOfSubsets must be positive")
                storm_identifier = str(codes_get(handle, "stormIdentifier")).strip().upper()
                if _parse_storm_identifier(storm_identifier) is None:
                    continue
                storm_name_raw = str(codes_get(handle, "longStormName")).strip()
                storm_name = storm_name_raw or None
                member_numbers = _broadcast_array(
                    codes_get_array(handle, "ensembleMemberNumber"),
                    subset_count,
                    "ensembleMemberNumber",
                )
                forecast_types = _broadcast_array(
                    codes_get_array(handle, "ensembleForecastType"),
                    subset_count,
                    "ensembleForecastType",
                )

                analysis_values = {
                    "taus": [0] * subset_count,
                    "latitudes": _broadcast_array(
                        codes_get_array(handle, "#2#latitude"), subset_count, "#2#latitude"
                    ),
                    "longitudes": _broadcast_array(
                        codes_get_array(handle, "#2#longitude"), subset_count, "#2#longitude"
                    ),
                    "pressures": _broadcast_array(
                        codes_get_array(handle, "#1#pressureReducedToMeanSeaLevel"),
                        subset_count,
                        "#1#pressureReducedToMeanSeaLevel",
                    ),
                    "winds": _broadcast_array(
                        codes_get_array(handle, "#1#windSpeedAt10M"),
                        subset_count,
                        "#1#windSpeedAt10M",
                    ),
                }
                _append_subset_records(
                    records,
                    storm_identifier=storm_identifier,
                    storm_name=storm_name,
                    member_numbers=member_numbers,
                    forecast_types=forecast_types,
                    missing_double=CODES_MISSING_DOUBLE,
                    missing_long=CODES_MISSING_LONG,
                    **analysis_values,
                )

                forecast_rank = 1
                while True:
                    time_key = f"#{forecast_rank}#timePeriod"
                    try:
                        taus = _broadcast_array(
                            codes_get_array(handle, time_key), subset_count, time_key
                        )
                    except CodesInternalError:
                        break

                    position_rank = 2 * forecast_rank + 2
                    value_rank = forecast_rank + 1
                    latitude_key = f"#{position_rank}#latitude"
                    longitude_key = f"#{position_rank}#longitude"
                    pressure_key = f"#{value_rank}#pressureReducedToMeanSeaLevel"
                    wind_key = f"#{value_rank}#windSpeedAt10M"
                    _append_subset_records(
                        records,
                        storm_identifier=storm_identifier,
                        storm_name=storm_name,
                        member_numbers=member_numbers,
                        forecast_types=forecast_types,
                        taus=taus,
                        latitudes=_broadcast_array(
                            codes_get_array(handle, latitude_key), subset_count, latitude_key
                        ),
                        longitudes=_broadcast_array(
                            codes_get_array(handle, longitude_key), subset_count, longitude_key
                        ),
                        pressures=_broadcast_array(
                            codes_get_array(handle, pressure_key), subset_count, pressure_key
                        ),
                        winds=_broadcast_array(
                            codes_get_array(handle, wind_key), subset_count, wind_key
                        ),
                        missing_double=CODES_MISSING_DOUBLE,
                        missing_long=CODES_MISSING_LONG,
                    )
                    forecast_rank += 1
            finally:
                codes_release(handle)

    if message_count == 0:
        raise ValueError("payload contains no BUFR messages")
    if not records:
        raise ValueError("payload contains no valid BUFR track records")
    return records


class EcmwfBufrAdapter:
    def __init__(self, source_id: str, *, client: httpx.Client | None = None) -> None:
        if source_id not in _SOURCE_MODELS:
            raise ValueError(f"unsupported ECMWF source: {source_id}")
        self.source_id = source_id
        self._client = client

    def fetch_latest(self, now: datetime) -> AdapterOutcome:
        if self._client is not None:
            return self._fetch_latest(self._client, now)
        with httpx.Client(timeout=30.0) as client:
            return self._fetch_latest(client, now)

    def _fetch_latest(self, client: httpx.Client, now: datetime) -> AdapterOutcome:
        for cycle in candidate_cycles(now, count=8):
            cycle_id = cycle.strftime("%Y%m%d%H")
            url = build_tf_url(self.source_id, cycle)
            try:
                response = client.get(url)
            except httpx.RequestError:
                return AdapterOutcome(
                    source_id=self.source_id,
                    cycle_id=cycle_id,
                    status="error",
                    error_kind="network_error",
                )
            if response.status_code == httpx.codes.NOT_FOUND:
                continue
            if not response.is_success:
                return AdapterOutcome(
                    source_id=self.source_id,
                    cycle_id=cycle_id,
                    status="error",
                    error_kind="http_error",
                )

            try:
                with TemporaryDirectory() as directory:
                    path = Path(directory) / "tracks.bufr"
                    path.write_bytes(response.content)
                    records = decode_bufr_file(path)
                cycle_data = normalize_bufr_records(self.source_id, cycle, records)
            except Exception:
                return AdapterOutcome(
                    source_id=self.source_id,
                    cycle_id=cycle_id,
                    status="error",
                    error_kind="invalid_bufr_payload",
                )
            return AdapterOutcome(
                source_id=self.source_id,
                cycle_id=cycle_id,
                status="ok" if cycle_data.storms else "empty",
                cycle=cycle_data,
            )

        return AdapterOutcome(
            source_id=self.source_id,
            cycle_id=None,
            status="unavailable",
            error_kind="terminal_file_not_found",
        )
