from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel

from cyclone_tracker.models import CycleData


class AdapterOutcome(BaseModel):
    source_id: str
    cycle_id: str | None
    status: Literal["ok", "empty", "unavailable", "error"]
    cycle: CycleData | None = None
    error_kind: str | None = None


class SourceAdapter(Protocol):
    source_id: str

    def fetch_latest(self, now: datetime) -> AdapterOutcome: ...
