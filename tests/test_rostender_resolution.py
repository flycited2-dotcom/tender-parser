from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from tender_parser.models import TenderRecord
from tender_parser.rostender_resolution import (
    RostenderOfficialResolver,
    extract_official_number,
    extract_official_number_details,
    procurement_law_for_number,
    record_fingerprint,
)


NUMBER_44 = "0275100000326000101"
NUMBER_44_20 = "10277000000000000001"
NUMBER_223 = "32616290638"


def _detail_html(number: str, *, field: str = "title") -> str:
    if field == "title":
        return f"<html><head><title>Поставка оборудования — Закупка: {number}</title></head></html>"
    return (
        "<html><head>"
        f'<meta property="{field}" content="Тендер на поставку. Закупка: № {number}">'
        "</head></html>"
    )


def _rostender_record(card_id: str = "94216089", *, suffix: str = "") -> TenderRecord:
    return TenderRecord(
        title=f"Поставка оборудования{suffix}",
        url=f"https://rostender.info/region/krym-respublika/{card_id}-tender-postavka",
        source="rostender",
        tender_number=card_id,
    )


def test_extracts_only_explicit_purchase_label_from_supported_metadata() -> None:
    assert extract_official_number(_detail_html(NUMBER_44)) == NUMBER_44
    assert extract_official_number(_detail_html(NUMBER_223, field="og:title")) == NUMBER_223
    assert extract_official_number(_detail_html(NUMBER_44_20, field="og:description")) == NUMBER_44_20
    assert procurement_law_for_number(NUMBER_44) == "44-ФЗ"
    assert procurement_law_for_number(NUMBER_44_20) == "44-ФЗ"
    assert procurement_law_for_number(NUMBER_223) == "223-ФЗ"


def test_rejects_internal_id_phone_body_numbers_and_missing_colon() -> None:
    html = f"""
    <html>
      <head>
        <title>Тендер №94216089; телефон +7 978 123-45-67; Закупка № {NUMBER_44}</title>
        <meta name="description" content="Закупка: {NUMBER_223}">
      </head>
      <body>Закупка: {NUMBER_44}</body>
    </html>
    """
    assert extract_official_number(html) is None

    phone_as_purchase = """
    <meta property="og:description" content="Контакты. Закупка: 79781234567">
    """
    assert extract_official_number(phone_as_purchase) is None


def test_conflicting_supported_metadata_values_are_not_auto_resolved() -> None:
    html = f"""
    <html><head>
      <title>Закупка: {NUMBER_44}</title>
      <meta property="og:title" content="Закупка: {NUMBER_223}">
    </head></html>
    """
    extraction = extract_official_number_details(html)
    assert extraction.official_number is None
    assert extraction.conflict is True
    assert set(extraction.candidates) == {NUMBER_44, NUMBER_223}


