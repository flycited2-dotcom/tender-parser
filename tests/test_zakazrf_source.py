from datetime import datetime
from urllib.parse import unquote_plus

from tender_parser.sources.zakazrf import ZakazRfSource, build_search_url, parse_search_page


SAMPLE_HTML = """
<table class="reporttable">
<tr><th>ФЗ</th></tr>
<tr>
  <td>44-ФЗ</td><td><a href="/NotificationEx/id/2068231">0813500000126015052</a></td>
  <td>Идет подача заявок на участие</td><td>Аукцион</td>
  <td>Поставка и монтаж кондиционера</td><td>101 030,00</td>
  <td>РЕГИОНАЛЬНЫЙ ЦЕНТР ЗАКУПОК</td><td>ЦЕНТР ЗАНЯТОСТИ</td><td>Контакт</td>
  <td>07.08.2026</td><td>07.08.2026</td><td>18.08.2026 08:00 (+03:00)</td>
  <td>18.08.2026 08:00 (+03:00)</td><td>18.08.2026 10:00 (+03:00)</td><td>20.08.2026</td>
</tr>
</table>
"""


def test_build_search_url_uses_region_and_active_deadline() -> None:
    url = unquote_plus(
        build_search_url("кондиционер", "91", active_from=datetime(2026, 8, 8))
    )

    assert "FastFilter=кондиционер" in url
    assert "RegionRF=91" in url
    assert "SubmissionCloseDateTimeFrom=08.08.2026" in url


def test_parse_search_page_extracts_notification() -> None:
    items = parse_search_page(
        SAMPLE_HTML,
        source_url="https://webppo.zakazrf.ru/NotificationEx",
        region_hint="Республика Крым",
    )

    assert len(items) == 1
    assert items[0].source == "zakazrf"
    assert items[0].tender_number == "0813500000126015052"
    assert items[0].customer == "ЦЕНТР ЗАНЯТОСТИ"
    assert items[0].region == "Республика Крым"
    assert items[0].price == 101_030.0
    assert items[0].deadline == datetime(2026, 8, 18, 8, 0)
    assert items[0].published_at == datetime(2026, 8, 7)


def test_default_queries_start_with_broad_regional_customer_discovery() -> None:
    assert ZakazRfSource().queries[0] == ""
