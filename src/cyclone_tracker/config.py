from datetime import datetime

SOURCE_NAMES_ZH_TW = {
    "gefs": "NCEP GEFS",
    "aigefs": "NCEP AIGEFS",
    "aigfs": "NCEP AIGFS",
    "ifs-ens": "ECMWF IFS ENS",
    "aifs-ens": "ECMWF AIFS ENS",
}

NCEP_TRACKER_ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/ens_tracker/prod"
NCEP_DIRECTORY_NAMES = {"gefs": "gefs", "aigefs": "aigefs", "aigfs": "aigfs"}


def ncep_cycle_url(source_id: str, cycle: datetime) -> str:
    directory = NCEP_DIRECTORY_NAMES[source_id]
    return f"{NCEP_TRACKER_ROOT}/{directory}.{cycle:%Y%m%d}/{cycle:%H}/tctrack/"
