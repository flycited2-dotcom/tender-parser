from __future__ import annotations

from tender_parser.direct_links import (
    EisCardLinkEnricher,
    build_platform_destination,
    documents_destination,
    normalize_direct_links,
    parse_eis_card_links,
    platform_display_name,
)
from tender_parser.models import TenderRecord


NUMBER = "0175100007026000060"
EIS_URL = (
    "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html"
    f"?regNumber={NUMBER}"
)


class Response:
    def __init__(self, text: str, url: str = EIS_URL) -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


class Session:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, timeout: int) -> Response:
        self.calls.append(url)
        return self.response


def eis_html() -> str:
    return f"""
    <html><body>
      <div>№ {NUMBER}</div>
      <div>Наименование электронной площадки в информационно-телекоммуникационной
      сети «Интернет» РТС-тендер Адрес электронной площадки в
      информационно-телекоммуникационной сети «Интернет» http://www.rts-tender.ru</div>
      <a href="/epz/order/notice/ea20/view/documents.html?regNumber={NUMBER}">ДОКУМЕНТЫ</a>
    </body></html>
    """


def test_native_sources_expose_direct_eis_and_platform_destinations() -> None:
    eis = TenderRecord(title="Мебель", url=EIS_URL, source="eis-zakupki", tender_number=NUMBER)
    gpb = TenderRecord(
        title="Лампа",
        url="https://gos.etpgpb.ru/front/procedure/view/id",
        source="etp-gpb",
        tender_number="0375200001526000132",
    )

    normalized_eis, normalized_gpb = normalize_direct_links([eis, gpb])

    assert normalized_eis.official_number == NUMBER
    assert normalized_eis.official_url == EIS_URL
    assert normalized_eis.procurement_law == "44-ФЗ"
    assert normalized_gpb.platform_number == "0375200001526000132"
    assert normalized_gpb.platform_url == gpb.url


def test_platform_display_name_prefers_direct_platform_url_and_knows_source_classes() -> None:
    resolved = TenderRecord(
        title="МФУ",
        url=EIS_URL,
        source="EisZakupkiSource",
        platform_url="https://utp.sberbank-ast.ru/Trade/NBT/PurchaseView/42/0/0/0",
    )
    tektorg = TenderRecord(
        title="Кабель",
        url="https://unknown.example.test/card/1",
        source="TektorgSource",
    )

    assert platform_display_name(resolved) == "Сбербанк-АСТ"
    assert platform_display_name(tektorg) == "ТЭК-Торг"


def test_rts_poisk_promotes_exact_eis_destination() -> None:
    record = TenderRecord(
        title="Бумага",
        url=EIS_URL,
        source="rts-poisk",
        tender_number=NUMBER,
    )

    normalized = normalize_direct_links([record])[0]

    assert normalized.official_number == NUMBER
    assert normalized.official_url == EIS_URL
    assert normalized.official_source == "eis-zakupki"
    assert normalized.platform_number is None
    assert normalized.resolution_method == "rts-poisk-direct-eis"


def test_rts_poisk_keeps_non_eis_as_platform_destination() -> None:
    platform_url = "https://agregatoreat.ru/purchases/announcement/example/info"
    record = TenderRecord(
        title="Монтаж",
        url=platform_url,
        source="rts-poisk",
        tender_number="200909853126100144",
    )

    normalized = normalize_direct_links([record])[0]

    assert normalized.official_number is None
    assert normalized.platform_number == "200909853126100144"
    assert normalized.platform_url == platform_url
    assert normalized.resolution_method == "rts-poisk-direct-platform"


def test_eis_card_discovers_documents_and_exact_rts_44_destination() -> None:
    parsed = parse_eis_card_links(eis_html(), EIS_URL, NUMBER)

    assert parsed.confirmed is True
    assert parsed.documents_url is not None and "documents.html" in parsed.documents_url
    assert parsed.platform_url == "http://www.rts-tender.ru"
    destination = build_platform_destination(
        parsed.platform_name, parsed.platform_url, NUMBER, procurement_law="44-ФЗ"
    )
    assert destination.endswith(f"/number/{NUMBER}/etpName/fks")


def test_live_enrichment_confirms_eis_and_adds_rts_platform() -> None:
    record = TenderRecord(
        title="Мебель",
        url=EIS_URL,
        source="eis-zakupki",
        tender_number=NUMBER,
        review_priority="hot",
    )
    session = Session(Response(eis_html()))

    enriched = EisCardLinkEnricher(session=session).enrich([record])[0]

    assert enriched.official_number == NUMBER
    assert enriched.platform_number == NUMBER
    assert enriched.platform_url is not None and "/auctionsearch/" in enriched.platform_url
    assert EisCardLinkEnricher  # keep the public class import covered
    assert documents_destination(enriched) == (
        EIS_URL.replace("common-info.html", "documents.html"),
        "Открыть документы",
    )


def test_unconfirmed_aggregator_number_is_not_shown_as_official() -> None:
    record = TenderRecord(
        title="Бумага",
        url="https://rostender.info/tender/94211789",
        source="rostender",
        tender_number="94211789",
        review_priority="hot",
        official_number=NUMBER,
        official_url=EIS_URL,
        official_source="eis-zakupki",
        procurement_law="44-ФЗ",
        resolution_method="rostender-meta+eis-exact",
        resolution_confidence=1.0,
    )
    session = Session(Response("<html><body>Запрашиваемая страница не существует</body></html>"))
    enricher = EisCardLinkEnricher(session=session)

    enriched = enricher.enrich([record])[0]

    assert enriched.official_number is None
    assert enriched.official_url is None
    assert enriched.platform_number == NUMBER
    assert enriched.platform_url is None
    assert enriched.resolution_method == "rostender-meta-unverified"
    assert enricher.last_report.invalidated == 1
