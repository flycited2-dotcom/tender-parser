from datetime import datetime

from tender_parser.sources.eat import (
    _parse_xml,
    EatIntegrationSource,
    build_order_list_request_xml,
    parse_order_notification_payload,
)


ORDER_LIST_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<responseProcessingResult xmlns="http://agregatoreat.ru/eat/object-types/" Version="1.11.0" RequestUID="req-1">
  <responseOrderList>
    <createDate>2026-05-30T10:00:00</createDate>
    <orders>
      <regNumber>EAT-2026-001</regNumber>
      <startTime>2026-05-30T09:00:00</startTime>
      <expireTime>2026-05-31T11:00:00</expireTime>
      <catalogCategoryRef>
        <name>Многофункциональные устройства</name>
      </catalogCategoryRef>
      <deliveryPlace>
        <region>Республика Крым</region>
        <address>г. Симферополь, ул. Киевская, 1</address>
      </deliveryPlace>
    </orders>
  </responseOrderList>
</responseProcessingResult>
"""


ORDER_NOTIFICATION_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<responseProcessingResult xmlns="http://agregatoreat.ru/eat/object-types/" Version="1.11.0" RequestUID="req-2">
  <responseOrderNotification>
    <OrderNumber>EAT-2026-001</OrderNumber>
    <startDate>2026-05-30T09:00:00</startDate>
    <orderExpireDate>2026-05-31T11:00:00</orderExpireDate>
    <typePurchase>Закупочная сессия</typePurchase>
    <Product>
      <name>Поставка МФУ для офиса</name>
      <requirements>МФУ лазерное, USB, сетевой интерфейс</requirements>
    </Product>
    <Customer>
      <fullName>ГБУ Республики Крым "Заказчик"</fullName>
    </Customer>
    <maxOrderCost>125000.00</maxOrderCost>
    <DeliveryAddress>
      <region>Республика Крым</region>
      <address>г. Симферополь, ул. Киевская, 1</address>
    </DeliveryAddress>
    <DeliveryDate>2026-06-10</DeliveryDate>
    <WWWReference>https://agregatoreat.ru/purchase/EAT-2026-001</WWWReference>
  </responseOrderNotification>
</responseProcessingResult>
"""


def test_parse_xml_respects_declared_encoding_in_bytes() -> None:
    payload = '<?xml version="1.0" encoding="windows-1251"?><a>привет</a>'.encode("cp1251")

    root = _parse_xml(payload)

    assert root.text == "привет"


def test_build_order_list_request_xml_contains_ext_system_and_request_id() -> None:
    xml = build_order_list_request_xml("EXT-CRM", request_uid="fixed-uid")

    assert 'RequestUID="fixed-uid"' in xml
    assert "<extSystem>EXT-CRM</extSystem>" in xml
    assert "<requestOrderList" in xml


def test_parse_order_notification_payload_extracts_tender() -> None:
    tenders = parse_order_notification_payload(ORDER_NOTIFICATION_RESPONSE)

    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.source == "eat-berezka"
    assert tender.tender_number == "EAT-2026-001"
    assert tender.title == "Поставка МФУ для офиса"
    assert tender.customer == 'ГБУ Республики Крым "Заказчик"'
    assert tender.region == "Республика Крым г. Симферополь, ул. Киевская, 1"
    assert tender.price == 125000.0
    assert tender.deadline == datetime(2026, 5, 31, 11, 0)
    assert tender.published_at == datetime(2026, 5, 30, 9, 0)
    assert tender.url == "https://agregatoreat.ru/purchase/EAT-2026-001"
    assert "МФУ лазерное" in tender.raw_text


class Response:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


class EatSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def post(self, url: str, data: str, headers: dict[str, str], timeout: int) -> Response:
        self.calls.append((url, data, headers))
        if url.endswith("/requestOrderList"):
            return Response(ORDER_LIST_RESPONSE)
        if url.endswith("/orderNotification"):
            return Response(ORDER_NOTIFICATION_RESPONSE)
        raise AssertionError(f"unexpected url {url}")


def test_fetch_keywords_uses_eat_token_and_fetches_order_details() -> None:
    session = EatSession()
    source = EatIntegrationSource(
        session=session,
        api_token="secret-token",
        ext_system="EXT-CRM",
        auth_header="Authorization",
        auth_scheme="Bearer",
    )

    tenders = source.fetch_keywords(["МФУ"])

    assert len(tenders) == 1
    assert session.calls[0][0].endswith("/requestOrderList")
    assert session.calls[0][2]["Authorization"] == "Bearer secret-token"
    assert session.calls[1][0].endswith("/orderNotification")
    assert "<OrderNumber>EAT-2026-001</OrderNumber>" in session.calls[1][1]
