from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ValidationError

from cyclone_tracker.config import SOURCE_CONFIG_BY_ID, SOURCE_CONFIGS
from cyclone_tracker.models import (
    CycleData,
    CycleSummary,
    Manifest,
    SourceState,
    cycle_to_json_bytes,
    manifest_to_json_bytes,
)

_BOOTSTRAP_TIME = datetime(1970, 1, 1, tzinfo=UTC)
_CYCLE_ID = re.compile(r"^\d{10}$")
_ERROR_KIND = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_MAX_CYCLES = 12


def _bootstrap_manifest() -> Manifest:
    return Manifest(
        generated_at=_BOOTSTRAP_TIME,
        sources=[
            SourceState(
                id=config.id,
                name_zh_tw=config.name_zh_tw,
                attribution_url=config.attribution_url,
                status="stale",
                last_success_cycle=None,
                stale_after_hours=12,
                error_kind=None,
                cycles=[],
            )
            for config in SOURCE_CONFIGS
        ],
    )


def _cycle_id(initialized_at: datetime) -> str:
    return initialized_at.astimezone(UTC).strftime("%Y%m%d%H")


def _expected_href(source_id: str, cycle_id: str) -> str:
    return f"/data/{source_id}/{cycle_id}.json"


def _source_for(manifest: Manifest, source_id: str) -> SourceState:
    if source_id not in SOURCE_CONFIG_BY_ID:
        raise ValueError(f"unknown source: {source_id}")
    return next(source for source in manifest.sources if source.id == source_id)


def _manifest_invariant_errors(manifest: Manifest) -> list[str]:
    errors: list[str] = []
    expected_ids = [config.id for config in SOURCE_CONFIGS]
    source_ids = [source.id for source in manifest.sources]
    if source_ids != expected_ids:
        errors.append("manifest invariant：來源必須是固定且唯一的五個來源")
        return errors

    for source, config in zip(manifest.sources, SOURCE_CONFIGS, strict=True):
        if (
            source.name_zh_tw != config.name_zh_tw
            or source.attribution_url != config.attribution_url
        ):
            errors.append(f"manifest invariant：{source.id} 的來源中繼資料不符")
        if source.stale_after_hours <= 0:
            errors.append(f"manifest invariant：{source.id} stale_after_hours 必須為正數")
        if len(source.cycles) > _MAX_CYCLES:
            errors.append(f"manifest invariant：{source.id} cycle 超過 {_MAX_CYCLES} 筆")
        cycle_ids = [cycle.id for cycle in source.cycles]
        if len(cycle_ids) != len(set(cycle_ids)):
            errors.append(f"manifest invariant：{source.id} cycle ID 重複")
        initialized_values = [cycle.initialized_at for cycle in source.cycles]
        if initialized_values != sorted(initialized_values, reverse=True):
            errors.append(f"manifest invariant：{source.id} cycles 未依新到舊排序")
        expected_last = source.cycles[0].id if source.cycles else None
        if source.last_success_cycle != expected_last:
            errors.append(f"manifest invariant：{source.id} last_success_cycle 不符")
        for summary in source.cycles:
            if summary.id != _cycle_id(summary.initialized_at):
                errors.append(f"manifest invariant：{source.id} cycle ID 與時間不符")
            if summary.href != _expected_href(source.id, summary.id):
                errors.append(f"cycle href 無效：{summary.href}")
        if source.status == "ok" and (not source.cycles or source.cycles[0].empty):
            errors.append(f"manifest invariant：{source.id} ok 狀態與最新 cycle 不符")
        if source.status == "empty" and (not source.cycles or not source.cycles[0].empty):
            errors.append(f"manifest invariant：{source.id} empty 狀態與最新 cycle 不符")
        if source.status == "error":
            if source.error_kind is None or _ERROR_KIND.fullmatch(source.error_kind) is None:
                errors.append(f"manifest invariant：{source.id} error_kind 無效")
        elif source.error_kind is not None:
            errors.append(f"manifest invariant：{source.id} 非 error 狀態不可有 error_kind")
    return errors


