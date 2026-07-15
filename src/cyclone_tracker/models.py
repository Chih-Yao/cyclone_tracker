import json
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, field_validator


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be UTC-aware")
    return value.astimezone(UTC)


def _require_finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("value must be finite")
    return value


def _validate_latitude(value: float) -> float:
    _require_finite(value)
    if not -90.0 <= value <= 90.0:
        raise ValueError("latitude must be between -90 and 90")
    return value


def _normalize_longitude(value: float) -> float:
    _require_finite(value)
    return (value + 180.0) % 360.0 - 180.0


def _validate_non_negative(value: float) -> float:
    _require_finite(value)
    if value < 0.0:
        raise ValueError("value must be non-negative")
    return value


def _validate_pressure(value: float) -> float:
    _require_finite(value)
    if not 0.0 < value <= 1200.0:
        raise ValueError("pressure must be greater than 0 and at most 1200 hPa")
    return value


UtcDateTime = Annotated[datetime, AfterValidator(_require_utc)]
Latitude = Annotated[float, AfterValidator(_validate_latitude)]
Longitude = Annotated[float, AfterValidator(_normalize_longitude)]
NonNegativeFloat = Annotated[float, AfterValidator(_validate_non_negative)]
Pressure = Annotated[float, AfterValidator(_validate_pressure)]

_STORM_ID_PATTERN = re.compile(r"^(0[1-9]|[1-4][0-9]|9[0-9])W$")


def _validate_storm_id(value: str) -> str:
    if _STORM_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("storm id must be a supported Western North Pacific identifier")
    return value


_StormId = Annotated[str, AfterValidator(_validate_storm_id)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class TrackPoint(_StrictModel):
    tau_h: int
    valid_at: UtcDateTime
    lat: Latitude
    lon: Longitude
    wind_kt: NonNegativeFloat
    wind_source_value: NonNegativeFloat
    wind_source_unit: Literal["kt", "m/s"]
    pressure_hpa: Pressure | None = None

    @field_validator("tau_h")
    @classmethod
    def validate_tau_h(cls, value: int) -> int:
        if value < 0:
            raise ValueError("tau_h must be non-negative")
        return value


class MemberTrack(_StrictModel):
    id: str
    member_type: Literal["control", "perturbed", "deterministic", "source_mean"]
    points: list[TrackPoint]

    @field_validator("points")
    @classmethod
    def validate_sorted_unique_tau_values(cls, value: list[TrackPoint]) -> list[TrackPoint]:
        tau_values = [point.tau_h for point in value]
        if tau_values != sorted(set(tau_values)):
            raise ValueError("member points must have sorted unique tau_h values")
        return value


class MeanPoint(_StrictModel):
    tau_h: int
    valid_at: UtcDateTime
    lat: Latitude
    lon: Longitude
    wind_kt: NonNegativeFloat | None
    pressure_hpa: Pressure | None
    member_count: int

    @field_validator("tau_h")
    @classmethod
    def validate_tau_h(cls, value: int) -> int:
        if value < 0:
            raise ValueError("tau_h must be non-negative")
        return value


class MeanTrack(_StrictModel):
    points: list[MeanPoint]


class Storm(_StrictModel):
    id: _StormId
    name: str | None
    basin: Literal["WP"]
    invest: bool
    members: list[MemberTrack]
    mean: MeanTrack


class CycleData(_StrictModel):
    schema_version: Literal[1] = 1
    source_id: str
    initialized_at: UtcDateTime
    storms: list[Storm]


class CycleSummary(_StrictModel):
    id: str
    initialized_at: UtcDateTime
    href: str
    storms: list[_StormId]
    empty: bool


class SourceState(_StrictModel):
    id: str
    name_zh_tw: str
    attribution_url: str
    status: Literal["ok", "empty", "stale", "error"]
    last_success_cycle: str | None
    stale_after_hours: int = 12
    error_kind: str | None = None
    cycles: list[CycleSummary]


class Manifest(_StrictModel):
    schema_version: Literal[1] = 1
    generated_at: UtcDateTime
    sources: list[SourceState]


def is_supported_storm(basin: str, number: int) -> bool:
    return basin == "WP" and (1 <= number <= 49 or 90 <= number <= 99)


def _json_bytes(model: BaseModel) -> bytes:
    payload = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return payload.encode("utf-8") + b"\n"


def cycle_to_json_bytes(cycle: CycleData) -> bytes:
    return _json_bytes(cycle)


def manifest_to_json_bytes(manifest: Manifest) -> bytes:
    return _json_bytes(manifest)
