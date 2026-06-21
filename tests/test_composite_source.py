from datetime import datetime

import pytest

from tender_parser.models import TenderRecord
from tender_parser.sources.composite import CompositeSource
from tender_parser.sources.rts import SourceFetchError


class FailingSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        raise SourceFetchError("blocked")


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


def test_composite_source_can_stop_after_first_success() -> None:
    first = CountingSource()
    second = CountingSource()
    source = CompositeSource([first, second], stop_after_first_success=True)

    tenders = source.fetch_keywords(["мфу"])

    assert len(tenders) == 1
    assert first.calls == 1
    assert second.calls == 0


def test_composite_source_raises_when_every_source_fails() -> None:
    source = CompositeSource([FailingSource(), FailingSource()])

    with pytest.raises(SourceFetchError, match="все источники недоступны"):
        source.fetch_keywords(["мфу"])
