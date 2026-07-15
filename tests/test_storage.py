import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import cyclone_tracker.storage as storage_module
from cyclone_tracker.config import SOURCE_CONFIGS
from cyclone_tracker.models import CycleData, MeanPoint, MeanTrack, Storm
from cyclone_tracker.storage import DataStore


def cycle_at(initialized_at: datetime, source_id: str = "gefs") -> CycleData:
    mean_point = MeanPoint(
        tau_h=0,
        valid_at=initialized_at,
        lat=15.0,
        lon=135.0,
        wind_kt=40.0,
        pressure_hpa=995.0,
        member_count=1,
    )
    return CycleData(
        source_id=source_id,
        initialized_at=initialized_at,
        storms=[
            Storm(
                id="09W",
                name="YAGI",
                basin="WP",
                invest=False,
                members=[],
                mean=MeanTrack(points=[mean_point]),
            )
        ],
    )


def empty_cycle_at(initialized_at: datetime, source_id: str = "gefs") -> CycleData:
    return CycleData(source_id=source_id, initialized_at=initialized_at, storms=[])


def temporary_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.name.endswith(".tmp")]


def test_missing_manifest_bootstraps_deterministically_without_writing(tmp_path: Path) -> None:
    first = DataStore(tmp_path).load_manifest()
    second = DataStore(tmp_path).load_manifest()

    assert first == second
    assert [source.id for source in first.sources] == [source.id for source in SOURCE_CONFIGS]
    assert all(source.status == "stale" for source in first.sources)
    assert not (tmp_path / "manifest.json").exists()


def test_validate_missing_manifest_is_read_only(tmp_path: Path) -> None:
    errors = DataStore(tmp_path).validate_tree()

    assert errors == ["找不到 manifest.json"]
    assert list(tmp_path.iterdir()) == []


