from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest

import cyclone_tracker.cli as cli_module
from cyclone_tracker.adapters.base import AdapterOutcome
from cyclone_tracker.cli import main
from cyclone_tracker.models import CycleData, MeanPoint, MeanTrack, Storm
from cyclone_tracker.pipeline import update_sources
from cyclone_tracker.storage import DataStore


def cycle_at(
    initialized_at: datetime,
    source_id: str = "gefs",
    *,
    empty: bool = False,
) -> CycleData:
    storms = []
    if not empty:
        storms = [
            Storm(
                id="09W",
                name="YAGI",
                basin="WP",
                invest=False,
                members=[],
                mean=MeanTrack(
                    points=[
                        MeanPoint(
                            tau_h=0,
                            valid_at=initialized_at,
                            lat=15.0,
                            lon=135.0,
                            wind_kt=40.0,
                            pressure_hpa=995.0,
                            member_count=1,
                        )
                    ]
                ),
            )
        ]
    return CycleData(source_id=source_id, initialized_at=initialized_at, storms=storms)


@dataclass
class FakeAdapter:
    source_id: str
    outcome: AdapterOutcome

    def fetch_latest(self, now: datetime) -> AdapterOutcome:
        return self.outcome


@dataclass
class RaisingAdapter:
    source_id: str

    def fetch_latest(self, now: datetime) -> AdapterOutcome:
        raise RuntimeError("secret upstream detail")


def ok_adapter(
    source_id: str,
    *,
    initialized_at: datetime = datetime(2026, 7, 15, tzinfo=UTC),
    empty: bool = False,
) -> FakeAdapter:
    cycle = cycle_at(initialized_at, source_id, empty=empty)
    return FakeAdapter(
        source_id,
        AdapterOutcome(
            source_id=source_id,
            cycle_id=initialized_at.strftime("%Y%m%d%H"),
            status="empty" if empty else "ok",
            cycle=cycle,
        ),
    )


def error_adapter(source_id: str, error_kind: str) -> FakeAdapter:
    return FakeAdapter(
        source_id,
        AdapterOutcome(
            source_id=source_id,
            cycle_id=None,
            status="error",
            error_kind=error_kind,
        ),
    )


def unavailable_adapter(source_id: str) -> FakeAdapter:
    return FakeAdapter(
        source_id,
        AdapterOutcome(
            source_id=source_id,
            cycle_id=None,
            status="unavailable",
            error_kind="not_yet_published",
        ),
    )


def test_update_sources_publishes_success_when_neighbor_fails(tmp_path: Path) -> None:
    report = update_sources(
        [ok_adapter("gefs"), error_adapter("ifs-ens", "decode_error")],
        DataStore(tmp_path),
        now=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )

    assert report.succeeded == ["gefs"]
    assert report.empty == []
    assert report.failed == {"ifs-ens": "decode_error"}
    assert (tmp_path / "gefs" / "2026071500.json").exists()
    assert DataStore(tmp_path).load_manifest().sources[3].status == "error"


def test_fetch_exception_is_isolated_and_message_is_not_persisted(tmp_path: Path) -> None:
    report = update_sources(
        [RaisingAdapter("gefs"), ok_adapter("aigefs")],
        DataStore(tmp_path),
        now=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )

    assert report.failed == {"gefs": "unexpected_error"}
    assert report.succeeded == ["aigefs"]
    assert b"secret upstream detail" not in (tmp_path / "manifest.json").read_bytes()


