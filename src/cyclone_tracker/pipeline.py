from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from cyclone_tracker.adapters.base import AdapterOutcome, SourceAdapter
from cyclone_tracker.config import SOURCE_CONFIG_BY_ID
from cyclone_tracker.storage import DataStore

_ERROR_KIND = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


@dataclass
class UpdateReport:
    succeeded: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def _valid_outcome(adapter_source_id: str, outcome: AdapterOutcome) -> bool:
    if outcome.source_id != adapter_source_id:
        return False
    if outcome.status in {"ok", "empty"}:
        if outcome.cycle is None or outcome.cycle_id is None or outcome.error_kind is not None:
            return False
        if outcome.cycle.source_id != adapter_source_id:
            return False
        if outcome.cycle_id != outcome.cycle.initialized_at.strftime("%Y%m%d%H"):
            return False
        return bool(outcome.cycle.storms) == (outcome.status == "ok")
    if outcome.cycle is not None:
        return False
    if outcome.status == "error":
        return (
            outcome.error_kind is not None and _ERROR_KIND.fullmatch(outcome.error_kind) is not None
        )
    return outcome.status == "unavailable"


def update_sources(
    adapters: Iterable[SourceAdapter],
    store: DataStore,
    *,
    now: datetime,
) -> UpdateReport:
    adapter_list = list(adapters)
    source_ids = [adapter.source_id for adapter in adapter_list]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("duplicate adapter source")
    unknown = [source_id for source_id in source_ids if source_id not in SOURCE_CONFIG_BY_ID]
    if unknown:
        raise ValueError(f"unknown adapter source: {unknown[0]}")

    report = UpdateReport()
    for adapter in adapter_list:
        try:
            outcome = adapter.fetch_latest(now)
        except Exception:
            store.record_failure(adapter.source_id, "unexpected_error", now=now)
            report.failed[adapter.source_id] = "unexpected_error"
            continue

        if not _valid_outcome(adapter.source_id, outcome):
            store.record_failure(adapter.source_id, "invalid_adapter_outcome", now=now)
            report.failed[adapter.source_id] = "invalid_adapter_outcome"
            continue
        if outcome.status == "unavailable":
            store.record_unavailable(adapter.source_id, now=now)
            report.failed[adapter.source_id] = "unavailable"
            continue
        if outcome.status == "error":
            assert outcome.error_kind is not None
            store.record_failure(adapter.source_id, outcome.error_kind, now=now)
            report.failed[adapter.source_id] = outcome.error_kind
            continue

        assert outcome.cycle is not None
        changed = store.publish_cycle(outcome.cycle, now=now)
        if not changed:
            report.unchanged.append(adapter.source_id)
        elif outcome.status == "empty":
            report.empty.append(adapter.source_id)
        else:
            report.succeeded.append(adapter.source_id)
    return report
