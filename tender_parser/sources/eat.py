from __future__ import annotations

import json
import os
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterable

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
    search_text: str | None = None


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


def parse_order_list_payload(payload: str | bytes) -> list[EatOrderReference]:
    json_payload = _try_parse_json(payload)
    if json_payload is not None:
        return _parse_json_order_list(json_payload)

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
                search_text=_joined_text([order_number, category, delivery_place]),
            )
        )
    return references


def parse_order_notification_payload(payload: str | bytes) -> list[TenderRecord]:
    json_payload = _try_parse_json(payload)
    if json_payload is not None:
        return _parse_json_order_notifications(json_payload)

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


def _try_parse_json(payload: str | bytes):
    if isinstance(payload, bytes):
        text = payload.decode("utf-8-sig", errors="replace")
    else:
        text = payload.lstrip("\ufeff")
    if not text.lstrip().startswith(("{", "[")):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_json_order_list(payload) -> list[EatOrderReference]:
    orders: list[dict] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("orders"), list):
            orders.extend(item for item in payload["orders"] if isinstance(item, dict))
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            orders.extend(order for order in item.get("orders") or [] if isinstance(order, dict))

    references: list[EatOrderReference] = []
    for order in orders:
        order_number = _json_text(order.get("regNumber"))
        if not order_number:
            continue
        category_node = order.get("catalogCategoryRef")
        category = _json_first(category_node, ["fullName", "shortName", "name", "code"])
        delivery_places = order.get("deliveryPlace") or []
        if isinstance(delivery_places, dict):
            delivery_places = [delivery_places]
        place_parts = []
        for place in delivery_places:
            if isinstance(place, dict):
                place_parts.append(_json_first(place, ["addressString", "address", "region", "deliveryRegion"]))
        references.append(
            EatOrderReference(
                order_number=order_number,
                deadline=_parse_datetime(_json_text(order.get("expireTime"))),
                category=_json_text(order.get("regName")) or category or None,
                region=_joined_text(place_parts) or None,
                search_text=_joined_text(
                    [order_number, _json_text(order.get("regName")), category, _joined_text(place_parts)]
                ),
            )
        )
    return references


