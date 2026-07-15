from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import numpy as np
import pytest

from cyclone_tracker.adapters.ecmwf import (
    BufrTrackRecord,
    EcmwfBufrAdapter,
    build_tf_url,
    decode_bufr_file,
    normalize_bufr_records,
)

INITIALIZED_AT = datetime(2026, 7, 15, tzinfo=UTC)
MISSING_DOUBLE = 1.0e100
MISSING_LONG = 2_147_483_647


@pytest.mark.parametrize(
    ("source_id", "hour", "step", "model"),
    [
        ("ifs-ens", 0, 360, "ifs"),
        ("ifs-ens", 6, 144, "ifs"),
        ("ifs-ens", 12, 360, "ifs"),
        ("ifs-ens", 18, 144, "ifs"),
        ("aifs-ens", 0, 360, "aifs-ens"),
        ("aifs-ens", 6, 360, "aifs-ens"),
        ("aifs-ens", 12, 360, "aifs-ens"),
        ("aifs-ens", 18, 360, "aifs-ens"),
    ],
)
def test_build_tf_url_uses_terminal_file_matrix(
    source_id: str,
    hour: int,
    step: int,
    model: str,
) -> None:
    cycle = datetime(2026, 7, 15, hour, tzinfo=UTC)

    assert build_tf_url(source_id, cycle) == (
        f"https://data.ecmwf.int/forecasts/20260715/{hour:02d}z/{model}/0p25/enfo/"
        f"20260715{hour:02d}0000-{step}h-enfo-tf.bufr"
    )


def test_build_ifs_tf_url_uses_official_open_data_layout() -> None:
    cycle = datetime(2026, 7, 15, 0, tzinfo=UTC)
    assert build_tf_url("ifs-ens", cycle) == (
        "https://data.ecmwf.int/forecasts/20260715/00z/ifs/0p25/enfo/"
        "20260715000000-360h-enfo-tf.bufr"
    )


@pytest.mark.parametrize("source_id", ["ifs", "aifs", "gefs"])
def test_build_tf_url_rejects_unsupported_sources(source_id: str) -> None:
    with pytest.raises(ValueError, match="unsupported ECMWF source"):
        build_tf_url(source_id, INITIALIZED_AT)


def test_normalize_bufr_records_converts_si_wind_and_filters_basin() -> None:
    records = [
        BufrTrackRecord("09W", 1, 4, 6, 15.0, 135.0, 51.4444444444, 96500.0, "YAGI"),
        BufrTrackRecord("09E", 1, 4, 6, 15.0, -120.0, 40.0, 98000.0, None),
    ]

    cycle = normalize_bufr_records("ifs-ens", INITIALIZED_AT, records)
    point = cycle.storms[0].members[0].points[0]

    assert cycle.storms[0].id == "09W"
    assert cycle.storms[0].name == "YAGI"
    assert point.wind_kt == pytest.approx(100.0)
    assert point.wind_source_value == pytest.approx(51.4444444444)
    assert point.wind_source_unit == "m/s"
    assert point.pressure_hpa == 965.0


def test_normalize_bufr_records_classifies_aifs_members_and_computes_mean() -> None:
    records = [
        BufrTrackRecord("90W", 51, 1, 0, 10.0, 130.0, 20.0, 100000.0, None),
        BufrTrackRecord("90W", 1, 4, 0, 12.0, 132.0, 30.0, 99000.0, None),
        BufrTrackRecord("90W", 52, 0, 0, 11.0, 131.0, 25.0, None, None),
    ]

    cycle = normalize_bufr_records("aifs-ens", INITIALIZED_AT, records)
    storm = cycle.storms[0]

    assert storm.invest is True
    assert [(member.id, member.member_type) for member in storm.members] == [
        ("51", "control"),
        ("1", "perturbed"),
        ("52", "deterministic"),
    ]
    assert storm.mean.points[0].member_count == 3


