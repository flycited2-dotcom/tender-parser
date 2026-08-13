from dataclasses import replace
from datetime import datetime
from threading import Barrier

import pytest

from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.composite import CompositeSource
from tender_parser.sources.rts import SourceBlockedError, SourceFetchError


class FailingSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        raise SourceFetchError("blocked")


class CaptchaBlockedSource:
    source_name = "captcha-source"

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        raise SourceBlockedError("captcha detected")


class GoodSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return [
            TenderRecord(
                title="Поставка МФУ в Республику Крым",
                url="https://example.test/tender-1/",
                source="good",
                tender_number="1",
                region="Республика Крым",
                price=45_000.0,
                deadline=datetime(2026, 6, 4, 10, 0),
                raw_text="Поставка МФУ в Республику Крым",
            )
        ]


class CountingSource(GoodSource):
    def __init__(self) -> None:
        self.calls = 0

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        self.calls += 1
        return super().fetch_keywords(keywords)


class ReportAwareSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return []

    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        return SourceFetchResult(
            tenders=[
                TenderRecord(
                    title="РџРѕСЃС‚Р°РІРєР° РїСЂРёРЅС‚РµСЂР° РІ РЎРµРІР°СЃС‚РѕРїРѕР»СЊ",
                    url="https://example.test/tender-2/",
                    source="report-aware",
                    tender_number="2",
                    region="РЎРµРІР°СЃС‚РѕРїРѕР»СЊ",
                    price=55_000.0,
                    deadline=datetime(2026, 6, 5, 10, 0),
                    raw_text="РџРѕСЃС‚Р°РІРєР° РїСЂРёРЅС‚РµСЂР° РІ РЎРµРІР°СЃС‚РѕРїРѕР»СЊ",
                )
            ],
            health=[
                SourceHealth(
                    source="custom-endpoint",
                    status="ok",
                    found=1,
                    elapsed_seconds=0.01,
                )
            ],
        )


def test_composite_source_returns_results_from_working_source() -> None:
    source = CompositeSource([FailingSource(), GoodSource()])

    tenders = source.fetch_keywords(["мфу"])

    assert len(tenders) == 1
    assert tenders[0].source == "good"


def test_composite_source_reports_success_and_failure_health() -> None:
    source = CompositeSource([FailingSource(), GoodSource()])

    result = source.fetch_with_report(["мфу"])

    assert len(result.tenders) == 1
    assert [(item.source, item.status, item.found) for item in result.health] == [
        ("FailingSource", "error", 0),
        ("GoodSource", "ok", 1),
    ]
    assert "blocked" in result.health[0].detail


def test_composite_source_reports_explicit_blocked_status_and_source_name() -> None:
    result = CompositeSource([CaptchaBlockedSource(), GoodSource()]).fetch_with_report(["мфу"])

    assert (result.health[0].source, result.health[0].status) == (
        "captcha-source",
        "blocked",
    )


def test_composite_source_can_stop_after_first_success() -> None:
    first = CountingSource()
    second = CountingSource()
    source = CompositeSource([first, second], stop_after_first_success=True)

    tenders = source.fetch_keywords(["мфу"])

    assert len(tenders) == 1
    assert first.calls == 1
    assert second.calls == 0


def test_composite_source_can_run_independent_sources_in_parallel() -> None:
    rendezvous = Barrier(2, timeout=2)

    class ParallelSource(GoodSource):
        def __init__(self, number: str) -> None:
            self.number = number

        def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
            rendezvous.wait()
            item = super().fetch_keywords(keywords)[0]
            return [
                replace(
                    item,
                    tender_number=self.number,
                    source=f"parallel-{self.number}",
                )
            ]

    source = CompositeSource(
        [ParallelSource("1"), ParallelSource("2")],
        parallel=True,
        max_workers=2,
    )

    result = source.fetch_with_report(["мфу"])

    assert [item.source for item in result.tenders] == ["parallel-1", "parallel-2"]
    assert [item.status for item in result.health] == ["ok", "ok"]


def test_parallel_mode_keeps_fallback_chain_lazy() -> None:
    first = CountingSource()
    second = CountingSource()
    source = CompositeSource(
        [first, second],
        stop_after_first_success=True,
        parallel=True,
        max_workers=2,
    )

    source.fetch_keywords(["мфу"])

    assert first.calls == 1
    assert second.calls == 0


def test_composite_source_uses_source_level_health_report() -> None:
    source = CompositeSource([ReportAwareSource()])

    result = source.fetch_with_report(["РїСЂРёРЅС‚РµСЂ"])

    assert len(result.tenders) == 1
    assert [(item.source, item.status, item.found) for item in result.health] == [
        ("custom-endpoint", "ok", 1)
    ]


def test_composite_source_keeps_same_number_from_different_sources() -> None:
    class OtherSource:
        def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
            return [
                TenderRecord(
                    title="Другой тендер с тем же номером",
                    url="https://other.test/tender-1/",
                    source="other",
                    tender_number="1",
                )
            ]

    source = CompositeSource([GoodSource(), OtherSource()])

    tenders = source.fetch_keywords(["мфу"])

    assert len(tenders) == 2
    assert {tender.source for tender in tenders} == {"good", "other"}


def test_composite_source_raises_when_every_source_fails() -> None:
    source = CompositeSource([FailingSource(), FailingSource()])

    with pytest.raises(SourceFetchError, match="все источники недоступны"):
        source.fetch_keywords(["мфу"])
