from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


FilterStatus = Literal["matched", "review", "excluded"]


@dataclass(frozen=True)
class TenderRecord:
    title: str
    url: str
    source: str
    tender_number: str | None = None
    customer: str | None = None
    region: str | None = None
    price: float | None = None
    deadline: datetime | None = None
    status: str | None = None
    published_at: datetime | None = None
    discovered_at: datetime | None = None
    raw_text: str = ""
    category: str | None = None
    include_reason: str = ""
    exclude_reason: str = ""
    filter_status: FilterStatus = "excluded"
    matched_terms: list[str] = field(default_factory=list)

    @property
    def unique_key(self) -> str:
        if self.tender_number:
            return f"{self.source}:{self.tender_number}"
        return f"{self.source}:{self.url.split('#', 1)[0].rstrip('/')}"
