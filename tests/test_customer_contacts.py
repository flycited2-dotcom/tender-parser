from datetime import datetime
from pathlib import Path

import requests

from tender_parser.customer_contacts import (
    CustomerContactEnricher,
    find_eis_organization_url,
    parse_eis_contact_page,
)
from tender_parser.customers import organization_key
from tender_parser.models import TenderRecord


NOTICE_URL = (
    "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html"
    "?regNumber=0375200002226000191"
)
ORG_URL = (
    "https://zakupki.gov.ru/epz/organization/view/info.html"
    "?organizationCode=03752000022"
)

NOTICE_HTML = f"""
<html><body>
  <a href="/epz/organization/view/info.html?organizationCode=03752000022">
    ГБУЗ РК "Больница"
  </a>
  <section class="blockInfo__section section">
    <span class="section__title">Адрес электронной почты</span>
    <span class="section__info">notice@example.ru</span>
  </section>
  <section class="blockInfo__section section">
    <span class="section__title">Номер контактного телефона</span>
    <span class="section__info">8-3652-000000</span>
  </section>
</body></html>
"""

ORG_HTML = """
<html><body>
  <div class="col-md-auto">
    <div class="registry-entry__body-title">ИНН</div>
    <div class="registry-entry__body-value">9102063951</div>
  </div>
  <div class="col-md-auto">
    <div class="registry-entry__body-title">Место нахождения</div>
    <div class="registry-entry__body-value">Республика Крым, г. Симферополь</div>
  </div>
  <section class="blockInfo__section section">
    <span class="section__title">Телефон</span>
    <span class="section__info">+7 (3652) 545434</span>
  </section>
  <section class="blockInfo__section section">
    <span class="section__title">Почтовый адрес</span>
    <span class="section__info">295000, г. Симферополь</span>
  </section>
  <section class="blockInfo__section section">
    <span class="section__title">Контактный адрес электронной почты</span>
    <span class="section__info">official@example.ru</span>
  </section>
  <section class="blockInfo__section section">
    <span class="section__title">Адрес организации в сети Интернет</span>
    <span class="section__info">https://hospital.example.ru</span>
  </section>
  <section class="blockInfo__section section">
    <span class="section__title">Контактное лицо</span>
    <span class="section__info">Иванова И. И.</span>
  </section>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class ErrorResponse(FakeResponse):
    def raise_for_status(self) -> None:
        raise requests.HTTPError("404")


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, timeout: int) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(ORG_HTML if "organization/view" in url else NOTICE_HTML)


class FallbackSession(FakeSession):
    def get(self, url: str, timeout: int) -> FakeResponse:
        self.calls.append(url)
        if "/notice/zk20/" in url:
            return ErrorResponse("")
        if "/extendedsearch/results" in url:
            return FakeResponse(
                '<a href="/epz/order/notice/ea20/view/common-info.html?regNumber='
                '0375200002226000191">№ 0375200002226000191</a>'
            )
        return FakeResponse(ORG_HTML if "organization/view" in url else NOTICE_HTML)


def tender() -> TenderRecord:
    return TenderRecord(
        title="Поставка мебели",
        url=NOTICE_URL,
        source="eis-zakupki",
        tender_number="0375200002226000191",
        customer='ГБУЗ РК "Больница"',
        region="Республика Крым",
        discovered_at=datetime(2026, 8, 19),
    )


def test_parse_eis_organization_contact_fields() -> None:
    contact = parse_eis_contact_page(ORG_HTML, ORG_URL)

    assert contact.inn == "9102063951"
    assert contact.legal_address == "Республика Крым, г. Симферополь"
    assert contact.postal_address == "295000, г. Симферополь"
    assert contact.email == "official@example.ru"
    assert contact.phone == "+7 (3652) 545434"
    assert contact.contact_person == "Иванова И. И."
    assert contact.website == "https://hospital.example.ru"


def test_parse_223_notice_inn_from_public_organization_link() -> None:
    html = """
    <a href="/epz/organization/view223/info.html?inn=9001025903&amp;kpp=900101001">
      Заказчик
    </a>
    """

    contact = parse_eis_contact_page(html, NOTICE_URL)

    assert contact.inn == "9001025903"


def test_find_eis_organization_url_from_notice() -> None:
    assert find_eis_organization_url(NOTICE_HTML, NOTICE_URL, 'ГБУЗ РК "Больница"') == ORG_URL


def test_enricher_uses_official_page_cache_and_preserves_manual_values(tmp_path: Path) -> None:
    item = tender()
    key = organization_key(item.customer or "")
    row: list[object] = [key, item.customer or "", "", "Республика Крым", "", "", "", "manual@example.ru", "", "", "", "", item.url, "01.01.2026", "Нужно проверить", ""]
    session = FakeSession()
    cache_path = tmp_path / "customer_contacts.json"
    enricher = CustomerContactEnricher(
        cache_path,
        session=session,  # type: ignore[arg-type]
        max_fetches=5,
        min_interval_seconds=0,
        now=lambda: datetime(2026, 8, 19, 12, 0),
    )

    rows, report = enricher.enrich([row], [item])

    assert report.fetched == 1
    assert report.enriched == 1
    assert session.calls == [NOTICE_URL, ORG_URL]
    assert rows[0][4] == "9102063951"
    assert rows[0][7] == "manual@example.ru"
    assert rows[0][8] == "+7 (3652) 545434"
    assert rows[0][9] == "Иванова И. И."
    assert rows[0][11] == ORG_URL
    assert rows[0][13] == "01.01.2026"
    assert cache_path.is_file()

    second_session = FakeSession()
    cached_enricher = CustomerContactEnricher(
        cache_path,
        session=second_session,  # type: ignore[arg-type]
        max_fetches=5,
        min_interval_seconds=0,
        now=lambda: datetime(2026, 8, 20, 12, 0),
    )
    blank_row: list[object] = [key, item.customer or "", "", "Республика Крым", "", "", "", "", "", "", "", "", item.url, "", "Нужно проверить", ""]
    cached_rows, cached_report = cached_enricher.enrich([blank_row], [item])

    assert second_session.calls == []
    assert cached_report.cached == 1
    assert cached_rows[0][7] == "official@example.ru"


def test_enricher_recovers_obsolete_eis_card_subtype_through_official_search(tmp_path: Path) -> None:
    item = TenderRecord(
        title="Поставка мебели",
        url=(
            "https://zakupki.gov.ru/epz/order/notice/zk20/view/common-info.html"
            "?regNumber=0375200002226000191"
        ),
        source="eis-zakupki",
        tender_number="0375200002226000191",
        customer='ГБУЗ РК "Больница"',
        region="Республика Крым",
    )
    key = organization_key(item.customer or "")
    row: list[object] = [key, item.customer or "", "", "Республика Крым", "", "", "", "", "", "", "", "", item.url, "", "Нужно проверить", ""]
    session = FallbackSession()

    rows, report = CustomerContactEnricher(
        tmp_path / "cache.json",
        session=session,  # type: ignore[arg-type]
        max_fetches=1,
        min_interval_seconds=0,
    ).enrich([row], [item])

    assert report.errors == 0
    assert rows[0][4] == "9102063951"
    assert any("/extendedsearch/results" in url for url in session.calls)
    assert any("/notice/ea20/" in url for url in session.calls)