def test_unavailable_preserves_cycle_and_maps_to_stale(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(
        cycle_at(datetime(2026, 7, 15, tzinfo=UTC)),
        now=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )

    report = update_sources(
        [unavailable_adapter("gefs")],
        store,
        now=datetime(2026, 7, 15, 2, tzinfo=UTC),
    )

    assert report.failed == {"gefs": "unavailable"}
    source = store.load_manifest().sources[0]
    assert source.status == "stale"
    assert source.last_success_cycle == "2026071500"
    assert (tmp_path / "gefs" / "2026071500.json").exists()


def test_successful_old_cycle_uses_supplied_now_for_stale_status(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    report = update_sources(
        [ok_adapter("gefs", initialized_at=datetime(2026, 7, 14, tzinfo=UTC))],
        store,
        now=datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert report.succeeded == ["gefs"]
    assert store.load_manifest().sources[0].status == "stale"


@pytest.mark.parametrize(
    "outcome",
    [
        AdapterOutcome(source_id="gefs", cycle_id="2026071500", status="ok"),
        AdapterOutcome(
            source_id="gefs",
            cycle_id=None,
            status="empty",
            cycle=cycle_at(datetime(2026, 7, 15, tzinfo=UTC), empty=True),
        ),
        AdapterOutcome(
            source_id="aigefs",
            cycle_id="2026071500",
            status="ok",
            cycle=cycle_at(datetime(2026, 7, 15, tzinfo=UTC)),
        ),
        AdapterOutcome(
            source_id="gefs",
            cycle_id="2026071506",
            status="ok",
            cycle=cycle_at(datetime(2026, 7, 15, tzinfo=UTC)),
        ),
    ],
)
def test_invalid_adapter_outcome_is_per_source_failure(
    tmp_path: Path, outcome: AdapterOutcome
) -> None:
    report = update_sources(
        [FakeAdapter("gefs", outcome), ok_adapter("aigefs")],
        DataStore(tmp_path),
        now=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )

    assert report.failed == {"gefs": "invalid_adapter_outcome"}
    assert report.succeeded == ["aigefs"]


def test_global_manifest_error_is_not_swallowed_as_source_failure(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{bad", encoding="utf-8")

    with pytest.raises(ValueError):
        update_sources(
            [ok_adapter("gefs")],
            DataStore(tmp_path),
            now=datetime(2026, 7, 15, 1, tzinfo=UTC),
        )


def test_duplicate_adapter_source_is_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate adapter"):
        update_sources(
            [ok_adapter("gefs"), ok_adapter("gefs")],
            DataStore(tmp_path),
            now=datetime(2026, 7, 15, 1, tzinfo=UTC),
        )


def test_unchanged_success_is_reported_deterministically(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    now = datetime(2026, 7, 15, 1, tzinfo=UTC)
    update_sources([ok_adapter("gefs")], store, now=now)

    report = update_sources([ok_adapter("gefs")], store, now=now)

    assert report.succeeded == []
    assert report.empty == []
    assert report.unchanged == ["gefs"]
    assert report.failed == {}


def test_successful_empty_cycle_is_reported_and_is_valid_data(tmp_path: Path) -> None:
    report = update_sources(
        [ok_adapter("gefs", empty=True)],
        DataStore(tmp_path),
        now=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )

    assert report.empty == ["gefs"]
    assert DataStore(tmp_path).source_has_valid_data("gefs") is True


def test_cli_validate_returns_nonzero_for_dangling_manifest_href(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    (tmp_path / "gefs" / "2026071500.json").unlink()
    output = StringIO()

    assert main(["validate", str(tmp_path)], stdout=output) == 1
    assert "找不到 cycle 檔案" in output.getvalue()


def test_cli_validate_success_message(tmp_path: Path) -> None:
    DataStore(tmp_path).publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    output = StringIO()

    assert main(["validate", str(tmp_path)], stdout=output) == 0
    assert output.getvalue() == "資料結構有效\n"


def test_cli_update_returns_one_when_selected_source_has_no_valid_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    DataStore(tmp_path).publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC), "aigefs"))
    monkeypatch.setattr(
        cli_module,
        "build_adapters",
        lambda source_ids: [error_adapter(source_ids[0], "network_error")],
    )
    output = StringIO()

    assert main(["update", str(tmp_path), "--source", "gefs"], stdout=output) == 1
    assert "NCEP GEFS：失敗（network_error）" in output.getvalue()


def test_cli_update_returns_zero_for_schema_valid_preserved_selected_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    DataStore(tmp_path).publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    monkeypatch.setattr(
        cli_module,
        "build_adapters",
        lambda source_ids: [error_adapter(source_ids[0], "network_error")],
    )

    assert main(["update", str(tmp_path), "--source", "gefs"], stdout=StringIO()) == 0


def test_cli_update_returns_zero_for_successful_empty_selected_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli_module,
        "build_adapters",
        lambda source_ids: [ok_adapter(source_ids[0], empty=True)],
    )

    assert main(["update", str(tmp_path), "--source", "gefs"], stdout=StringIO()) == 0


def test_cli_update_returns_one_for_global_manifest_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "manifest.json").write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(
        cli_module,
        "build_adapters",
        lambda source_ids: [ok_adapter(source_ids[0])],
    )
    output = StringIO()

    assert main(["update", str(tmp_path), "--source", "gefs"], stdout=output) == 1
    assert "更新失敗：資料設定或儲存錯誤" in output.getvalue()
