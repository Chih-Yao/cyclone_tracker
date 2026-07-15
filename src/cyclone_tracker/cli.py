from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from cyclone_tracker.adapters.atcf import NcepAtcfAdapter
from cyclone_tracker.adapters.base import SourceAdapter
from cyclone_tracker.adapters.ecmwf import EcmwfBufrAdapter
from cyclone_tracker.config import (
    NCEP_DIRECTORY_NAMES,
    SOURCE_CONFIG_BY_ID,
    SOURCE_CONFIGS,
)
from cyclone_tracker.pipeline import UpdateReport, update_sources
from cyclone_tracker.storage import DataStore


def build_adapters(source_ids: Sequence[str]) -> list[SourceAdapter]:
    adapters: list[SourceAdapter] = []
    for source_id in source_ids:
        if source_id in NCEP_DIRECTORY_NAMES:
            adapters.append(NcepAtcfAdapter(source_id))
        else:
            adapters.append(EcmwfBufrAdapter(source_id))
    return adapters


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cyclone-tracker")
    commands = parser.add_subparsers(dest="command", required=True)
    update = commands.add_parser("update", help="更新靜態氣旋資料")
    update.add_argument("root", nargs="?", type=Path, default=Path("public/data"))
    update.add_argument(
        "--source",
        action="append",
        choices=[source.id for source in SOURCE_CONFIGS],
        dest="sources",
    )
    validate = commands.add_parser("validate", help="驗證靜態資料結構")
    validate.add_argument("root", nargs="?", type=Path, default=Path("public/data"))
    return parser


def _print_report(report: UpdateReport, source_ids: Sequence[str], stdout: TextIO) -> None:
    for source_id in source_ids:
        name = SOURCE_CONFIG_BY_ID[source_id].name_zh_tw
        if source_id in report.succeeded:
            message = "成功更新"
        elif source_id in report.empty:
            message = "成功更新（無符合條件的氣旋）"
        elif source_id in report.unchanged:
            message = "資料未變更"
        else:
            message = f"失敗（{report.failed[source_id]}）"
        print(f"{name}：{message}", file=stdout)


def main(argv: Sequence[str] | None = None, stdout: TextIO = sys.stdout) -> int:
    arguments = _parser().parse_args(argv)
    store = DataStore(arguments.root)
    if arguments.command == "validate":
        errors = store.validate_tree()
        if errors:
            for error in errors:
                print(error, file=stdout)
            return 1
        print("資料結構有效", file=stdout)
        return 0

    source_ids = arguments.sources or [source.id for source in SOURCE_CONFIGS]
    try:
        report = update_sources(
            build_adapters(source_ids),
            store,
            now=datetime.now(UTC),
        )
        _print_report(report, source_ids, stdout)
        return 0 if any(store.source_has_valid_data(item) for item in source_ids) else 1
    except Exception:
        print("更新失敗：資料設定或儲存錯誤", file=stdout)
        return 1
