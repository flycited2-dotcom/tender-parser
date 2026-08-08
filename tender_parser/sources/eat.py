from __future__ import annotations

import os
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import requests

from tender_parser.config import HTTP_TIMEOUT_SECONDS
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError


EAT_ORDER_LIST_URL = "https://agregatoreat.ru/integration/ecom/rest/api/order/requestOrderList"
EAT_ORDER_NOTIFICATION_URL = "https://agregatoreat.ru/integration/ecom/rest/api/order/orderNotification"
EAT_PROCESSING_RESULT_URL = "https://agregatoreat.ru/integration/ecom/rest/api/processingResult"
EAT_SCHEMA_VERSION = "2.0.0"
EAT_SOURCE_NAME = "eat-berezka"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.2"


@dataclass(frozen=True)
class EatOrderReference:
    order_number: str
    deadline: datetime | None
    category: str | None
    region: str | None


def build_order_list_request_xml(ext_system: str, request_uid: str | None = None) -> str:
    request_id = request_uid or str(uuid.uuid4())
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<obj:requestOrderList xmlns:obj="http://agregatoreat.ru/eat/object-types/" '
        f'xmlns:eat="http://agregatoreat.ru/eat/" eat:Version="{EAT_SCHEMA_VERSION}" '
        f'eat:RequestUID="{request_id}"><extSystem>{_xml_escape(ext_system)}</extSystem>'
        f"</obj:requestOrderList>"
    )


def build_order_notification_request_xml(
    order_number: str,
    ext_system: str,
    request_uid: str | None = None,
) -> str:
    request_id = request_uid or str(uuid.uuid4())
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<obj:requestOrderNotification xmlns:obj="http://agregatoreat.ru/eat/object-types/" '
        f'xmlns:eat="http://agregatoreat.ru/eat/" eat:Version="{EAT_SCHEMA_VERSION}" '
        f'eat:RequestUID="{request_id}"><eat:OrderNumber>{_xml_escape(order_number)}</eat:OrderNumber>'
        f"<extSystem>{_xml_escape(ext_system)}</extSystem></obj:requestOrderNotification>"
    )


def build_processing_result_request_xml(ext_system: str, request_uid: str) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<obj:requestProcessingResult xmlns:obj="http://agregatoreat.ru/eat/object-types/" '
        f'xmlns:eat="http://agregatoreat.ru/eat/" eat:Version="{EAT_SCHEMA_VERSION}" '
        f'eat:RequestUID="{_xml_escape(request_uid)}"><extSystem>{_xml_escape(ext_system)}</extSystem>'
        f"</obj:requestProcessingResult>"
    )


def parse_order_list_payload(payload: str) -> list[EatOrderReference]:
    root = _parse_xml(payload)
    references: list[EatOrderReference] = []
    for order in _iter_elements(root, "orders"):
        order_number = _child_text(order, "regNumber")
        if not order_number:
            continue
        category = _first_text(order, ["catalogCategoryRef", "name"])
        delivery_place = _child_joined_text(order, "deliveryPlace")
        references.append(
            EatOrderReference(
                order_number=order_number,
                deadline=_parse_datetime(_child_text(order, "expireTime")),
                category=category,
                region=delivery_place or None,
            )
        )
    return references


