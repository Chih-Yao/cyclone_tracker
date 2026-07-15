from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from cyclone_tracker.adapters.atcf import (
    NcepAtcfAdapter,
    candidate_cycles,
    parse_atcf_coordinate,
    parse_atcf_text,
)
from cyclone_tracker.config import ncep_cycle_url

INITIALIZED_AT = datetime(2026, 7, 15, tzinfo=UTC)


@pytest.fixture
def fixture_text() -> str:
    return Path("tests/fixtures/atcf/wp_tracks.dat").read_text(encoding="ascii")


def test_parse_coordinate_handles_hemispheres() -> None:
    assert parse_atcf_coordinate("152N") == 15.2
    assert parse_atcf_coordinate("1795E") == 179.5
    assert parse_atcf_coordinate("005S") == -0.5
    assert parse_atcf_coordinate("1795W") == -179.5


def test_parse_atcf_filters_wp_and_builds_named_and_invest_storms(fixture_text: str) -> None:
    cycle = parse_atcf_text(
        fixture_text,
        source_id="gefs",
        initialized_at=INITIALIZED_AT,
    )

    assert [storm.id for storm in cycle.storms] == ["09W", "90W"]
    assert cycle.storms[0].invest is False
    assert cycle.storms[1].invest is True
    assert cycle.storms[0].members[0].points[0].wind_source_unit == "kt"


def test_parse_atcf_maps_member_types_and_sorts_them(fixture_text: str) -> None:
    cycle = parse_atcf_text(fixture_text, source_id="gefs", initialized_at=INITIALIZED_AT)
    named, invest = cycle.storms

    assert [(member.id, member.member_type) for member in named.members] == [
        ("AC00", "control"),
        ("AP01", "perturbed"),
        ("AEMN", "source_mean"),
    ]
    assert [(member.id, member.member_type) for member in invest.members] == [
        ("A000", "control"),
        ("A001", "perturbed"),
        ("AGFS", "deterministic"),
        ("AIMN", "source_mean"),
    ]


def test_parse_atcf_keeps_last_complete_duplicate_and_ignores_incomplete_one(
    fixture_text: str,
) -> None:
    cycle = parse_atcf_text(fixture_text, source_id="gefs", initialized_at=INITIALIZED_AT)
    ap01 = next(member for member in cycle.storms[0].members if member.id == "AP01")

    assert [point.tau_h for point in ap01.points] == [0, 6, 12]
    assert ap01.points[1].lat == 17.0
    assert ap01.points[1].wind_kt == 60.0
    assert ap01.points[1].pressure_hpa == 980.0
    assert ap01.points[2].lat == 18.0


def test_parse_atcf_excludes_source_mean_from_computed_mean(fixture_text: str) -> None:
    cycle = parse_atcf_text(fixture_text, source_id="gefs", initialized_at=INITIALIZED_AT)
    named = cycle.storms[0]

    assert named.mean.points[0].member_count == 2
    assert named.mean.points[0].wind_kt == 50.0


def test_parse_atcf_warns_with_unrecognized_technique_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = "\n".join(
        [
            "WP, 09, 2026071500, 03, UNKNOWN, 000, 152N, 1300E, 45, 990",
            "WP, 09, 2026071500, 03, OTHER, 006, 160N, 1310E, 50, 985",
        ]
    )

    with caplog.at_level(logging.WARNING):
        cycle = parse_atcf_text(text, source_id="gefs", initialized_at=INITIALIZED_AT)

    assert cycle.storms == []
    assert "ignored 2 ATCF rows with unrecognized techniques" in caplog.text


def test_candidate_cycles_are_latest_first_on_six_hour_boundaries() -> None:
    now = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
    assert candidate_cycles(now, count=3) == [
        datetime(2026, 7, 15, 6, tzinfo=UTC),
        datetime(2026, 7, 15, 0, tzinfo=UTC),
        datetime(2026, 7, 14, 18, tzinfo=UTC),
    ]


def test_ncep_cycle_url_uses_source_directory_and_cycle() -> None:
    assert ncep_cycle_url("aigefs", INITIALIZED_AT) == (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/ens_tracker/prod/"
        "aigefs.20260715/00/tctrack/"
    )


