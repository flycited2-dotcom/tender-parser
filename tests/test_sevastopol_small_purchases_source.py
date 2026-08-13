from __future__ import annotations

import requests

from tender_parser.sources.sevastopol_small_purchases import (
    SevastopolSmallPurchasesAdapter,
)


class NoCallSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(self, url: str, *, timeout: int):
        raise AssertionError("live showcase must not be called in the safe default mode")


class TimeoutSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(self, url: str, *, timeout: int):
        raise requests.Timeout("showcase did not answer")


def test_adapter_default_is_explicitly_skipped_with_import_fallback() -> None:
    source = SevastopolSmallPurchasesAdapter(session=NoCallSession())

    result = source.fetch_with_report(["принтер"])

    assert result.tenders == []
    assert result.errors == []
    assert result.health[0].source == "sevastopol-small-purchases"
    assert result.health[0].status == "skipped"
    assert "не подтвержден" in result.health[0].detail
    assert "imports/" in result.health[0].detail


def test_diagnostic_probe_reports_timeout_without_false_success() -> None:
    source = SevastopolSmallPurchasesAdapter(
        session=TimeoutSession(),
        probe_live=True,
        timeout_seconds=3,
    )

    result = source.fetch_with_report([])

    assert result.tenders == []
    assert result.health[0].status == "timeout"
    assert result.health[0].found == 0
    assert "imports/" in result.health[0].detail
    assert result.errors