class FakeResponse:
    def __init__(
        self,
        text: str = "",
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class QueueSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _resolver(
    tmp_path: Path,
    session: QueueSession,
    **kwargs: object,
) -> RostenderOfficialResolver:
    return RostenderOfficialResolver(
        session=session,  # type: ignore[arg-type]
        cache_path=tmp_path / "cache.json",
        delay_seconds=0,
        timeout_seconds=7,
        **kwargs,
    )


def test_exact_join_enriches_copy_and_preserves_rostender_provenance(tmp_path: Path) -> None:
    rostender = _rostender_record()
    platform = TenderRecord(
        title="Поставка оборудования",
        url="https://etp.example/procedure/777",
        source="sberbank-ast",
        tender_number=NUMBER_44,
    )
    session = QueueSession([FakeResponse(_detail_html(NUMBER_44))])
    resolver = _resolver(tmp_path, session)

    resolved = resolver.resolve_shortlist([rostender], [platform])[0]

    assert resolved.tender_number == "94216089"
    assert resolved.url == rostender.url
    assert resolved.official_number == NUMBER_44
    assert resolved.official_source == "eis-zakupki"
    assert resolved.platform_number == NUMBER_44
    assert resolved.platform_url == platform.url
    assert resolved.official_url is not None and "zakupki.gov.ru" in resolved.official_url
    assert resolved.procurement_law == "44-ФЗ"
    assert resolved.resolution_method == "rostender-meta+collected-exact"
    assert resolved.resolution_confidence == 1.0
    assert len(session.calls) == 1  # no EIS request after the collected exact join


def test_official_source_describes_official_url_not_matched_platform(tmp_path: Path) -> None:
    rostender = _rostender_record()
    eis = TenderRecord(
        title="Поставка оборудования",
        url=f"https://zakupki.gov.ru/epz/order/notice/view.html?regNumber={NUMBER_44}",
        source="eis-zakupki",
        tender_number=NUMBER_44,
    )
    platform = TenderRecord(
        title="Поставка оборудования",
        url="https://etp.example/procedure/777",
        source="sberbank-ast",
        tender_number=NUMBER_44,
    )
    session = QueueSession([FakeResponse(_detail_html(NUMBER_44))])
    resolver = _resolver(tmp_path, session)

    resolved = resolver.resolve_shortlist([rostender], [eis, platform])[0]

    assert resolved.official_url == eis.url
    assert resolved.official_source == "eis-zakupki"
    assert resolved.platform_url == platform.url
    assert resolved.platform_number == NUMBER_44


def test_exact_eis_search_result_becomes_direct_official_url(tmp_path: Path) -> None:
    eis_html = f"""
    <div class="search-registry-entry-block">
      <div class="registry-entry__header-mid__number">
        <a href="/epz/order/notice/ea20/view/common-info.html?regNumber={NUMBER_44}">№ {NUMBER_44}</a>
      </div>
      <div class="registry-entry__header-mid__title">Подача заявок</div>
      <div class="registry-entry__body-block">
        <div class="registry-entry__body-title">Объект закупки</div>
        <div class="registry-entry__body-value">Поставка оборудования</div>
      </div>
    </div>
    """
    session = QueueSession(
        [FakeResponse(_detail_html(NUMBER_44)), FakeResponse(eis_html)]
    )
    resolver = _resolver(tmp_path, session)

    resolved = resolver.resolve_shortlist([_rostender_record()])[0]

    assert len(session.calls) == 2
    assert f"searchString={NUMBER_44}" in session.calls[1][0]
    assert resolved.official_url == (
        "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html"
        f"?regNumber={NUMBER_44}"
    )
    assert resolved.official_source == "eis-zakupki"
    assert resolved.resolution_method == "rostender-meta+eis-exact"
    assert resolved.resolution_confidence == 1.0


def test_eis_failure_keeps_number_and_uses_generic_search_link(tmp_path: Path) -> None:
    session = QueueSession(
        [FakeResponse(_detail_html(NUMBER_223)), requests.Timeout("eis timeout")]
    )
    resolver = _resolver(tmp_path, session)

    resolved = resolver.resolve_shortlist([_rostender_record()])[0]

    assert resolved.official_number == NUMBER_223
    assert resolved.procurement_law == "223-ФЗ"
    assert resolved.official_url is not None
    assert "extendedsearch/results.html" in resolved.official_url
    assert f"searchString={NUMBER_223}" in resolved.official_url
    assert resolved.resolution_method == "rostender-meta+eis-search-link"
    assert resolver.last_results[0].error == "Timeout: eis timeout"


def test_cache_has_fingerprint_and_checked_at_and_avoids_repeat_fetch(tmp_path: Path) -> None:
    fixed_now = datetime(2026, 8, 15, 9, 30, tzinfo=timezone.utc)
    record = _rostender_record()
    session = QueueSession(
        [FakeResponse(_detail_html(NUMBER_44)), FakeResponse("<html></html>")]
    )
    resolver = _resolver(tmp_path, session, now=lambda: fixed_now)

    first = resolver.resolve_shortlist([record])[0]
    calls_after_first = len(session.calls)
    second = resolver.resolve_shortlist([record])[0]

    assert first.official_number == second.official_number == NUMBER_44
    assert len(session.calls) == calls_after_first
    payload = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    fingerprint = record_fingerprint(record)
    entry = payload["entries"][fingerprint]
    assert entry["fingerprint"] == fingerprint
    assert entry["checked_at"] == "2026-08-15T09:30:00+00:00"


def test_unresolved_detail_is_rechecked_after_negative_cache_ttl(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 15, 9, 30, tzinfo=timezone.utc)]
    session = QueueSession(
        [
            FakeResponse("<html><title>Тендер №94216089</title></html>"),
            FakeResponse(_detail_html(NUMBER_44)),
            FakeResponse("<html></html>"),
        ]
    )
    resolver = _resolver(tmp_path, session, now=lambda: current[0])
    record = _rostender_record()

    assert resolver.resolve_shortlist([record])[0].official_number is None
    current[0] += timedelta(hours=23)
    assert resolver.resolve_shortlist([record])[0].official_number is None
    assert len(session.calls) == 1

    current[0] += timedelta(hours=2)
    assert resolver.resolve_shortlist([record])[0].official_number == NUMBER_44
    assert len(session.calls) == 3


