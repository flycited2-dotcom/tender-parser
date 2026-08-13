from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, runtime_checkable

from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.rts import SourceBlockedError, SourceFetchError


class TenderSource(Protocol):
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        ...


@runtime_checkable
class ReportAwareTenderSource(TenderSource, Protocol):
    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        ...


@dataclass(frozen=True)
class _SourceResult:
    tenders: list[TenderRecord]
    health: list[SourceHealth]
    errors: list[str]


class CompositeSource:
    def __init__(
        self,
        sources: list[TenderSource],
        stop_after_first_success: bool = False,
        *,
        parallel: bool = False,
        max_workers: int = 4,
    ) -> None:
        self.sources = sources
        self.stop_after_first_success = stop_after_first_success
        self.parallel = parallel
        self.max_workers = max(1, max_workers)

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
        source_results = (
            (self._fetch_source(source, keywords) for source in self.sources)
            if self.stop_after_first_success
            else self._run_sources(keywords)
        )
        for source_result in source_results:
            health.extend(source_result.health)
            errors.extend(source_result.errors)
            for tender in source_result.tenders:
                dedupe_key = tender.unique_key
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                collected.append(tender)
            if source_result.tenders and self.stop_after_first_success:
                break

        return SourceFetchResult(tenders=collected, health=health, errors=errors)

    def _run_sources(self, keywords: list[str]) -> list[_SourceResult]:
        # A fallback chain must remain lazy: starting every fallback at once
        # would defeat stop_after_first_success. Parallel mode is therefore for
        # independent sources only.
        if not self.parallel or len(self.sources) < 2:
            return [self._fetch_source(source, keywords) for source in self.sources]

        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(self.sources)),
            thread_name_prefix="tender-source",
        ) as executor:
            futures = [
                executor.submit(self._fetch_source, source, keywords)
                for source in self.sources
            ]
            # Reading futures in source order keeps reports and deterministic
            # deduplication identical to the former sequential implementation.
            return [future.result() for future in futures]

    @staticmethod
    def _fetch_source(source: TenderSource, keywords: list[str]) -> _SourceResult:
        started_at = monotonic()
        try:
            if isinstance(source, CompositeSource):
                nested_result = source.fetch_with_report(keywords)
                return _SourceResult(
                    nested_result.tenders,
                    nested_result.health,
                    nested_result.errors,
                )
            if isinstance(source, ReportAwareTenderSource):
                source_result = source.fetch_with_report(keywords)
                return _SourceResult(
                    source_result.tenders,
                    source_result.health,
                    source_result.errors,
                )

            tenders = source.fetch_keywords(keywords)
            source_name = str(
                getattr(source, "source_name", source.__class__.__name__)
            )
            return _SourceResult(
                tenders,
                [
                    SourceHealth(
                        source=source_name,
                        status="ok" if tenders else "empty",
                        found=len(tenders),
                        elapsed_seconds=round(monotonic() - started_at, 3),
                    )
                ],
                [],
            )
        except SourceFetchError as exc:
            detail = str(exc)
            source_name = str(
                getattr(source, "source_name", source.__class__.__name__)
            )
            return _SourceResult(
                [],
                [
                    SourceHealth(
                        source=source_name,
                        status=(
                            "skipped"
                            if "не настроен" in detail.lower()
                            else "blocked"
                            if isinstance(exc, SourceBlockedError)
                            else "error"
                        ),
                        found=0,
                        elapsed_seconds=round(monotonic() - started_at, 3),
                        detail=detail,
                    )
                ],
                [detail],
            )
