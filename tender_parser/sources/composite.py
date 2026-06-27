from __future__ import annotations

from time import monotonic
from typing import Protocol, runtime_checkable

from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.rts import SourceFetchError


class TenderSource(Protocol):
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        ...


@runtime_checkable
class ReportAwareTenderSource(TenderSource, Protocol):
    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        ...


class CompositeSource:
    def __init__(self, sources: list[TenderSource], stop_after_first_success: bool = False) -> None:
        self.sources = sources
        self.stop_after_first_success = stop_after_first_success

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        result = self.fetch_with_report(keywords)
        if not result.tenders and result.errors:
            raise SourceFetchError(f"все источники недоступны: {'; '.join(result.errors)}")
        return result.tenders

    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        collected: list[TenderRecord] = []
        errors: list[str] = []
        health: list[SourceHealth] = []
        seen: set[str] = set()
        for source in self.sources:
            started_at = monotonic()
            try:
                if isinstance(source, CompositeSource):
                    nested_result = source.fetch_with_report(keywords)
                    tenders = nested_result.tenders
                    health.extend(nested_result.health)
                    errors.extend(nested_result.errors)
                elif isinstance(source, ReportAwareTenderSource):
                    source_result = source.fetch_with_report(keywords)
                    tenders = source_result.tenders
                    health.extend(source_result.health)
                    errors.extend(source_result.errors)
                else:
                    tenders = source.fetch_keywords(keywords)
                    health.append(
                        SourceHealth(
                            source=source.__class__.__name__,
                            status="ok" if tenders else "empty",
                            found=len(tenders),
                            elapsed_seconds=round(monotonic() - started_at, 3),
                        )
                    )
            except SourceFetchError as exc:
                detail = str(exc)
                errors.append(detail)
                health.append(
                    SourceHealth(
                        source=source.__class__.__name__,
                        status="skipped" if "не настроен" in detail.lower() else "error",
                        found=0,
                        elapsed_seconds=round(monotonic() - started_at, 3),
                        detail=detail,
                    )
                )
                continue

            for tender in tenders:
                dedupe_key = tender.tender_number or tender.unique_key
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                collected.append(tender)
            if tenders and self.stop_after_first_success:
                break

        return SourceFetchResult(tenders=collected, health=health, errors=errors)
