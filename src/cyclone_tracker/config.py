from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name_zh_tw: str
    attribution_url: str


SOURCE_CONFIGS = (
    SourceConfig("gefs", "NCEP GEFS", "https://nomads.ncep.noaa.gov/"),
    SourceConfig("aigefs", "NCEP AIGEFS", "https://nomads.ncep.noaa.gov/"),
    SourceConfig("aigfs", "NCEP AIGFS", "https://nomads.ncep.noaa.gov/"),
    SourceConfig(
        "ifs-ens",
        "ECMWF IFS ENS",
        "https://www.ecmwf.int/en/forecasts/datasets/open-data",
    ),
    SourceConfig(
        "aifs-ens",
        "ECMWF AIFS ENS",
        "https://www.ecmwf.int/en/forecasts/datasets/open-data",
    ),
)
SOURCE_CONFIG_BY_ID = {source.id: source for source in SOURCE_CONFIGS}

SOURCE_NAMES_ZH_TW = {source.id: source.name_zh_tw for source in SOURCE_CONFIGS}

NCEP_TRACKER_ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/ens_tracker/prod"
NCEP_DIRECTORY_NAMES = {"gefs": "gefs", "aigefs": "aigefs", "aigfs": "aigfs"}


def ncep_cycle_url(source_id: str, cycle: datetime) -> str:
    directory = NCEP_DIRECTORY_NAMES[source_id]
    return f"{NCEP_TRACKER_ROOT}/{directory}.{cycle:%Y%m%d}/{cycle:%H}/tctrack/"
