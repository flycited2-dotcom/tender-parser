from tender_parser.sources.eat_browser import parse_eat_listing_page


EAT_URL = "https://agregatoreat.ru/lk/supplier/eat/purchases/active/all"


def test_parse_eat_authenticated_listing_card() -> None:
    html = """
    <app-purchase-card>
      <h3 id="tradeNumber"><a href="/lk/supplier/eat/announcement/card-1">100082982126100019</a></h3>
      <div id="purchaseStateDescription">Подача предложений</div>
      <p id="subject">Поставка кондиционеров</p>
      <span id="organizerInfoNameLink">ГБУ Республики Крым</span>
      <p id="deliveryAddress">Республика Крым, г. Симферополь</p>
      <h1 id="contractPrice">270 000,00 ₽</h1>
    </app-purchase-card>
    """

    tenders = parse_eat_listing_page(html, EAT_URL)

    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.source == "eat-berezka"
    assert tender.tender_number == "100082982126100019"
    assert tender.title == "Поставка кондиционеров"
    assert tender.customer == "ГБУ Республики Крым"
    assert tender.region == "Симферополь"
    assert tender.price == 270_000.0
    assert tender.url == "https://agregatoreat.ru/lk/supplier/eat/announcement/card-1"
    assert tender.status == "Подача предложений"