def test_normalize_bufr_records_classifies_ifs_member_51_type_0_as_control() -> None:
    cycle = normalize_bufr_records(
        "ifs-ens",
        INITIALIZED_AT,
        [BufrTrackRecord("09W", 51, 0, 0, 15.0, 135.0, 20.0, 99000.0, None)],
    )

    assert [(member.id, member.member_type) for member in cycle.storms[0].members] == [
        ("51", "control")
    ]


@pytest.mark.parametrize(
    ("source_id", "member_number", "forecast_type"),
    [
        ("ifs-ens", 51, 1),
        ("ifs-ens", 52, 0),
        ("aifs-ens", 51, 0),
        ("aifs-ens", 52, 1),
        ("aifs-ens", 999, 4),
    ],
)
def test_normalize_bufr_records_rejects_source_specific_member_type_mismatch(
    source_id: str,
    member_number: int,
    forecast_type: int,
) -> None:
    record = BufrTrackRecord(
        "09W", member_number, forecast_type, 0, 15.0, 135.0, 20.0, 99000.0, None
    )

    with pytest.raises(ValueError, match="invalid ECMWF record"):
        normalize_bufr_records(source_id, INITIALIZED_AT, [record])


@pytest.mark.parametrize(
    ("source_id", "cycle", "member_number", "forecast_type", "tau_h"),
    [
        ("ifs-ens", datetime(2026, 7, 15, 6, tzinfo=UTC), 51, 0, 144),
        ("ifs-ens", INITIALIZED_AT, 51, 0, 360),
        ("aifs-ens", INITIALIZED_AT, 52, 0, 360),
    ],
)
def test_normalize_bufr_records_accepts_terminal_horizon_boundary(
    source_id: str,
    cycle: datetime,
    member_number: int,
    forecast_type: int,
    tau_h: int,
) -> None:
    record = BufrTrackRecord(
        "09W", member_number, forecast_type, tau_h, 15.0, 135.0, 20.0, 99000.0, None
    )

    normalized = normalize_bufr_records(source_id, cycle, [record])

    assert normalized.storms[0].members[0].points[0].tau_h == tau_h


@pytest.mark.parametrize(
    ("source_id", "cycle", "member_number", "forecast_type", "tau_h"),
    [
        ("ifs-ens", datetime(2026, 7, 15, 6, tzinfo=UTC), 51, 0, 150),
        ("ifs-ens", INITIALIZED_AT, 51, 0, 366),
        ("aifs-ens", INITIALIZED_AT, 52, 0, 366),
        ("ifs-ens", INITIALIZED_AT, 51, 0, 999),
        ("ifs-ens", INITIALIZED_AT, 51, 0, -6),
        ("ifs-ens", INITIALIZED_AT, 51, 0, 1),
    ],
)
def test_normalize_bufr_records_rejects_invalid_tau_for_source_cycle(
    source_id: str,
    cycle: datetime,
    member_number: int,
    forecast_type: int,
    tau_h: int,
) -> None:
    record = BufrTrackRecord(
        "09W", member_number, forecast_type, tau_h, 15.0, 135.0, 20.0, 99000.0, None
    )

    with pytest.raises(ValueError, match="invalid ECMWF record"):
        normalize_bufr_records(source_id, cycle, [record])


def test_normalize_bufr_records_keeps_last_duplicate_member_tau() -> None:
    records = [
        BufrTrackRecord("09W", 1, 4, 6, 15.0, 135.0, 20.0, 98000.0, "YAGI"),
        BufrTrackRecord("09W", 1, 4, 6, 16.0, 136.0, 30.0, 97000.0, "YAGI"),
    ]

    cycle = normalize_bufr_records("ifs-ens", INITIALIZED_AT, records)
    point = cycle.storms[0].members[0].points[0]

    assert point.lat == 16.0
    assert point.wind_source_value == 30.0
    assert point.pressure_hpa == 970.0


class FakeCodesInternalError(Exception):
    pass