def test_broken_manifest_symlink_is_rejected_instead_of_bootstrapped(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").symlink_to(tmp_path / "missing.json")

    with pytest.raises(ValueError, match="symlink"):
        DataStore(tmp_path).load_manifest()
    assert DataStore(tmp_path).validate_tree() == ["manifest.json 不可為符號連結"]


def test_publish_retains_latest_twelve_successful_cycles(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    for hour in range(13):
        store.publish_cycle(cycle_at(datetime(2026, 7, 1, tzinfo=UTC) + timedelta(hours=6 * hour)))
    source = store.load_manifest().sources[0]
    assert len(source.cycles) == 12
    assert source.cycles[0].id == "2026070400"
    assert not (tmp_path / "gefs" / "2026070100.json").exists()


def test_retry_cleans_orphan_after_transient_retention_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DataStore(tmp_path)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    for offset in range(12):
        store.publish_cycle(cycle_at(start + timedelta(hours=6 * offset)))

    evicted_path = tmp_path / "gefs" / "2026070100.json"
    thirteenth = cycle_at(start + timedelta(hours=6 * 12))
    real_unlink = Path.unlink
    failed_once = False

    def transient_unlink_failure(path: Path, missing_ok: bool = False) -> None:
        nonlocal failed_once
        if path == evicted_path and not failed_once:
            failed_once = True
            raise OSError("simulated transient retention unlink failure")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", transient_unlink_failure)
    with pytest.raises(OSError, match="transient retention unlink failure"):
        store.publish_cycle(thirteenth, now=datetime(2026, 7, 4, 1, tzinfo=UTC))

    manifest_path = tmp_path / "manifest.json"
    manifest_after_failure = manifest_path.read_bytes()
    assert evicted_path.exists()
    assert (tmp_path / "gefs" / "2026070400.json").exists()
    assert any("未被 manifest 引用" in error for error in store.validate_tree())

    assert store.publish_cycle(thirteenth, now=datetime(2026, 7, 4, 2, tzinfo=UTC)) is True
    assert not evicted_path.exists()
    assert manifest_path.read_bytes() == manifest_after_failure
    assert store.validate_tree() == []
    source = store.load_manifest().sources[0]
    assert len(source.cycles) == 12
    assert source.cycles[0].id == "2026070400"


def test_empty_cycles_are_published_and_count_toward_retention(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    for offset in range(13):
        store.publish_cycle(empty_cycle_at(start + timedelta(hours=6 * offset)))

    source = store.load_manifest().sources[0]
    assert len(source.cycles) == 12
    assert source.status == "empty"
    assert source.cycles[0].empty is True
    assert source.cycles[0].storms == []
    assert not (tmp_path / "gefs" / "2026070100.json").exists()


def test_identical_publish_is_byte_and_mtime_noop(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    cycle = cycle_at(datetime(2026, 7, 15, tzinfo=UTC))
    assert store.publish_cycle(cycle, now=datetime(2026, 7, 15, 1, tzinfo=UTC)) is True
    cycle_path = tmp_path / "gefs" / "2026071500.json"
    manifest_path = tmp_path / "manifest.json"
    first_cycle_bytes = cycle_path.read_bytes()
    first_manifest_bytes = manifest_path.read_bytes()
    first_cycle_mtime = cycle_path.stat().st_mtime_ns
    first_manifest_mtime = manifest_path.stat().st_mtime_ns

    assert store.publish_cycle(cycle, now=datetime(2026, 7, 15, 2, tzinfo=UTC)) is False
    assert cycle_path.read_bytes() == first_cycle_bytes
    assert manifest_path.read_bytes() == first_manifest_bytes
    assert cycle_path.stat().st_mtime_ns == first_cycle_mtime
    assert manifest_path.stat().st_mtime_ns == first_manifest_mtime


def test_cycle_and_manifest_json_are_deterministic_and_leave_no_temp_files(
    tmp_path: Path,
) -> None:
    cycle = cycle_at(datetime(2026, 7, 15, tzinfo=UTC))
    now = datetime(2026, 7, 15, 1, tzinfo=UTC)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    DataStore(first_root).publish_cycle(cycle, now=now)
    DataStore(second_root).publish_cycle(cycle, now=now)

    assert (first_root / "manifest.json").read_bytes() == (
        second_root / "manifest.json"
    ).read_bytes()
    assert (first_root / "gefs" / "2026071500.json").read_bytes() == (
        second_root / "gefs" / "2026071500.json"
    ).read_bytes()
    assert temporary_files(first_root) == []
    assert temporary_files(second_root) == []


def test_older_than_retained_cycle_is_complete_noop(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    start = datetime(2026, 7, 2, tzinfo=UTC)
    for offset in range(12):
        store.publish_cycle(cycle_at(start + timedelta(hours=6 * offset)))
    manifest_path = tmp_path / "manifest.json"
    before = manifest_path.read_bytes()
    before_mtime = manifest_path.stat().st_mtime_ns

    older = cycle_at(start - timedelta(hours=6))
    assert store.publish_cycle(older, now=datetime(2026, 7, 20, tzinfo=UTC)) is False
    assert not (tmp_path / "gefs" / "2026070118.json").exists()
    assert manifest_path.read_bytes() == before
    assert manifest_path.stat().st_mtime_ns == before_mtime


def test_retained_backfill_keeps_status_of_newest_cycle(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    newest = datetime(2026, 7, 15, tzinfo=UTC)
    store.publish_cycle(cycle_at(newest), now=newest + timedelta(hours=1))

    assert store.publish_cycle(
        empty_cycle_at(newest - timedelta(hours=6)),
        now=newest + timedelta(hours=2),
    )
    source = store.load_manifest().sources[0]
    assert source.last_success_cycle == "2026071500"
    assert source.status == "ok"


def test_failed_update_preserves_last_good_cycle_and_records_stable_error(
    tmp_path: Path,
) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    first = store.record_failure("gefs", "http_unavailable")
    manifest_after_first = (tmp_path / "manifest.json").read_bytes()
    second = store.record_failure("gefs", "http_unavailable")
    assert first is True
    assert second is False
    assert (tmp_path / "manifest.json").read_bytes() == manifest_after_first
    assert (tmp_path / "gefs" / "2026071500.json").exists()


def test_unavailable_maps_to_stale_and_repeated_status_is_noop(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(
        cycle_at(datetime(2026, 7, 15, tzinfo=UTC)),
        now=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )

    assert store.record_unavailable("gefs", now=datetime(2026, 7, 15, 2, tzinfo=UTC)) is True
    after_first = (tmp_path / "manifest.json").read_bytes()
    assert store.record_unavailable("gefs", now=datetime(2026, 7, 15, 3, tzinfo=UTC)) is False
    source = store.load_manifest().sources[0]
    assert source.status == "stale"
    assert source.error_kind is None
    assert (tmp_path / "manifest.json").read_bytes() == after_first


@pytest.mark.parametrize("error_kind", ["HTTPError", "has space", "../error", ""])
def test_failure_rejects_non_stable_error_kinds(tmp_path: Path, error_kind: str) -> None:
    with pytest.raises(ValueError, match="error_kind"):
        DataStore(tmp_path).record_failure("gefs", error_kind)
    assert not (tmp_path / "manifest.json").exists()


def test_publish_rejects_unknown_source_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown source"):
        DataStore(tmp_path).publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC), "../outside"))
    assert list(tmp_path.iterdir()) == []


def test_manifest_replace_failure_removes_new_orphan_and_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace

    def fail_manifest_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination).name == "manifest.json":
            raise OSError("simulated manifest replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(storage_module.os, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="simulated"):
        DataStore(tmp_path).publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))

    assert not (tmp_path / "gefs" / "2026071500.json").exists()
    assert not (tmp_path / "manifest.json").exists()
    assert temporary_files(tmp_path) == []


def test_manifest_replace_failure_restores_existing_cycle_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DataStore(tmp_path)
    original = cycle_at(datetime(2026, 7, 15, tzinfo=UTC))
    store.publish_cycle(original)
    cycle_path = tmp_path / "gefs" / "2026071500.json"
    original_cycle_bytes = cycle_path.read_bytes()
    original_manifest_bytes = (tmp_path / "manifest.json").read_bytes()
    changed = original.model_copy(deep=True)
    changed.storms[0].name = "CHANGED"
    real_replace = os.replace

    def fail_manifest_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination).name == "manifest.json":
            raise OSError("simulated manifest replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(storage_module.os, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="simulated"):
        store.publish_cycle(changed)

    assert cycle_path.read_bytes() == original_cycle_bytes
    assert (tmp_path / "manifest.json").read_bytes() == original_manifest_bytes
    assert temporary_files(tmp_path) == []


def test_load_manifest_rejects_malformed_existing_json(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError):
        DataStore(tmp_path).load_manifest()


def test_validate_tree_reports_malformed_manifest_without_mutation(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{not-json", encoding="utf-8")
    before = manifest_path.read_bytes()

    assert DataStore(tmp_path).validate_tree() == ["manifest.json 格式無效"]
    assert manifest_path.read_bytes() == before


def test_validate_tree_reports_dangling_cycle(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    (tmp_path / "gefs" / "2026071500.json").unlink()

    assert "找不到 cycle 檔案：gefs/2026071500.json" in store.validate_tree()


def test_valid_data_requires_the_latest_manifest_cycle_to_validate(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 14, 18, tzinfo=UTC)))
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    (tmp_path / "gefs" / "2026071500.json").unlink()

    assert store.source_has_valid_data("gefs") is False


@pytest.mark.parametrize(
    "href",
    [
        "gefs/2026071500.json",
        "/data/gefs/../aigefs/2026071500.json",
        "/data/gefs/%2e%2e/2026071500.json",
        "/data/gefs/nested/2026071500.json",
        "/data/unknown/2026071500.json",
        "/data/gefs/2026071500.json?x=1",
        "/data/gefs/2026071500.json#fragment",
        "\\data\\gefs\\2026071500.json",
    ],
)
def test_validate_tree_rejects_non_exact_href(tmp_path: Path, href: str) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    manifest_path = tmp_path / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sources"][0]["cycles"][0]["href"] = href
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert any("cycle href 無效" in error for error in store.validate_tree())


def test_validate_tree_rejects_cycle_cross_field_mismatch(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    cycle_path = tmp_path / "gefs" / "2026071500.json"
    payload = json.loads(cycle_path.read_text(encoding="utf-8"))
    payload["source_id"] = "aigefs"
    cycle_path.write_text(json.dumps(payload), encoding="utf-8")

    assert any("cycle 內容與摘要不一致" in error for error in store.validate_tree())


def test_validate_tree_rejects_orphan_json(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    cycle = cycle_at(datetime(2026, 7, 15, tzinfo=UTC))
    store.publish_cycle(cycle)
    orphan = tmp_path / "gefs" / "2026071418.json"
    orphan.write_bytes(cycle_at(datetime(2026, 7, 14, 18, tzinfo=UTC)).model_dump_json().encode())

    assert "未被 manifest 引用的 cycle 檔案：gefs/2026071418.json" in store.validate_tree()


def test_validate_tree_rejects_symlinked_cycle_without_following_it(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    cycle_path = tmp_path / "gefs" / "2026071500.json"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-cycle.json"
    outside.write_bytes(cycle_path.read_bytes())
    cycle_path.unlink()
    cycle_path.symlink_to(outside)
    outside_before = outside.read_bytes()

    try:
        assert any("符號連結" in error for error in store.validate_tree())
        assert outside.read_bytes() == outside_before
    finally:
        outside.unlink(missing_ok=True)


def test_validate_tree_rejects_symlinked_source_directory(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    source_dir = tmp_path / "gefs"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-source"
    source_dir.rename(outside)
    source_dir.symlink_to(outside, target_is_directory=True)

    try:
        assert "來源目錄不可為符號連結：gefs" in store.validate_tree()
    finally:
        source_dir.unlink(missing_ok=True)
        outside.rename(source_dir)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_source",
        "last_success",
        "cycle_id",
        "cycles_order",
        "empty_mismatch",
    ],
)
def test_validate_tree_rejects_manifest_cross_field_invariants(
    tmp_path: Path, mutation: str
) -> None:
    store = DataStore(tmp_path)
    store.publish_cycle(cycle_at(datetime(2026, 7, 15, tzinfo=UTC)))
    if mutation == "cycles_order":
        store.publish_cycle(cycle_at(datetime(2026, 7, 14, 18, tzinfo=UTC)))
    manifest_path = tmp_path / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = payload["sources"][0]
    if mutation == "duplicate_source":
        payload["sources"][1] = payload["sources"][0]
    elif mutation == "last_success":
        source["last_success_cycle"] = None
    elif mutation == "cycle_id":
        source["cycles"][0]["id"] = "2026071506"
    elif mutation == "cycles_order":
        source["cycles"].reverse()
    else:
        source["cycles"][0]["empty"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert any("manifest invariant" in error for error in store.validate_tree())