def _parse_json_order_notifications(payload) -> list[TenderRecord]:
    if not isinstance(payload, dict):
        return []
    notifications = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    if payload.get("orderNumber"):
        notifications.append(payload)

    records: list[TenderRecord] = []
    for notification in notifications:
        order_number = _json_text(notification.get("orderNumber"))
        if not order_number:
            continue

        products = notification.get("product") or notification.get("Product") or []
        if isinstance(products, dict):
            products = [products]
        products = [product for product in products if isinstance(product, dict)]
        product_names = [
            _json_first(product, ["name", "productName", "fullName", "shortName"])
            for product in products
        ]
        product_names = [name for name in product_names if name]
        title = product_names[0] if product_names else f"Закупочная сессия ЕАТ {order_number}"

        customer_node = notification.get("customer") or notification.get("Customer") or {}
        customer = _json_first(customer_node, ["customerName", "fullName", "shortName", "name"])
        if not customer and isinstance(customer_node, dict):
            customer = _json_first(customer_node.get("sellerRef"), ["fullName", "shortName", "name"])

        delivery_nodes = notification.get("deliveryAddress") or notification.get("DeliveryAddress") or []
        if isinstance(delivery_nodes, dict):
            delivery_nodes = [delivery_nodes]
        delivery_parts = []
        for address in delivery_nodes:
            if isinstance(address, dict):
                delivery_parts.append(
                    _json_first(address, ["addressString", "address", "fullAddress", "region"])
                )
        delivery_address = _joined_text(delivery_parts)

        requirement_parts = []
        for product in products:
            requirement_parts.extend(
                _json_text(product.get(key))
                for key in ("requirements", "description", "characteristics", "specifications")
            )

        type_purchase = _json_text(notification.get("typePurchase"))
        state = _json_text(notification.get("stateDescription")) or type_purchase or "Закупочная сессия"
        raw_text = _joined_text(
            [title, " ".join(product_names), _joined_text(requirement_parts), customer, delivery_address, state]
        )
        records.append(
            TenderRecord(
                title=title,
                url=_json_text(notification.get("wwwReference"))
                or f"https://agregatoreat.ru/purchase/{order_number}",
                source=EAT_SOURCE_NAME,
                tender_number=order_number,
                customer=customer or None,
                region=delivery_address or None,
                price=_parse_float(_json_text(notification.get("maxOrderCost"))),
                deadline=_parse_datetime(_json_text(notification.get("orderExpireDate"))),
                status=state,
                published_at=_parse_datetime(_json_text(notification.get("startDate"))),
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return records


def _json_first(node, keys: list[str]) -> str:
    if not isinstance(node, dict):
        return _json_text(node)
    for key in keys:
        value = _json_text(node.get(key))
        if value:
            return value
    return ""


def _json_text(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return _joined_text(_json_text(item) for item in value)
    if isinstance(value, dict):
        return _joined_text(_json_text(item) for item in value.values())
    return ""


def _json_processing_finished(payload: str | bytes) -> bool:
    parsed = _try_parse_json(payload)
    return isinstance(parsed, dict) and isinstance(parsed.get("items"), list)


def _json_violation_messages(payload: str | bytes) -> list[str]:
    parsed = _try_parse_json(payload)
    if not isinstance(parsed, dict) or not parsed.get("violations"):
        return []
    messages: list[str] = []
    for violation in parsed["violations"] if isinstance(parsed["violations"], list) else [parsed["violations"]]:
        if isinstance(violation, dict):
            message = _json_first(violation, ["message", "description", "text", "code"])
        else:
            message = _json_text(violation)
        if message:
            messages.append(message)
    return messages


def _rate_limit_delay(
    headers: object,
    *,
    attempt: int,
    backoff_seconds: float,
    max_delay_seconds: float,
) -> float:
    """Retry-After (секунды или HTTP-date), иначе экспоненциальный backoff."""
    retry_after = ""
    if hasattr(headers, "get"):
        retry_after = str(headers.get("Retry-After", "")).strip()  # type: ignore[attr-defined]

    delay: float | None = None
    if retry_after:
        try:
            delay = max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                delay = None

    if delay is None:
        delay = backoff_seconds * (2**attempt)
    return min(delay, max_delay_seconds)


class EatIntegrationSource:
    source_name = EAT_SOURCE_NAME

    def __init__(
        self,
        session: requests.Session | None = None,
        api_token: str | None = None,
        ext_system: str | None = None,
        auth_header: str | None = None,
        auth_scheme: str | None = None,
        max_details: int | None = None,
        poll_attempts: int | None = None,
        poll_delay_seconds: float | None = None,
        rate_limit_retries: int | None = None,
        rate_limit_backoff_seconds: float | None = None,
        rate_limit_max_delay_seconds: float | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,*/*"})
        self.api_token = api_token if api_token is not None else os.getenv("EAT_API_TOKEN", "")
        self.ext_system = ext_system if ext_system is not None else os.getenv("EAT_EXT_SYSTEM", "")
        self.auth_header = auth_header if auth_header is not None else os.getenv("EAT_AUTH_HEADER", "Authorization")
        self.auth_scheme = auth_scheme if auth_scheme is not None else os.getenv("EAT_AUTH_SCHEME", "Bearer")
        self.max_details = max_details if max_details is not None else int(os.getenv("EAT_MAX_DETAILS", "100"))
        self.poll_attempts = poll_attempts if poll_attempts is not None else int(os.getenv("EAT_POLL_ATTEMPTS", "5"))
        self.poll_delay_seconds = (
            poll_delay_seconds
            if poll_delay_seconds is not None
            else float(os.getenv("EAT_POLL_DELAY_SECONDS", "3"))
        )
        self.rate_limit_retries = max(
            0,
            rate_limit_retries
            if rate_limit_retries is not None
            else int(os.getenv("EAT_RATE_LIMIT_RETRIES", "3")),
        )
        self.rate_limit_backoff_seconds = max(
            0.0,
            rate_limit_backoff_seconds
            if rate_limit_backoff_seconds is not None
            else float(os.getenv("EAT_RATE_LIMIT_BACKOFF_SECONDS", "5")),
        )
        self.rate_limit_max_delay_seconds = max(
            0.0,
            rate_limit_max_delay_seconds
            if rate_limit_max_delay_seconds is not None
            else float(os.getenv("EAT_RATE_LIMIT_MAX_DELAY_SECONDS", "60")),
        )
        self._sleep = sleeper or time.sleep

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
            references = self._poll_processing_result(
                request_uid,
                parse_order_list_payload,
            )

        keyword_values = [keyword.strip().casefold() for keyword in keywords if keyword.strip()]
        selected_references = references
        if keyword_values:
            selected_references = [
                reference
                for reference in references
                if any(
                    keyword in (reference.search_text or "").casefold()
                    for keyword in keyword_values
                )
            ]

        records: list[TenderRecord] = []
        detail_errors: list[str] = []
        for reference in selected_references[: self.max_details]:
            try:
                detail_request_uid = str(uuid.uuid4())
                detail_payload = self._post_xml(
                    EAT_ORDER_NOTIFICATION_URL,
                    build_order_notification_request_xml(
                        reference.order_number,
                        self.ext_system,
                        request_uid=detail_request_uid,
                    ),
                )
                detail_records = parse_order_notification_payload(detail_payload)
                if not detail_records:
                    detail_records = self._poll_processing_result(
                        detail_request_uid,
                        parse_order_notification_payload,
                    )
            except SourceFetchError as exc:
                detail_errors.append(str(exc))
                continue
            records.extend(detail_records)

        if selected_references and not records and detail_errors:
            raise SourceFetchError(f"ЕАТ Березка не вернул карточки закупок: {'; '.join(detail_errors[:3])}")
        return records or [_reference_to_record(reference) for reference in selected_references[: self.max_details]]

    def _poll_processing_result(self, request_uid: str, parser):
        for _ in range(max(1, self.poll_attempts)):
            if self.poll_delay_seconds > 0:
                self._sleep(self.poll_delay_seconds)
            processing_payload = self._post_xml(
                EAT_PROCESSING_RESULT_URL,
                build_processing_result_request_xml(self.ext_system, request_uid=request_uid),
            )
            parsed = parser(processing_payload)
            if parsed:
                return parsed
            if _json_processing_finished(processing_payload):
                return parsed
        return []

    def _post_xml(self, url: str, payload: str) -> bytes:
        headers = {"Content-Type": "text/xml; charset=utf-8", **self._auth_headers()}
        response = None
        for attempt in range(self.rate_limit_retries + 1):
            try:
                response = self.session.post(url, data=payload, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
                if response.status_code in {401, 403}:
                    raise SourceFetchError(f"ЕАТ Березка API отклонил доступ: HTTP {response.status_code}")
                if response.status_code == 429:
                    if attempt >= self.rate_limit_retries:
                        raise SourceFetchError(
                            "ЕАТ Березка ограничил частоту запросов: HTTP 429; "
                            "лимит повторов исчерпан, следующий запуск повторит источник"
                        )
                    delay = _rate_limit_delay(
                        getattr(response, "headers", {}),
                        attempt=attempt,
                        backoff_seconds=self.rate_limit_backoff_seconds,
                        max_delay_seconds=self.rate_limit_max_delay_seconds,
                    )
                    self._sleep(delay)
                    continue
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                raise SourceFetchError(f"ЕАТ Березка API недоступен: {exc}") from exc
        if response is None:
            raise SourceFetchError("ЕАТ Березка API не вернул ответ")
        violations = _json_violation_messages(response.content)
        if violations:
            raise SourceFetchError(f"ЕАТ Березка отклонил запрос: {'; '.join(violations[:3])}")
        # Байты, не text: XML-декларация encoding должна решать сама.
        return response.content

    def _auth_headers(self) -> dict[str, str]:
        token_value = self.api_token
        if self.auth_scheme:
            token_value = f"{self.auth_scheme} {self.api_token}"
        return {self.auth_header: token_value}


def _reference_to_record(reference: EatOrderReference) -> TenderRecord:
    title = reference.category or f"Закупочная сессия ЕАТ {reference.order_number}"
    raw_text = _joined_text([reference.search_text, title, reference.region, reference.order_number])
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
