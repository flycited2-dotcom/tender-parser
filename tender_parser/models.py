from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


FilterStatus = Literal["matched", "review", "excluded"]
MatchConfidence = Literal["точное", "вероятное", "ручная проверка"]
ReviewPriority = Literal["hot", "review", "wide", "excluded"]
DetailStatus = Literal["not_checked", "imported", "documents_checked", "enriched"]


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
    match_confidence: MatchConfidence | None = None
    review_priority: ReviewPriority | None = None
    matched_terms: list[str] = field(default_factory=list)
    detail_status: DetailStatus = "not_checked"
    document_matches: list[str] = field(default_factory=list)
    delivery_region_evidence: str = ""
    source_confidence: float = 0.0

    @property
    def unique_key(self) -> str:
        if self.tender_number:
            return f"{self.source}:{self.tender_number}"
        return f"{self.source}:{self.url.split('#', 1)[0].rstrip('/')}"