def _validate_model_bytes[ModelT: BaseModel](data: bytes, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate_json(data)


class DataStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def _require_safe_root(self) -> None:
        if self.root.is_symlink():
            raise ValueError("data root must not be a symlink")

    def load_manifest(self) -> Manifest:
        self._require_safe_root()
        if self.manifest_path.is_symlink():
            raise ValueError("manifest.json must not be a symlink")
        if not self.manifest_path.exists():
            return _bootstrap_manifest()
        try:
            manifest = Manifest.model_validate_json(self.manifest_path.read_bytes())
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid manifest.json") from error
        invariant_errors = _manifest_invariant_errors(manifest)
        if invariant_errors:
            raise ValueError(invariant_errors[0])
        return manifest

    def _cycle_path(self, source_id: str, cycle_id: str) -> Path:
        if source_id not in SOURCE_CONFIG_BY_ID:
            raise ValueError(f"unknown source: {source_id}")
        if _CYCLE_ID.fullmatch(cycle_id) is None:
            raise ValueError(f"invalid cycle id: {cycle_id}")
        source_dir = self.root / source_id
        if source_dir.is_symlink():
            raise ValueError(f"source directory must not be a symlink: {source_id}")
        path = source_dir / f"{cycle_id}.json"
        if path.is_symlink():
            raise ValueError(f"cycle path must not be a symlink: {source_id}/{cycle_id}")
        return path

    @staticmethod
    def _prepare_atomic_file(
        destination: Path,
        data: bytes,
        validator: Callable[[bytes], object],
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            validator(temporary_path.read_bytes())
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return temporary_path

    @staticmethod
    def _restore_cycle(path: Path, previous: bytes | None) -> None:
        if previous is None:
            path.unlink(missing_ok=True)
            return
        temporary = DataStore._prepare_atomic_file(
            path,
            previous,
            lambda data: _validate_model_bytes(data, CycleData),
        )
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def publish_cycle(self, cycle: CycleData, *, now: datetime | None = None) -> bool:
        self._require_safe_root()
        if cycle.source_id not in SOURCE_CONFIG_BY_ID:
            raise ValueError(f"unknown source: {cycle.source_id}")
        cycle_bytes = cycle_to_json_bytes(cycle)
        validated_cycle = _validate_model_bytes(cycle_bytes, CycleData)
        if validated_cycle != cycle:
            raise ValueError("cycle changed during schema validation")

        manifest = self.load_manifest()
        source = _source_for(manifest, cycle.source_id)
        cycle_id = _cycle_id(cycle.initialized_at)
        cycle_path = self._cycle_path(cycle.source_id, cycle_id)
        existing_ids = {summary.id for summary in source.cycles}
        if (
            cycle_id not in existing_ids
            and len(source.cycles) >= _MAX_CYCLES
            and cycle.initialized_at < source.cycles[-1].initialized_at
        ):
            return False

        summary = CycleSummary(
            id=cycle_id,
            initialized_at=cycle.initialized_at,
            href=_expected_href(cycle.source_id, cycle_id),
            storms=[storm.id for storm in cycle.storms],
            empty=not cycle.storms,
        )
        summaries = [item for item in source.cycles if item.id != cycle_id]
        summaries.append(summary)
        summaries.sort(key=lambda item: item.initialized_at, reverse=True)
        retained = summaries[:_MAX_CYCLES]
        removed = summaries[_MAX_CYCLES:]
        latest = retained[0]
        status = "empty" if latest.empty else "ok"
        effective_now = now.astimezone(UTC) if now is not None else cycle.initialized_at
        if effective_now - latest.initialized_at > timedelta(hours=source.stale_after_hours):
            status = "stale"

        candidate = manifest.model_copy(deep=True)
        candidate_source = _source_for(candidate, cycle.source_id)
        candidate_source.cycles = retained
        candidate_source.last_success_cycle = retained[0].id
        candidate_source.status = status
        candidate_source.error_kind = None
        current_cycle_bytes = cycle_path.read_bytes() if cycle_path.exists() else None
        cycle_changed = current_cycle_bytes != cycle_bytes

        comparison = candidate.model_copy(deep=True)
        comparison.generated_at = manifest.generated_at
        manifest_state_changed = comparison != manifest
        if not cycle_changed and not manifest_state_changed:
            return False
        candidate.generated_at = effective_now
        candidate_bytes = manifest_to_json_bytes(candidate)
        validated_manifest = _validate_model_bytes(candidate_bytes, Manifest)
        invariant_errors = _manifest_invariant_errors(validated_manifest)
        if invariant_errors:
            raise ValueError(invariant_errors[0])

        cycle_temp: Path | None = None
        manifest_temp: Path | None = None
        try:
            if cycle_changed:
                cycle_temp = self._prepare_atomic_file(
                    cycle_path,
                    cycle_bytes,
                    lambda data: _validate_model_bytes(data, CycleData),
                )
            manifest_temp = self._prepare_atomic_file(
                self.manifest_path,
                candidate_bytes,
                lambda data: _validate_model_bytes(data, Manifest),
            )
            if cycle_temp is not None:
                os.replace(cycle_temp, cycle_path)
                cycle_temp = None
            try:
                os.replace(manifest_temp, self.manifest_path)
                manifest_temp = None
            except BaseException:
                if cycle_changed:
                    self._restore_cycle(cycle_path, current_cycle_bytes)
                raise
        finally:
            if cycle_temp is not None:
                cycle_temp.unlink(missing_ok=True)
            if manifest_temp is not None:
                manifest_temp.unlink(missing_ok=True)

        retained_ids = {item.id for item in retained}
        for old_summary in removed:
            if old_summary.id not in retained_ids:
                self._cycle_path(cycle.source_id, old_summary.id).unlink(missing_ok=True)
        return True

    def _record_status(
        self,
        source_id: str,
        *,
        status: str,
        error_kind: str | None,
        now: datetime | None,
    ) -> bool:
        self._require_safe_root()
        manifest = self.load_manifest()
        source = _source_for(manifest, source_id)
        if source.status == status and source.error_kind == error_kind:
            return False
        candidate = manifest.model_copy(deep=True)
        candidate_source = _source_for(candidate, source_id)
        candidate_source.status = status  # type: ignore[assignment]
        candidate_source.error_kind = error_kind
        candidate.generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        candidate_bytes = manifest_to_json_bytes(candidate)
        validated = _validate_model_bytes(candidate_bytes, Manifest)
        invariant_errors = _manifest_invariant_errors(validated)
        if invariant_errors:
            raise ValueError(invariant_errors[0])
        temporary = self._prepare_atomic_file(
            self.manifest_path,
            candidate_bytes,
            lambda data: _validate_model_bytes(data, Manifest),
        )
        try:
            os.replace(temporary, self.manifest_path)
        finally:
            temporary.unlink(missing_ok=True)
        return True

    def record_failure(
        self,
        source_id: str,
        error_kind: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        if _ERROR_KIND.fullmatch(error_kind) is None:
            raise ValueError("error_kind must be stable snake_case")
        return self._record_status(
            source_id,
            status="error",
            error_kind=error_kind,
            now=now,
        )

    def record_unavailable(self, source_id: str, *, now: datetime | None = None) -> bool:
        return self._record_status(source_id, status="stale", error_kind=None, now=now)

    @staticmethod
    def _summary_matches_cycle(
        source_id: str,
        summary: CycleSummary,
        cycle: CycleData,
    ) -> bool:
        return (
            cycle.source_id == source_id
            and cycle.initialized_at == summary.initialized_at
            and _cycle_id(cycle.initialized_at) == summary.id
            and [storm.id for storm in cycle.storms] == summary.storms
            and (not cycle.storms) == summary.empty
        )

    def _read_referenced_cycle(
        self,
        source_id: str,
        summary: CycleSummary,
    ) -> CycleData | None:
        if summary.href != _expected_href(source_id, summary.id):
            return None
        try:
            path = self._cycle_path(source_id, summary.id)
        except ValueError:
            return None
        if not path.is_file() or path.is_symlink():
            return None
        try:
            cycle = CycleData.model_validate_json(path.read_bytes())
        except (OSError, ValidationError, ValueError):
            return None
        return cycle if self._summary_matches_cycle(source_id, summary, cycle) else None

    def source_has_valid_data(self, source_id: str) -> bool:
        manifest = self.load_manifest()
        source = _source_for(manifest, source_id)
        return bool(
            source.cycles and self._read_referenced_cycle(source_id, source.cycles[0]) is not None
        )

    def validate_tree(self) -> list[str]:
        if self.root.is_symlink():
            return ["資料目錄不可為符號連結"]
        if self.manifest_path.is_symlink():
            return ["manifest.json 不可為符號連結"]
        if not self.manifest_path.exists():
            return ["找不到 manifest.json"]
        try:
            manifest = Manifest.model_validate_json(self.manifest_path.read_bytes())
        except (OSError, ValidationError, ValueError, json.JSONDecodeError):
            return ["manifest.json 格式無效"]

        errors = _manifest_invariant_errors(manifest)
        referenced: set[Path] = set()
        for source in manifest.sources:
            if source.id not in SOURCE_CONFIG_BY_ID:
                continue
            source_dir = self.root / source.id
            if source_dir.is_symlink():
                errors.append(f"來源目錄不可為符號連結：{source.id}")
                continue
            for summary in source.cycles:
                if summary.href != _expected_href(source.id, summary.id):
                    continue
                direct_path = source_dir / f"{summary.id}.json"
                if direct_path.is_symlink():
                    errors.append(f"cycle 檔案不可為符號連結：{source.id}/{summary.id}.json")
                    continue
                try:
                    path = self._cycle_path(source.id, summary.id)
                except ValueError:
                    errors.append(f"cycle 路徑無效：{source.id}/{summary.id}.json")
                    continue
                referenced.add(path)
                if not path.is_file():
                    errors.append(f"找不到 cycle 檔案：{source.id}/{summary.id}.json")
                    continue
                try:
                    cycle = CycleData.model_validate_json(path.read_bytes())
                except (OSError, ValidationError, ValueError):
                    errors.append(f"cycle 檔案格式無效：{source.id}/{summary.id}.json")
                    continue
                if not self._summary_matches_cycle(source.id, summary, cycle):
                    errors.append(f"cycle 內容與摘要不一致：{source.id}/{summary.id}.json")

        if self.root.exists():
            for entry in sorted(self.root.iterdir(), key=lambda path: path.name):
                if entry == self.manifest_path:
                    continue
                if entry.name not in SOURCE_CONFIG_BY_ID:
                    errors.append(f"未知的資料項目：{entry.name}")
                    continue
                if entry.is_symlink() or not entry.is_dir():
                    continue
                for child in sorted(entry.iterdir(), key=lambda path: path.name):
                    if child.is_symlink():
                        if child not in referenced:
                            errors.append(
                                f"未被 manifest 引用的 cycle 檔案：{entry.name}/{child.name}"
                            )
                        continue
                    if child.is_file() and child.suffix == ".json" and child not in referenced:
                        errors.append(f"未被 manifest 引用的 cycle 檔案：{entry.name}/{child.name}")
                    elif child.is_dir():
                        errors.append(f"cycle 目錄不可巢狀：{entry.name}/{child.name}")
        return sorted(set(errors))