class FakeEccodes(ModuleType):
    CODES_MISSING_DOUBLE = MISSING_DOUBLE
    CODES_MISSING_LONG = MISSING_LONG
    CodesInternalError = FakeCodesInternalError

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        super().__init__("eccodes")
        self.messages = iter(messages)
        self.released: list[dict[str, Any]] = []

    def codes_bufr_new_from_file(self, _file: object) -> dict[str, Any] | None:
        return next(self.messages, None)

    def codes_set(self, message: dict[str, Any], key: str, value: int) -> None:
        assert (key, value) == ("unpack", 1)
        message["unpacked"] = True

    def codes_get(self, message: dict[str, Any], key: str) -> Any:
        try:
            return message["values"][key]
        except KeyError as error:
            raise FakeCodesInternalError(key) from error

    def codes_get_array(self, message: dict[str, Any], key: str) -> list[int | float]:
        try:
            return message["arrays"][key]
        except KeyError as error:
            raise FakeCodesInternalError(key) from error

    def codes_release(self, message: dict[str, Any]) -> None:
        self.released.append(message)


def bufr_message(*, subsets: int = 2) -> dict[str, Any]:
    message = {
        "values": {
            "numberOfSubsets": subsets,
            "stormIdentifier": "09W",
            "longStormName": "YAGI",
        },
        "arrays": {
            "ensembleMemberNumber": [51, 1],
            "ensembleForecastType": [1, 4],
            "#2#latitude": [15.0, 16.0],
            "#2#longitude": [135.0, 136.0],
            "#1#pressureReducedToMeanSeaLevel": [96500.0, 97000.0],
            "#1#windSpeedAt10M": [51.4444444444, 40.0],
            "#1#timePeriod": [6],
            "#4#latitude": [16.0, 17.0],
            "#4#longitude": [136.0, 137.0],
            "#2#pressureReducedToMeanSeaLevel": [96000.0, 96500.0],
            "#2#windSpeedAt10M": [55.0, 45.0],
        },
    }
    if subsets == 1:
        message["arrays"] = {key: values[:1] for key, values in message["arrays"].items()}
    return message


def install_fake_eccodes(monkeypatch: pytest.MonkeyPatch, *messages: dict[str, Any]) -> FakeEccodes:
    module = FakeEccodes(list(messages))
    monkeypatch.setitem(sys.modules, "eccodes", module)
    return module


def test_decode_bufr_file_reads_ranked_descriptors_and_releases_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    message = bufr_message()
    module = install_fake_eccodes(monkeypatch, message)
    path = tmp_path / "tracks.bufr"
    path.write_bytes(b"BUFR")

    records = decode_bufr_file(path)

    assert records == [
        BufrTrackRecord("09W", 51, 1, 0, 15.0, 135.0, 51.4444444444, 96500.0, "YAGI"),
        BufrTrackRecord("09W", 1, 4, 0, 16.0, 136.0, 40.0, 97000.0, "YAGI"),
        BufrTrackRecord("09W", 51, 1, 6, 16.0, 136.0, 55.0, 96000.0, "YAGI"),
        BufrTrackRecord("09W", 1, 4, 6, 17.0, 137.0, 45.0, 96500.0, "YAGI"),
    ]
    assert message["unpacked"] is True
    assert module.released == [message]


