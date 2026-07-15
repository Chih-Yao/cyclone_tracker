import csv
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

import httpx
from pydantic import ValidationError

from cyclone_tracker.adapters.base import AdapterOutcome
from cyclone_tracker.config import NCEP_DIRECTORY_NAMES, ncep_cycle_url
from cyclone_tracker.mean import compute_mean_track
from cyclone_tracker.models import CycleData, MeanTrack, MemberTrack, Storm, TrackPoint

logger = logging.getLogger(__name__)

_TECHNIQUE_TYPES = {
    "AC00": "control",
    **{f"AP{member:02d}": "perturbed" for member in range(1, 31)},
    "A000": "control",
    **{f"A{member:03d}": "perturbed" for member in range(1, 31)},
    "AGFS": "deterministic",
    "AEMN": "source_mean",
    "AIMN": "source_mean",
}
_MEMBER_TYPE_ORDER = {
    "control": 0,
    "perturbed": 1,
    "deterministic": 2,
    "source_mean": 3,
}
_FILE_PREFIX_PATTERNS = {
    "gefs": r"(?:ac00|ap(?:0[1-9]|[12][0-9]|30)|aemn)",
    "aigefs": r"(?:a0(?:0[0-9]|[12][0-9]|30)|aimn)",
    "aigfs": r"agfs",
}
_EXPECTED_TECHNIQUES = {
    "gefs": ("ac00", *(f"ap{member:02d}" for member in range(1, 31)), "aemn"),
    "aigefs": (*(f"a{member:03d}" for member in range(31)), "aimn"),
    "aigfs": ("agfs",),
}


class _DirectoryLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        for name, value in attrs:
            if name.casefold() == "href" and value is not None:
                self.hrefs.append(value)


def parse_atcf_coordinate(value: str) -> float:
    coordinate = value.strip().upper()
    match = re.fullmatch(r"([0-9]+)([NSEW])", coordinate)
    if match is None:
        raise ValueError(f"invalid ATCF coordinate: {value!r}")
    magnitude_tenths = int(match.group(1))
    hemisphere = match.group(2)
    maximum = 900 if hemisphere in "NS" else 1800
    if magnitude_tenths > maximum:
        raise ValueError(f"ATCF coordinate out of range: {value!r}")
    magnitude = magnitude_tenths / 10.0
    return -magnitude if hemisphere in "SW" else magnitude


def _parse_pressure(value: str) -> float | None:
    if not value:
        return None
    pressure = float(value)
    return pressure if pressure > 0 else None


def _contains_syntactically_valid_atcf_row(text: str) -> bool:
    for row in csv.reader(text.splitlines()):
        fields = [field.strip() for field in row[:10]]
        if len(fields) < 10:
            continue
        basin, number, cycle, _, technique, tau, lat, lon, wind, pressure = fields
        if len(basin) != 2 or not basin.isalpha() or not technique:
            continue
        if not lat.upper().endswith(("N", "S")) or not lon.upper().endswith(("E", "W")):
            continue
        try:
            int(number)
            datetime.strptime(cycle, "%Y%m%d%H")
            int(tau)
            parse_atcf_coordinate(lat)
            parse_atcf_coordinate(lon)
            float(wind)
            _parse_pressure(pressure)
        except ValueError:
            continue
        return True
    return False


