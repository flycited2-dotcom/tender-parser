from datetime import datetime
from urllib.parse import unquote_plus

from tender_parser.sources.roseltorg import build_search_url, parse_search_page, target_regions


SAMPLE_HTML = """
<div class="search-results__item" data-feature-favorite-lots-procedure-number="0875300029426000218">
  <div class="search-results__subject"><a class="search-results__link--description" href="/procedure/0875300029426000218/1">Поставка МФУ</a></div>
  <div class="search-results__section">Государственные закупки (44-ФЗ)</div>
  <div class="search-results__region">91. Республика Крым</div>
  <div class="search-results__customer"><p><a>АДМИНИСТРАЦИЯ СИМФЕРОПОЛЯ</a></p></div>
  <div class="search-results__status">Прием заявок 5 дн.</div>
  <div class="search-results__sum"><p class="desktop">780 760,00 ₽</p></div>
  <p class="search-results__type">Электронный аукцион</p>
  <time class="search-results__time">14.08.2026 в 09:00</time>
</div>
"""


def test_target_regions_dedupes_crimea_aliases() -> None:
    assert target_regions(["Симферополь", "Крым", "Севастополь"]) == [
        ("91", "Республика Крым"),
        ("92", "Севастополь"),
    ]


def test_build_search_url_uses_active_statuses_and_regions() -> None:
    url = unquote_plus(build_search_url("мфу", ["91", "92"]))

    assert "query_field=мфу" in url
    assert "status[]=5" in url
    assert "status[]=0" in url
    assert "region[]=91" in url
    assert "region[]=92" in url


def test_parse_search_page_extracts_card() -> None:
    items = parse_search_page(SAMPLE_HTML, "https://www.roseltorg.ru/procedures/search")

    assert len(items) == 1
    assert items[0].source == "roseltorg"
    assert items[0].tender_number == "0875300029426000218"
    assert items[0].region == "91. Республика Крым"
    assert items[0].price == 780_760.0
    assert items[0].deadline == datetime(2026, 8, 14, 9, 0)
    assert items[0].url == "https://www.roseltorg.ru/procedure/0875300029426000218/1"