def test_decode_bufr_file_broadcasts_scalar_compressed_arrays(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    message = bufr_message()
    message["arrays"]["#4#latitude"] = [18.0]
    message["arrays"]["#4#longitude"] = [138.0]
    message["arrays"]["#2#pressureReducedToMeanSeaLevel"] = [95000.0]
    message["arrays"]["#2#windSpeedAt10M"] = [60.0]
    install_fake_eccodes(monkeypatch, message)
    path = tmp_path / "tracks.bufr"
    path.write_bytes(b"BUFR")

    records = decode_bufr_file(path)

    forecast = [record for record in records if record.tau_h == 6]
    assert [(record.lat, record.lon, record.wind_m_s) for record in forecast] == [
        (18.0, 138.0, 60.0),
        (18.0, 138.0, 60.0),
    ]


def test_decode_bufr_file_accepts_numpy_scalars_returned_by_eccodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    message = bufr_message(subsets=1)
    message["arrays"] = {key: np.asarray(values[:1]) for key, values in message["arrays"].items()}
    install_fake_eccodes(monkeypatch, message)
    path = tmp_path / "tracks.bufr"
    path.write_bytes(b"BUFR")

    records = decode_bufr_file(path)

    assert len(records) == 2
    assert records[1].tau_h == 6


def test_decode_bufr_file_skips_partial_missing_values_but_keeps_missing_pressure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    message = bufr_message()
    message["arrays"]["#4#latitude"] = [18.0, MISSING_DOUBLE]
    message["arrays"]["#2#pressureReducedToMeanSeaLevel"] = [MISSING_DOUBLE, 95000.0]
    install_fake_eccodes(monkeypatch, message)
    path = tmp_path / "tracks.bufr"
    path.write_bytes(b"BUFR")

    records = decode_bufr_file(path)

    forecast = [record for record in records if record.tau_h == 6]
    assert len(forecast) == 1
    assert forecast[0].ensemble_member_number == 51
    assert forecast[0].pressure_pa is None


def test_decode_bufr_file_rejects_malformed_array_length_and_releases_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    message = bufr_message()
    message["arrays"]["#4#latitude"] = [15.0, 16.0, 17.0]
    module = install_fake_eccodes(monkeypatch, message)
    path = tmp_path / "tracks.bufr"
    path.write_bytes(b"BUFR")

    with pytest.raises(ValueError, match="#4#latitude"):
        decode_bufr_file(path)

    assert module.released == [message]


def test_decode_bufr_file_rejects_payload_without_bufr_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_fake_eccodes(monkeypatch)
    path = tmp_path / "tracks.bufr"
    path.write_bytes(b"not BUFR")

    with pytest.raises(ValueError, match="no BUFR messages"):
        decode_bufr_file(path)


def test_fetch_latest_reports_malformed_raw_storm_identifier_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = bufr_message(subsets=1)
    message["values"]["stormIdentifier"] = "BAD!"
    install_fake_eccodes(monkeypatch, message)

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"BUFR"))
    ) as client:
        outcome = EcmwfBufrAdapter("aifs-ens", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle is None
    assert outcome.error_kind == "invalid_bufr_payload"


@pytest.mark.parametrize("storm_identifier", ["05E", "70W"])
def test_fetch_latest_preserves_valid_unsupported_storm_identifier_as_empty(
    monkeypatch: pytest.MonkeyPatch,
    storm_identifier: str,
) -> None:
    message = bufr_message(subsets=1)
    message["values"]["stormIdentifier"] = storm_identifier
    install_fake_eccodes(monkeypatch, message)

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"BUFR"))
    ) as client:
        outcome = EcmwfBufrAdapter("aifs-ens", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "empty"
    assert outcome.cycle is not None
    assert outcome.cycle.storms == []


@pytest.mark.parametrize(
    ("member_number", "forecast_type", "tau_h"),
    [(999, 4, 0), (52, 0, 999)],
)
def test_fetch_latest_maps_semantic_record_violation_to_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
    member_number: int,
    forecast_type: int,
    tau_h: int,
) -> None:
    monkeypatch.setattr(
        "cyclone_tracker.adapters.ecmwf.decode_bufr_file",
        lambda _path: [
            BufrTrackRecord(
                "05E",
                member_number,
                forecast_type,
                tau_h,
                15.0,
                -120.0,
                20.0,
                99000.0,
                None,
            )
        ],
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"BUFR"))
    ) as client:
        outcome = EcmwfBufrAdapter("aifs-ens", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle is None
    assert outcome.error_kind == "invalid_bufr_payload"


def test_fetch_latest_falls_back_from_404_and_decodes_one_terminal_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    newest = datetime(2026, 7, 15, 6, tzinfo=UTC)
    selected = INITIALIZED_AT
    calls: list[str] = []
    decoded_payloads: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == build_tf_url("ifs-ens", newest):
            return httpx.Response(404)
        if str(request.url) == build_tf_url("ifs-ens", selected):
            return httpx.Response(200, content=b"BUFR selected")
        raise AssertionError(f"unexpected request: {request.url}")

    def fake_decode(path: Path) -> list[BufrTrackRecord]:
        assert path.exists()
        decoded_payloads.append(path.read_bytes())
        return [BufrTrackRecord("09W", 1, 4, 0, 15.0, 135.0, 20.0, 99000.0, None)]

    monkeypatch.setattr("cyclone_tracker.adapters.ecmwf.decode_bufr_file", fake_decode)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = EcmwfBufrAdapter("ifs-ens", client=client).fetch_latest(
            datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
        )

    assert outcome.status == "ok"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is not None
    assert outcome.cycle.storms[0].id == "09W"
    assert calls == [build_tf_url("ifs-ens", newest), build_tf_url("ifs-ens", selected)]
    assert decoded_payloads == [b"BUFR selected"]


def test_fetch_latest_returns_empty_only_after_successful_decode_without_supported_storm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyclone_tracker.adapters.ecmwf.decode_bufr_file",
        lambda _path: [BufrTrackRecord("09E", 1, 4, 0, 15.0, -120.0, 20.0, 99000.0, None)],
    )

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"BUFR"))
    ) as client:
        outcome = EcmwfBufrAdapter("aifs-ens", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "empty"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is not None
    assert outcome.cycle.storms == []


def test_fetch_latest_returns_unavailable_after_eight_missing_terminal_files() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = EcmwfBufrAdapter("ifs-ens", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "unavailable"
    assert outcome.cycle_id is None
    assert outcome.cycle is None
    assert outcome.error_kind == "terminal_file_not_found"
    assert len(calls) == 8


@pytest.mark.parametrize(
    ("response", "error_kind"),
    [
        (httpx.Response(503), "http_error"),
        (httpx.ConnectError("offline"), "network_error"),
    ],
)
def test_fetch_latest_reports_transport_failures(
    response: httpx.Response | httpx.ConnectError,
    error_kind: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = EcmwfBufrAdapter("ifs-ens", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is None
    assert outcome.error_kind == error_kind


def test_fetch_latest_reports_malformed_200_payload_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_decode(_path: Path) -> list[BufrTrackRecord]:
        raise ValueError("invalid BUFR")

    monkeypatch.setattr("cyclone_tracker.adapters.ecmwf.decode_bufr_file", fail_decode)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"<html>upstream error</html>")
        )
    ) as client:
        outcome = EcmwfBufrAdapter("ifs-ens", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is None
    assert outcome.error_kind == "invalid_bufr_payload"


@pytest.mark.network
def test_live_terminal_file_smoke() -> None:
    if os.environ.get("RUN_NETWORK_TESTS") != "1":
        pytest.skip("set RUN_NETWORK_TESTS=1 to enable live ECMWF smoke test")

    source_id = os.environ.get("ECMWF_NETWORK_SOURCE", "ifs-ens")
    cycle_text = os.environ.get("ECMWF_NETWORK_CYCLE")
    cycle = (
        datetime.strptime(cycle_text, "%Y%m%d%H").replace(tzinfo=UTC)
        if cycle_text
        else datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    )
    cycle = cycle.replace(hour=(cycle.hour // 6) * 6)

    response = httpx.get(build_tf_url(source_id, cycle), timeout=60.0)
    if response.status_code == 404:
        pytest.skip("selected cycle has no published tropical-cyclone terminal file")
    response.raise_for_status()
    assert response.content.startswith(b"BUFR")