def parse_order_notification_payload(payload: str) -> list[TenderRecord]:
    root = _parse_xml(payload)
    records: list[TenderRecord] = []
    notifications = list(_iter_elements(root, "responseOrderNotification"))
    if _local_name(root.tag) == "responseOrderNotification":
        notifications.append(root)

    for notification in notifications:
        order_number = _child_text(notification, "OrderNumber")
        products = list(_direct_children(notification, "Product"))
        product_names = [_child_text(product, "name") for product in products]
        product_names = [name for name in product_names if name]
        title = product_names[0] if product_names else f"Закупочная сессия ЕАТ {order_number}".strip()
        if not order_number or not title:
            continue

        customer_node = next(_direct_children(notification, "Customer"), None)
        customer = _first_text(customer_node, ["fullName", "name", "shortName"]) if customer_node is not None else ""
        delivery_address = _child_joined_text(notification, "DeliveryAddress")
        raw_text = _joined_text(
            [
                title,
                " ".join(product_names),
                " ".join(_child_text(product, "requirements") for product in products),
                customer,
                delivery_address,
                _child_text(notification, "typePurchase"),
            ]
        )
        records.append(
            TenderRecord(
                title=title,
                url=_child_text(notification, "WWWReference") or f"https://agregatoreat.ru/purchase/{order_number}",
                source=EAT_SOURCE_NAME,
                tender_number=order_number,
                customer=customer or None,
                region=delivery_address or None,
                price=_parse_float(_child_text(notification, "maxOrderCost")),
                deadline=_parse_datetime(_child_text(notification, "orderExpireDate")),
                status=_child_text(notification, "typePurchase") or "Закупочная сессия",
                published_at=_parse_datetime(_child_text(notification, "startDate")),
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return records


class EatIntegrationSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        api_token: str | None = None,
        ext_system: str | None = None,
        auth_header: str | None = None,
        auth_scheme: str | None = None,
        max_details: int | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,*/*"})
        self.api_token = api_token if api_token is not None else os.getenv("EAT_API_TOKEN", "")
        self.ext_system = ext_system if ext_system is not None else os.getenv("EAT_EXT_SYSTEM", "")
        self.auth_header = auth_header if auth_header is not None else os.getenv("EAT_AUTH_HEADER", "Authorization")
        self.auth_scheme = auth_scheme if auth_scheme is not None else os.getenv("EAT_AUTH_SCHEME", "Bearer")
        self.max_details = max_details if max_details is not None else int(os.getenv("EAT_MAX_DETAILS", "100"))

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        if not self.api_token or not self.ext_system:
            raise SourceFetchError("ЕАТ Березка API не настроен: нужны EAT_API_TOKEN и EAT_EXT_SYSTEM")

        request_uid = str(uuid.uuid4())
        list_payload = self._post_xml(
            EAT_ORDER_LIST_URL,
            build_order_list_request_xml(self.ext_system, request_uid=request_uid),
        )
        references = parse_order_list_payload(list_payload)
        if not references:
            processing_payload = self._post_xml(
                EAT_PROCESSING_RESULT_URL,
                build_processing_result_request_xml(self.ext_system, request_uid=request_uid),
            )
            references = parse_order_list_payload(processing_payload)

        records: list[TenderRecord] = []
        detail_errors: list[str] = []
        for reference in references[: self.max_details]:
            try:
                detail_payload = self._post_xml(
                    EAT_ORDER_NOTIFICATION_URL,
                    build_order_notification_request_xml(reference.order_number, self.ext_system),
                )
            except SourceFetchError as exc:
                detail_errors.append(str(exc))
                continue
            records.extend(parse_order_notification_payload(detail_payload))

        if references and not records and detail_errors:
            raise SourceFetchError(f"ЕАТ Березка не вернул карточки закупок: {'; '.join(detail_errors[:3])}")
        return records or [_reference_to_record(reference) for reference in references[: self.max_details]]

    def _post_xml(self, url: str, payload: str) -> bytes:
        headers = {"Content-Type": "text/xml; charset=utf-8", **self._auth_headers()}
        try:
            response = self.session.post(url, data=payload, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
            if response.status_code in {401, 403}:
                raise SourceFetchError(f"ЕАТ Березка API отклонил доступ: HTTP {response.status_code}")
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SourceFetchError(f"ЕАТ Березка API недоступен: {exc}") from exc
        # Байты, не text: XML-декларация encoding должна решать сама.
        return response.content

    def _auth_headers(self) -> dict[str, str]:
        token_value = self.api_token
        if self.auth_scheme:
            token_value = f"{self.auth_scheme} {self.api_token}"
        return {self.auth_header: token_value}


def _reference_to_record(reference: EatOrderReference) -> TenderRecord:
    title = reference.category or f"Закупочная сессия ЕАТ {reference.order_number}"
    raw_text = _joined_text([title, reference.region, reference.order_number])
    return TenderRecord(
        title=title,
        url=f"https://agregatoreat.ru/purchase/{reference.order_number}",
        source=EAT_SOURCE_NAME,
        tender_number=reference.order_number,
        region=reference.region,
        deadline=reference.deadline,
        status="Закупочная сессия",
        discovered_at=datetime.now(),
        raw_text=raw_text,
    )


def _parse_xml(payload: str | bytes) -> ET.Element:
    try:
        if isinstance(payload, bytes):
            return ET.fromstring(payload)
        return ET.fromstring(payload.encode("utf-8"))
    except ET.ParseError as exc:
        raise SourceFetchError(f"ЕАТ Березка вернул невалидный XML: {exc}") from exc


def _iter_elements(root: ET.Element, name: str) -> Iterable[ET.Element]:
    for element in root.iter():
        if _local_name(element.tag) == name:
            yield element


def _direct_children(root: ET.Element, name: str) -> Iterable[ET.Element]:
    for element in list(root):
        if _local_name(element.tag) == name:
            yield element


def _child_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name and element.text:
            return " ".join(element.text.split())
    return ""


def _child_joined_text(root: ET.Element, name: str) -> str:
    for child in _direct_children(root, name):
        values = [text.strip() for text in child.itertext() if text and text.strip()]
        return " ".join(values)
    return ""


def _first_text(root: ET.Element, names: list[str]) -> str:
    for name in names:
        value = _child_text(root, name)
        if value:
            return value
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _joined_text(parts: Iterable[str | None]) -> str:
    return " ".join(part for part in (part.strip() if part else "" for part in parts) if part)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    if not value:
        return None
    normalized = value.strip().replace(" ", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
