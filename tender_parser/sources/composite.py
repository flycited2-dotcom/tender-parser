from __future__ import annotations

from typing import Protocol

from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError


class TenderSource(Protocol):
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        ...


class CompositeSource:
    def __init__(self, sources: list[TenderSource], stop_after_first_success: bool = False) -> None:
        self.sources = sources
        self.stop_after_first_success = stop_after_first_success

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        errors: list[str] = []
        seen: set[str] = set()
        for source in self.sources:
            try:
                tenders = source.fetch_keywords(keywords)
            except SourceFetchError as exc:
                errors.append(str(exc))
                continue

            for tender in tenders:
                dedupe_key = tender.tender_number or tender.unique_key
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                collected.append(tender)
            if tenders and self.stop_after_first_success:
                break

        if not collected and errors:
            raise SourceFetchError(f"все источники недоступны: {'; '.join(errors)}")
        return collected