def test_eis_not_found_is_rechecked_after_negative_cache_ttl(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 15, 9, 30, tzinfo=timezone.utc)]
    eis_exact_html = f"""
    <div class="search-registry-entry-block">
      <div class="registry-entry__header-mid__number">
        <a href="/epz/order/notice/ea20/view/common-info.html?regNumber={NUMBER_44}">№ {NUMBER_44}</a>
      </div>
      <div class="registry-entry__body-block">
        <div class="registry-entry__body-title">Объект закупки</div>
        <div class="registry-entry__body-value">Поставка оборудования</div>
      </div>
    </div>
    """
    session = QueueSession(
        [
            FakeResponse(_detail_html(NUMBER_44)),
            FakeResponse("<html></html>"),
            FakeResponse(eis_exact_html),
        ]
    )
    resolver = _resolver(tmp_path, session, now=lambda: current[0])
    record = _rostender_record()

    first = resolver.resolve_shortlist([record])[0]
    assert first.resolution_method == "rostender-meta+eis-search-link"
    current[0] += timedelta(hours=23)
    resolver.resolve_shortlist([record])
    assert len(session.calls) == 2

    current[0] += timedelta(hours=2)
    refreshed = resolver.resolve_shortlist([record])[0]
    assert len(session.calls) == 3
    assert refreshed.resolution_method == "rostender-meta+eis-exact"
    assert f"common-info.html?regNumber={NUMBER_44}" in (refreshed.official_url or "")


def test_limit_fetches_only_first_shortlisted_records(tmp_path: Path) -> None:
    records = [_rostender_record(str(94216089 + index), suffix=str(index)) for index in range(3)]
    session = QueueSession(
        [
            FakeResponse(_detail_html(NUMBER_44)),
            FakeResponse("<html></html>"),  # first record's EIS lookup
            FakeResponse(_detail_html(NUMBER_223)),
            FakeResponse("<html></html>"),  # second record's EIS lookup
        ]
    )
    resolver = _resolver(tmp_path, session, limit=2)

    resolved = resolver.resolve_shortlist(records)

    assert resolved[0].official_number == NUMBER_44
    assert resolved[1].official_number == NUMBER_223
    assert resolved[2] is records[2]
    assert all(records[2].url not in url for url, _ in session.calls)


def test_429_obeys_retry_after_and_then_succeeds(tmp_path: Path) -> None:
    waits: list[float] = []
    session = QueueSession(
        [
            FakeResponse(status_code=429, headers={"Retry-After": "4"}),
            FakeResponse(_detail_html(NUMBER_44)),
            FakeResponse("<html></html>"),
        ]
    )
    resolver = RostenderOfficialResolver(
        session=session,  # type: ignore[arg-type]
        cache_path=tmp_path / "cache.json",
        delay_seconds=0,
        sleeper=waits.append,
    )

    resolved = resolver.resolve_shortlist([_rostender_record()])[0]

    assert resolved.official_number == NUMBER_44
    assert waits == [4.0]
    assert len(session.calls) == 3


def test_conflict_and_network_error_leave_records_unchanged(tmp_path: Path) -> None:
    conflict_html = f"""
    <title>Закупка: {NUMBER_44}</title>
    <meta property="og:description" content="Закупка: {NUMBER_223}">
    """
    first = _rostender_record()
    second = _rostender_record("94216090", suffix=" 2")
    session = QueueSession(
        [FakeResponse(conflict_html), requests.ConnectionError("offline")]
    )
    resolver = _resolver(tmp_path, session, retries=0)

    resolved = resolver.resolve_shortlist([first, second])

    assert resolved == [first, second]
    assert resolver.last_results[0].resolution_method == "rostender-meta-conflict"
    assert "conflicting official numbers" in (resolver.last_results[0].error or "")
    assert resolver.last_results[1].error == "ConnectionError: offline"


def test_non_rostender_records_are_never_fetched(tmp_path: Path) -> None:
    record = TenderRecord(title="EIS", url="https://zakupki.gov.ru/x", source="eis-zakupki")
    session = QueueSession([])
    resolver = _resolver(tmp_path, session)

    assert resolver.resolve_shortlist([record]) == [record]
    assert session.calls == []