def parse_atcf_text(text: str, *, source_id: str, initialized_at: datetime) -> CycleData:
    initialized_at_utc = initialized_at.astimezone(UTC)
    records: dict[str, dict[str, dict[int, TrackPoint]]] = defaultdict(lambda: defaultdict(dict))
    unrecognized_count = 0

    for row in csv.reader(text.splitlines()):
        fields = [field.strip() for field in row[:10]]
        if len(fields) < 10:
            continue
        basin, number_text, cycle_text, _, technique, tau_text, lat, lon, wind, pressure = fields
        technique = technique.upper()

        try:
            number = int(number_text)
        except ValueError:
            continue
        if basin.upper() != "WP" or not (1 <= number <= 49 or 90 <= number <= 99):
            continue

        member_type = _TECHNIQUE_TYPES.get(technique)
        if member_type is None:
            unrecognized_count += 1
            continue

        try:
            row_initialized_at = datetime.strptime(cycle_text, "%Y%m%d%H").replace(tzinfo=UTC)
            if row_initialized_at != initialized_at_utc:
                continue
            tau_h = int(tau_text)
            point = TrackPoint(
                tau_h=tau_h,
                valid_at=initialized_at_utc + timedelta(hours=tau_h),
                lat=parse_atcf_coordinate(lat),
                lon=parse_atcf_coordinate(lon),
                wind_kt=float(wind),
                wind_source_value=float(wind),
                wind_source_unit="kt",
                pressure_hpa=_parse_pressure(pressure),
            )
        except (ValueError, ValidationError):
            continue

        storm_id = f"{number:02d}W"
        records[storm_id][technique][tau_h] = point

    if unrecognized_count:
        logger.warning(
            "ignored %d ATCF rows with unrecognized techniques",
            unrecognized_count,
        )

    storms: list[Storm] = []
    for storm_id in sorted(records):
        members: list[MemberTrack] = []
        for technique, points_by_tau in records[storm_id].items():
            members.append(
                MemberTrack(
                    id=technique,
                    member_type=_TECHNIQUE_TYPES[technique],
                    points=[points_by_tau[tau_h] for tau_h in sorted(points_by_tau)],
                )
            )
        members.sort(key=lambda member: (_MEMBER_TYPE_ORDER[member.member_type], member.id))
        number = int(storm_id[:2])
        storms.append(
            Storm(
                id=storm_id,
                name=None,
                basin="WP",
                invest=90 <= number <= 99,
                members=members,
                mean=MeanTrack(points=compute_mean_track(members)),
            )
        )

    return CycleData(source_id=source_id, initialized_at=initialized_at_utc, storms=storms)


def candidate_cycles(now: datetime, *, count: int = 8) -> list[datetime]:
    now_utc = now.astimezone(UTC)
    latest = now_utc.replace(
        hour=(now_utc.hour // 6) * 6,
        minute=0,
        second=0,
        microsecond=0,
    )
    return [latest - timedelta(hours=6 * offset) for offset in range(count)]


def _matching_tracker_files(source_id: str, cycle: datetime, listing: str) -> list[str]:
    parser = _DirectoryLinkParser()
    parser.feed(listing)
    pattern = re.compile(
        rf"{_FILE_PREFIX_PATTERNS[source_id]}\.t{cycle:%H}z\.cyclone\.trackatcfunix",
        re.IGNORECASE,
    )
    filenames = {unquote(urlparse(href).path).rsplit("/", maxsplit=1)[-1] for href in parser.hrefs}
    return sorted(filename for filename in filenames if pattern.fullmatch(filename))


def _expected_tracker_files(source_id: str, cycle: datetime) -> set[str]:
    return {
        f"{technique}.t{cycle:%H}z.cyclone.trackatcfunix"
        for technique in _EXPECTED_TECHNIQUES[source_id]
    }


class NcepAtcfAdapter:
    def __init__(self, source_id: str, *, client: httpx.Client | None = None) -> None:
        if source_id not in NCEP_DIRECTORY_NAMES:
            raise ValueError(f"unsupported NCEP source: {source_id}")
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
            directory_url = ncep_cycle_url(self.source_id, cycle)
            try:
                response = client.get(directory_url)
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

            filenames = _matching_tracker_files(self.source_id, cycle, response.text)
            if set(filenames) != _expected_tracker_files(self.source_id, cycle):
                continue
            file_texts: list[str] = []
            for filename in filenames:
                try:
                    file_response = client.get(f"{directory_url}{filename}")
                except httpx.RequestError:
                    return AdapterOutcome(
                        source_id=self.source_id,
                        cycle_id=cycle_id,
                        status="error",
                        error_kind="network_error",
                    )
                if not file_response.is_success:
                    return AdapterOutcome(
                        source_id=self.source_id,
                        cycle_id=cycle_id,
                        status="error",
                        error_kind="http_error",
                    )
                if not _contains_syntactically_valid_atcf_row(file_response.text):
                    return AdapterOutcome(
                        source_id=self.source_id,
                        cycle_id=cycle_id,
                        status="error",
                        error_kind="invalid_atcf_payload",
                    )
                file_texts.append(file_response.text)

            cycle_data = parse_atcf_text(
                "\n".join(file_texts),
                source_id=self.source_id,
                initialized_at=cycle,
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
            error_kind="cycle_directory_not_found",
        )