def test_fetch_latest_falls_back_and_combines_only_matching_gefs_files() -> None:
    first_url = ncep_cycle_url("gefs", datetime(2026, 7, 15, 6, tzinfo=UTC))
    selected_url = ncep_cycle_url("gefs", INITIALIZED_AT)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == first_url:
            return httpx.Response(404)
        if url == selected_url:
            return httpx.Response(
                200,
                text="""
                    <a href="ap01.t00z.cyclone.trackatcfunix">AP01</a>
                    <a href="a001.t00z.cyclone.trackatcfunix">wrong model</a>
                    <a href="ac00.t00z.cyclone.trackatcfunix">AC00</a>
                    <a href="ap01.t00z.cyclone.trackatcfunix">duplicate link</a>
                """,
            )
        if url == f"{selected_url}ac00.t00z.cyclone.trackatcfunix":
            return httpx.Response(
                200,
                text="WP, 09, 2026071500, 03, AC00, 000, 152N, 1300E, 45, 990\n",
            )
        if url == f"{selected_url}ap01.t00z.cyclone.trackatcfunix":
            return httpx.Response(
                200,
                text="WP, 09, 2026071500, 03, AP01, 000, 154N, 1305E, 55, 985\n",
            )
        raise AssertionError(f"unexpected request: {url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("gefs", client=client).fetch_latest(
            datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
        )

    assert outcome.status == "ok"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is not None
    assert outcome.cycle.source_id == "gefs"
    assert [member.id for member in outcome.cycle.storms[0].members] == ["AC00", "AP01"]
    assert outcome.cycle.storms[0].mean.points[0].member_count == 2
    assert calls == [
        first_url,
        selected_url,
        f"{selected_url}ac00.t00z.cyclone.trackatcfunix",
        f"{selected_url}ap01.t00z.cyclone.trackatcfunix",
    ]


def test_fetch_latest_returns_empty_for_published_cycle_without_supported_wp_tracks() -> None:
    cycle_url = ncep_cycle_url("aigfs", INITIALIZED_AT)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == cycle_url:
            return httpx.Response(
                200,
                text='<a href="agfs.t00z.cyclone.trackatcfunix">AGFS</a>',
            )
        return httpx.Response(
            200,
            text="EP, 05, 2026071500, 03, AGFS, 000, 152N, 1100W, 45, 990\n",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "empty"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is not None
    assert outcome.cycle.storms == []


def test_fetch_latest_matches_aigefs_files_but_excludes_postprocessed_variants() -> None:
    cycle_url = ncep_cycle_url("aigefs", INITIALIZED_AT)
    requested_files: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        filename = request.url.path.rsplit("/", maxsplit=1)[-1]
        if str(request.url) == cycle_url:
            return httpx.Response(
                200,
                text="""
                    <a href="a000.t00z.cyclone.trackatcfunix">A000</a>
                    <a href="a000p.t00z.cyclone.trackatcfunix">A000 postprocessed</a>
                    <a href="aimn.t00z.cyclone.trackatcfunix">AIMN</a>
                """,
            )
        requested_files.append(filename)
        technique = "A000" if filename.startswith("a000.") else "AIMN"
        return httpx.Response(
            200,
            text=f"WP, 09, 2026071500, 03, {technique}, 000, 152N, 1300E, 45, 990\n",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigefs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "ok"
    assert requested_files == [
        "a000.t00z.cyclone.trackatcfunix",
        "aimn.t00z.cyclone.trackatcfunix",
    ]
    assert outcome.cycle is not None
    assert outcome.cycle.storms[0].mean.points[0].member_count == 1


def test_fetch_latest_returns_unavailable_after_eight_missing_cycles() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("gefs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "unavailable"
    assert outcome.cycle_id is None
    assert outcome.cycle is None
    assert outcome.error_kind == "cycle_directory_not_found"
    assert len(calls) == 8


def test_fetch_latest_reports_non_missing_http_failure_as_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("gefs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is None
    assert outcome.error_kind == "http_error"
