from datetime import datetime
import xml.etree.ElementTree as ET

from tender_parser.sources.eat import (
    _parse_xml,
    EatIntegrationSource,
    build_order_notification_request_xml,
    build_order_list_request_xml,
    build_processing_result_request_xml,
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

    assert 'eat:RequestUID="fixed-uid"' in xml
    assert 'eat:Version="2.0.0"' in xml
    assert "<extSystem>EXT-CRM</extSystem>" in xml
    assert "<obj:requestOrderList" in xml


def test_eat_requests_use_namespaces_required_by_current_xsd() -> None:
    object_ns = "http://agregatoreat.ru/eat/object-types/"
    eat_ns = "http://agregatoreat.ru/eat/"
    request_id = "11111111-1111-1111-1111-111111111111"

    list_root = ET.fromstring(build_order_list_request_xml("123", request_uid=request_id))
    assert list_root.tag == f"{{{object_ns}}}requestOrderList"
    assert list_root.attrib[f"{{{eat_ns}}}Version"] == "2.0.0"
    assert list_root.attrib[f"{{{eat_ns}}}RequestUID"] == request_id
    assert list(list_root)[0].tag == "extSystem"

    detail_root = ET.fromstring(
        build_order_notification_request_xml("EAT-1", "123", request_uid=request_id)
    )
    assert list(detail_root)[0].tag == f"{{{eat_ns}}}OrderNumber"
    assert list(detail_root)[1].tag == "extSystem"

    processing_root = ET.fromstring(build_processing_result_request_xml("123", request_id))
    assert processing_root.tag == f"{{{object_ns}}}requestProcessingResult"
    assert list(processing_root)[0].tag == "extSystem"


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
    assert session.calls[0][2]["Content-Type"] == "text/xml; charset=utf-8"
    assert session.calls[1][0].endswith("/orderNotification")
    assert "<eat:OrderNumber>EAT-2026-001</eat:OrderNumber>" in session.calls[1][1]
