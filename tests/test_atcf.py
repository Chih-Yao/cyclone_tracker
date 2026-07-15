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


def expected_tracker_files(source_id: str, cycle: datetime) -> list[str]:
    techniques = {
        "gefs": ["ac00", *(f"ap{member:02d}" for member in range(1, 31)), "aemn"],
        "aigefs": [*(f"a{member:03d}" for member in range(31)), "aimn"],
        "aigfs": ["agfs"],
    }[source_id]
    return [f"{technique}.t{cycle:%H}z.cyclone.trackatcfunix" for technique in techniques]


def directory_listing(filenames: list[str]) -> str:
    return "\n".join(f'<a href="{filename}">{filename}</a>' for filename in filenames)


@pytest.fixture
def fixture_text() -> str:
    return Path("tests/fixtures/atcf/wp_tracks.dat").read_text(encoding="ascii")


def test_parse_coordinate_handles_hemispheres() -> None:
    assert parse_atcf_coordinate("152N") == 15.2
    assert parse_atcf_coordinate("1795E") == 179.5
    assert parse_atcf_coordinate("005S") == -0.5
    assert parse_atcf_coordinate("1795W") == -179.5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("000N", 0.0),
        ("900N", 90.0),
        ("900S", -90.0),
        ("1800E", 180.0),
        ("1800W", -180.0),
    ],
)
def test_parse_coordinate_accepts_valid_boundaries(value: str, expected: float) -> None:
    assert parse_atcf_coordinate(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "9999E",
        "-005S",
        "+005N",
        "901N",
        "1200N",
        "1801E",
        "1801W",
        "",
        "N152",
        "15.2N",
        "005 S",
        "005SS",
        "005Q",
    ],
)
def test_parse_coordinate_rejects_malformed_or_out_of_range_tokens(value: str) -> None:
    with pytest.raises(ValueError):
        parse_atcf_coordinate(value)


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
    expected_files = expected_tracker_files("gefs", INITIALIZED_AT)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == first_url:
            return httpx.Response(404)
        if url == selected_url:
            return httpx.Response(
                200,
                text=directory_listing(
                    [
                        *expected_files,
                        "a001.t00z.cyclone.trackatcfunix",
                        "ap01.t00z.cyclone.trackatcfunix",
                    ]
                ),
            )
        filename = request.url.path.rsplit("/", maxsplit=1)[-1]
        if filename in expected_files:
            technique = filename.split(".", maxsplit=1)[0].upper()
            return httpx.Response(
                200,
                text=f"WP, 09, 2026071500, 03, {technique}, 000, 152N, 1300E, 45, 990\n",
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
    assert len(outcome.cycle.storms[0].members) == 32
    assert outcome.cycle.storms[0].mean.points[0].member_count == 31
    assert calls == [
        first_url,
        selected_url,
        *(f"{selected_url}{filename}" for filename in sorted(expected_files)),
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
    assert outcome.error_kind is None


def test_fetch_latest_reports_corrupt_200_tracker_payload_as_error() -> None:
    cycle_url = ncep_cycle_url("aigfs", INITIALIZED_AT)
    expected_filename = expected_tracker_files("aigfs", INITIALIZED_AT)[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == cycle_url:
            return httpx.Response(200, text=directory_listing([expected_filename]))
        return httpx.Response(200, text="<html><body>upstream error</body></html>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is None
    assert outcome.error_kind == "invalid_atcf_payload"


@pytest.mark.parametrize(
    "row",
    [
        "WP, 09, 2026071500, 03, AGFS, 000, 152N, 1300E, nan, 990",
        "WP, 09, 2026071500, 03, AGFS, -006, 152N, 1300E, 45, 990",
        "WP, 09, 2026071500, 03, AGFS, 000, 152N, 1300E, 45, 1301",
        "WP, 09, 2026071400, 03, AGFS, 000, 152N, 1300E, 45, 990",
    ],
    ids=["non-finite-wind", "negative-tau", "invalid-pressure", "cycle-mismatch"],
)
def test_fetch_latest_rejects_atcf_shaped_payload_without_parser_valid_rows(row: str) -> None:
    cycle_url = ncep_cycle_url("aigfs", INITIALIZED_AT)
    expected_filename = expected_tracker_files("aigfs", INITIALIZED_AT)[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == cycle_url:
            return httpx.Response(200, text=directory_listing([expected_filename]))
        return httpx.Response(200, text=f"{row}\n")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is None
    assert outcome.error_kind == "invalid_atcf_payload"


def test_fetch_latest_matches_aigefs_files_but_excludes_postprocessed_variants() -> None:
    cycle_url = ncep_cycle_url("aigefs", INITIALIZED_AT)
    expected_files = expected_tracker_files("aigefs", INITIALIZED_AT)
    requested_files: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        filename = request.url.path.rsplit("/", maxsplit=1)[-1]
        if str(request.url) == cycle_url:
            return httpx.Response(
                200,
                text=directory_listing([*expected_files, "a000p.t00z.cyclone.trackatcfunix"]),
            )
        requested_files.append(filename)
        technique = filename.split(".", maxsplit=1)[0].upper()
        return httpx.Response(
            200,
            text=f"WP, 09, 2026071500, 03, {technique}, 000, 152N, 1300E, 45, 990\n",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigefs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "ok"
    assert requested_files == sorted(expected_files)
    assert outcome.cycle is not None
    assert outcome.cycle.storms[0].mean.points[0].member_count == 31


def test_fetch_latest_falls_back_when_newest_listing_has_only_postprocessed_file() -> None:
    newest_cycle = datetime(2026, 7, 15, 6, tzinfo=UTC)
    newest_url = ncep_cycle_url("aigfs", newest_cycle)
    selected_url = ncep_cycle_url("aigfs", INITIALIZED_AT)
    selected_filename = expected_tracker_files("aigfs", INITIALIZED_AT)[0]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == newest_url:
            return httpx.Response(
                200,
                text=directory_listing(["agfsp.t06z.cyclone.trackatcfunix"]),
            )
        if url == selected_url:
            return httpx.Response(200, text=directory_listing([selected_filename]))
        if url == f"{selected_url}{selected_filename}":
            return httpx.Response(
                200,
                text="WP, 09, 2026071500, 03, AGFS, 000, 152N, 1300E, 45, 990\n",
            )
        raise AssertionError(f"unexpected request: {url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(
            datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
        )

    assert outcome.status == "ok"
    assert outcome.cycle_id == "2026071500"
    assert calls == [newest_url, selected_url, f"{selected_url}{selected_filename}"]


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


def test_fetch_latest_falls_back_when_newest_directory_is_forbidden() -> None:
    newest_cycle = datetime(2026, 7, 15, 6, tzinfo=UTC)
    newest_url = ncep_cycle_url("aigfs", newest_cycle)
    selected_url = ncep_cycle_url("aigfs", INITIALIZED_AT)
    filename = expected_tracker_files("aigfs", INITIALIZED_AT)[0]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == newest_url:
            return httpx.Response(403)
        if url == selected_url:
            return httpx.Response(200, text=directory_listing([filename]))
        if url == f"{selected_url}{filename}":
            return httpx.Response(
                200,
                text="WP, 09, 2026071500, 03, AGFS, 000, 152N, 1300E, 45, 990\n",
            )
        raise AssertionError(f"unexpected request: {url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(
            datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
        )

    assert outcome.status == "ok"
    assert outcome.cycle_id == "2026071500"
    assert calls == [newest_url, selected_url, f"{selected_url}{filename}"]


def test_fetch_latest_reports_http_error_after_all_directories_are_forbidden() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(403)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("gefs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is None
    assert outcome.error_kind == "http_error"
    assert len(calls) == 8


@pytest.mark.parametrize("file_status", [403, 404])
def test_fetch_latest_falls_back_when_newest_tracker_file_is_incomplete(
    file_status: int,
) -> None:
    newest_cycle = datetime(2026, 7, 15, 6, tzinfo=UTC)
    newest_url = ncep_cycle_url("aigfs", newest_cycle)
    newest_filename = expected_tracker_files("aigfs", newest_cycle)[0]
    selected_url = ncep_cycle_url("aigfs", INITIALIZED_AT)
    selected_filename = expected_tracker_files("aigfs", INITIALIZED_AT)[0]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == newest_url:
            return httpx.Response(200, text=directory_listing([newest_filename]))
        if url == f"{newest_url}{newest_filename}":
            return httpx.Response(file_status)
        if url == selected_url:
            return httpx.Response(200, text=directory_listing([selected_filename]))
        if url == f"{selected_url}{selected_filename}":
            return httpx.Response(
                200,
                text="WP, 09, 2026071500, 03, AGFS, 000, 152N, 1300E, 45, 990\n",
            )
        raise AssertionError(f"unexpected request: {url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(
            datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
        )

    assert outcome.status == "ok"
    assert outcome.cycle_id == "2026071500"
    assert outcome.cycle is not None
    assert calls == [
        newest_url,
        f"{newest_url}{newest_filename}",
        selected_url,
        f"{selected_url}{selected_filename}",
    ]


def test_fetch_latest_reports_http_error_after_all_tracker_files_are_incomplete() -> None:
    now = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
    cycles = candidate_cycles(now, count=8)
    directories = {
        ncep_cycle_url("aigfs", cycle): expected_tracker_files("aigfs", cycle)[0]
        for cycle in cycles
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url in directories:
            return httpx.Response(200, text=directory_listing([directories[url]]))
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(now)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071506"
    assert outcome.cycle is None
    assert outcome.error_kind == "http_error"
    assert len(calls) == 16


@pytest.mark.parametrize("file_status", [401, 500])
def test_fetch_latest_reports_other_tracker_file_http_failures_immediately(
    file_status: int,
) -> None:
    cycle_url = ncep_cycle_url("aigfs", INITIALIZED_AT)
    filename = expected_tracker_files("aigfs", INITIALIZED_AT)[0]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == cycle_url:
            return httpx.Response(200, text=directory_listing([filename]))
        return httpx.Response(file_status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071500"
    assert outcome.error_kind == "http_error"
    assert calls == [cycle_url, f"{cycle_url}{filename}"]


def test_fetch_latest_reports_tracker_file_network_failure_immediately() -> None:
    cycle_url = ncep_cycle_url("aigfs", INITIALIZED_AT)
    filename = expected_tracker_files("aigfs", INITIALIZED_AT)[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == cycle_url:
            return httpx.Response(200, text=directory_listing([filename]))
        raise httpx.ConnectError("network unavailable", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = NcepAtcfAdapter("aigfs", client=client).fetch_latest(INITIALIZED_AT)

    assert outcome.status == "error"
    assert outcome.cycle_id == "2026071500"
    assert outcome.error_kind == "network_error"
