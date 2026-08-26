from datetime import datetime

import pytest

from tender_parser.sources.tektorg import (
    TektorgSource,
    build_soap_request,
    parse_soap_response,
)


SOAP_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
  <SOAP-ENV:Body>
    <SOAP-ENV:proceduresResponse>
      <totalProcedures>2</totalProcedures>
      <currentPage>1</currentPage>
      <totalPage>2</totalPage>
      <sectionCode>44fz</sectionCode>
      <procedures>
        <procedure id="101">
          <remoteId>101</remoteId>
          <url_to_showcase>https://www.tektorg.ru/44-fz/procedures/101</url_to_showcase>
          <registryNumber>TEK-101</registryNumber>
          <eisRegistryNumber>0174100000626000005</eisRegistryNumber>
          <title>Поставка кондиционеров</title>
          <datePublished>2026-08-26T09:15:00+03:00</datePublished>
          <dateUpdated>2026-08-26T10:00:00+03:00</dateUpdated>
          <dateEndRegistration>2026-09-02T10:00:00+03:00</dateEndRegistration>
          <procedureType><id>1</id><title>Электронный аукцион</title></procedureType>
          <contactEmail>contact@example.test</contactEmail>
          <contactPhone>+7 978 000-00-00</contactPhone>
          <contactPerson>Иванов Иван Иванович</contactPerson>
          <currency>RUB</currency>
          <organizer>
            <id>11</id><fullName>ГБУ РК «Климат»</fullName><inn>9100000001</inn>
            <legal><index>295000</index><region>Республика Крым</region><city>Симферополь</city><street>ул. Ленина</street><house>1</house><countryIsoNr>643</countryIsoNr></legal>
            <postal><index>295000</index><region>Республика Крым</region><city>Симферополь</city><street>ул. Ленина</street><house>1</house><countryIsoNr>643</countryIsoNr></postal>
          </organizer>
          <documents><document><id>1</id><filename>Техническое задание.pdf</filename><file>https://files.test/tz.pdf</file></document></documents>
          <lots>
            <lot><remoteId>201</remoteId><number>1</number><subject>Сплит-системы</subject><startPrice>100000</startPrice><status>Приём заявок</status>
              <deliveryPlaces><deliveryPlace><address>Республика Крым, г. Симферополь</address></deliveryPlace></deliveryPlaces>
              <nomenclature2><item><code>28.25.12</code><name>Кондиционеры</name></item></nomenclature2>
            </lot>
            <lot><remoteId>202</remoteId><number>2</number><subject>Монтаж</subject><startPrice>50000</startPrice><status>Приём заявок</status></lot>
          </lots>
        </procedure>
      </procedures>
    </SOAP-ENV:proceduresResponse>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
"""


def test_parse_public_soap_keeps_direct_identifiers_contacts_and_documents() -> None:
    records, total_pages = parse_soap_response(SOAP_RESPONSE, section_code="44fz")

    assert total_pages == 2
    assert len(records) == 1
    item = records[0]
    assert item.source == "tektorg"
    assert item.tender_number == "TEK-101"
    assert item.official_number == "0174100000626000005"
    assert "searchString=0174100000626000005" in (item.official_url or "")
    assert item.platform_url == "https://www.tektorg.ru/44-fz/procedures/101"
    assert item.procurement_law == "44-ФЗ"
    assert item.customer == "ГБУ РК «Климат»"
    assert item.region == "Симферополь"
    assert item.price == 150_000
    assert item.deadline == datetime(2026, 9, 2, 10, 0)
    assert "Техническое задание.pdf" in item.raw_text
    assert "TEKTORG_INN=9100000001" in item.raw_text
    assert "TEKTORG_EMAIL=contact@example.test" in item.raw_text
    assert "Республика Крым" in item.delivery_region_evidence


def test_build_request_contains_no_credentials_and_has_update_window() -> None:
    body = build_soap_request(
        section_code="market",
        page=2,
        page_size=100,
        start_update_at=datetime(2026, 8, 25, 8, 0),
        end_update_at=datetime(2026, 8, 26, 8, 0),
    ).decode("utf-8")

    assert "market" in body
    assert "2026-08-25T08:00:00" in body
    assert ">100<" in body
    assert ">2<" in body
    assert "token" not in body.casefold()
    assert "password" not in body.casefold()


def test_parser_rejects_dtd_and_entities() -> None:
    with pytest.raises(ValueError, match="DTD/ENTITY"):
        parse_soap_response('<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><x>&y;</x>')


class FakeResponse:
    def __init__(self, body: str) -> None:
        self.content = body.encode("utf-8")
        self.url = "https://api.tektorg.ru/procedures/soap"

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requests: list[bytes] = []

    def post(self, url: str, *, data: bytes, timeout: int) -> FakeResponse:
        assert url.startswith("https://api.tektorg.ru/")
        assert timeout == 5
        self.requests.append(data)
        return FakeResponse(SOAP_RESPONSE.replace("<totalPage>2</totalPage>", "<totalPage>1</totalPage>"))


def test_source_keeps_only_target_regions_by_default() -> None:
    session = FakeSession()
    source = TektorgSource(
        session=session,  # type: ignore[arg-type]
        section_codes=("44fz",),
        lookback_hours=24,
        page_size=100,
        max_pages_per_section=1,
        timeout_seconds=5,
        now=lambda: datetime(2026, 8, 26, 8, 0),
    )

    records = source.fetch_keywords(["кондиционер"])

    assert len(records) == 1
    assert len(session.requests) == 1
    assert b"44fz" in session.requests[0]
