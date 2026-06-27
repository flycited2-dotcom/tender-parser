from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from tender_parser.models import TenderRecord


SourceStatus = Literal["ok", "empty", "skipped", "partial", "blocked", "timeout", "ssl_error", "error"]


@dataclass(frozen=True)
class SourceHealth:
    source: str
    status: SourceStatus
    found: int
    elapsed_seconds: float
    detail: str = ""


@dataclass
class SourceFetchResult:
    tenders: list[TenderRecord] = field(default_factory=list)
    health: list[SourceHealth] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
